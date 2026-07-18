"""Command-line entry point. Every command returns a non-zero exit code on
error, never turns a failure into a quiet empty success, and never hides a
partial/malformed result — spec section 9.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import typer
from langchain_core.runnables import RunnableConfig

from demand_radar import _warnings  # noqa: F401
from demand_radar.agents.base import (
    READ_ONLY_DATA_PROFILE,
    AgentRunner,
    NeverCalledRunner,
    sha256_file,
)
from demand_radar.agents.fake import FakeRunner
from demand_radar.config import ProductConfig, load_product_config_by_name
from demand_radar.graph.build import build_graph, open_checkpointer, run_or_resume
from demand_radar.graph.state import DemandState, RunContext, initial_state
from demand_radar.ingest.jsonl import ingest_jsonl_file
from demand_radar.ingest.rss import ingest_rss_feed
from demand_radar.models import EvidenceItem
from demand_radar.review import (
    FinalizeError,
    ReviewError,
    export_review_packet,
    finalize_run,
    generate_fixtures,
    import_reviews,
)
from demand_radar.storage.sqlite import DatabaseNotInitializedError, Store

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Evidence-backed opportunity discovery."
)
inspect_app = typer.Typer(no_args_is_help=True, help="Look up one stored record by id.")
app.add_typer(inspect_app, name="inspect")
review_app = typer.Typer(no_args_is_help=True, help="Export/import a deferred human review packet.")
app.add_typer(review_app, name="review")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path("data/demand.db")
DEFAULT_RUNS_DIR = Path("runs")
SCHEMAS_DIR = REPO_ROOT / "schemas"
PRODUCTS_DIR = REPO_ROOT / "products"

DB_OPTION = typer.Option(DEFAULT_DB, "--db", help="Path to the SQLite evidence store.")
RUNS_DIR_OPTION = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir")
INPUT_OPTION = typer.Option(..., "--input", exists=True, dir_okay=False)
RUN_ID_OPTION = typer.Option(
    None, "--run-id", help="Resume this run_id if it exists; otherwise start it fresh."
)
RUN_OPTION = typer.Option(..., "--run")


def _open_store_or_exit(db: Path) -> Store:
    try:
        return Store.open(db)
    except DatabaseNotInitializedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _load_product_or_exit(product: str) -> ProductConfig:
    try:
        return load_product_config_by_name(product, products_dir=PRODUCTS_DIR)
    except FileNotFoundError as exc:
        typer.echo(f"error: unknown product {product!r}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _make_o7_runner(engine: str) -> AgentRunner:
    # Lazy import kept inside this small named function (rather than at
    # module scope, and rather than inline in a lambda in RUNNER_FACTORIES)
    # specifically so offline paths that never touch a real runner --
    # `init`, `ingest`, `report`, `verify`, `--analyst fake --critic fake`
    # runs -- never import o7_invoke.py (and its subprocess-adjacent
    # module-level constants) at all.
    from demand_radar.agents.o7_invoke import O7InvokeRunner

    return O7InvokeRunner(engine=engine)


# One entry per known agent *transport* (how a runner actually reaches
# claude/codex), keyed by the --runner CLI value -- currently just "o7"
# (O7InvokeRunner, shelling out to `o7 invoke`). Kept as a registry rather
# than an if/elif chain so a second transport can be added by adding one
# entry here, not by editing get_runner's control flow.
RUNNER_FACTORIES: dict[str, Callable[[str], AgentRunner]] = {"o7": _make_o7_runner}


def get_runner(engine: str, runner: str) -> AgentRunner:
    """`engine` (fake | claude | codex) selects which model backend to use;
    `runner` (currently only "o7") selects the transport that reaches it --
    two independent axes, spec section 13's runner/engine split."""
    if engine == "fake":
        # FakeRunner is itself the transport -- an in-process stand-in that
        # never shells out to anything -- so which --runner was requested is
        # moot for it; "fake" always ignores the runner axis rather than
        # erroring on an unrecognized one, since the whole point of --analyst
        # fake/--critic fake is to work identically regardless of what
        # transport a real run would have used.
        return FakeRunner(provider_name="fake")
    if engine in ("claude", "codex"):
        factory = RUNNER_FACTORIES.get(runner)
        if factory is None:
            typer.echo(
                f"error: unknown runner {runner!r}; expected one of: "
                f"{', '.join(sorted(RUNNER_FACTORIES))}",
                err=True,
            )
            raise typer.Exit(code=1)
        return factory(engine)
    typer.echo(
        f"error: unknown engine {engine!r}; expected fake, claude, or codex "
        f"(runner transport was {runner!r})",
        err=True,
    )
    raise typer.Exit(code=1)


def _refuse_unverified_profiles(
    checks: Sequence[tuple[str, str, AgentRunner]], *, runner: str
) -> None:
    """spec section 17/Zone 2: refuse to route untrusted evidence text
    through any constructed runner whose verified_profiles() doesn't
    provably cover read-only-data -- see
    agents/base.py::AgentRunner.verified_profiles and
    docs/trust-boundaries.md. Replaces the old codex-specific hardcoded
    `if "codex" in (analyst, critic)` check: the refusal now falls out of
    what the constructed runner can actually prove rather than a hardcoded
    engine-name comparison, so a future verified runner (or a newly
    unverified one) doesn't need a code change here, only an honest
    verified_profiles() implementation.

    `checks` is (role, engine_name, runner) triples -- role is "analyst" or
    "critic", purely for the error message.
    """
    for role, engine_name, r in checks:
        if READ_ONLY_DATA_PROFILE not in r.verified_profiles():
            typer.echo(
                f"error: unverified_capability_profile -- {role} engine {engine_name!r} "
                f"via runner {runner!r} cannot provably enforce the 'read-only-data' "
                "profile required for untrusted evidence text; see "
                "docs/trust-boundaries.md and docs/runner-contract.md. "
                "`demand-radar smoke-agents` may still probe codex reachability -- it "
                "sends no evidence content.",
                err=True,
            )
            raise typer.Exit(code=2)


@app.command()
def init(db: Path = DB_OPTION) -> None:
    """Create a new evidence store (idempotent: safe to re-run on an existing one)."""
    Store.init(db)
    typer.echo(f"initialized database at {db}")


@app.command()
def ingest(
    product: str = typer.Option(..., "--product"),
    input_path: Path = INPUT_OPTION,
    db: Path = DB_OPTION,
) -> None:
    """Ingest a generic JSONL export. One malformed line is reported, never
    hidden, and never aborts the rest of the batch."""
    _load_product_or_exit(product)
    store = _open_store_or_exit(db)
    result = ingest_jsonl_file(input_path, product=product, store=store)
    store.close()

    typer.echo(
        f"accepted={len(result.accepted)} inserted={len(result.inserted_ids)} "
        f"already_present={len(result.already_present_ids)} errors={len(result.errors)}"
    )
    for e in result.errors:
        typer.echo(f"  line {e.line_number}: {e.message}", err=True)
    if result.errors:
        raise typer.Exit(code=1)


@app.command("ingest-rss")
def ingest_rss(
    product: str = typer.Option(..., "--product"),
    url: str = typer.Option(..., "--url"),
    query_id: str = typer.Option(
        ..., "--query-id", help="collection.query_id to record for every entry"
    ),
    source_family: str = typer.Option("rss", "--source-family"),
    db: Path = DB_OPTION,
) -> None:
    """Ingest one RSS/Atom feed. Requires network access -- never run in offline CI."""
    _load_product_or_exit(product)
    store = _open_store_or_exit(db)
    try:
        result = ingest_rss_feed(
            url, product=product, store=store, query_id=query_id, source_family=source_family
        )
    except OSError as exc:
        typer.echo(f"error: could not fetch feed: {exc}", err=True)
        store.close()
        raise typer.Exit(code=1) from exc
    store.close()

    typer.echo(
        f"accepted={len(result.accepted)} inserted={len(result.inserted_ids)} "
        f"already_present={len(result.already_present_ids)} errors={len(result.errors)}"
    )
    for e in result.errors:
        typer.echo(f"  entry {e.line_number}: {e.message}", err=True)
    if result.errors:
        raise typer.Exit(code=1)


_VERDICT_EXIT_CODE = {"PASS": 0, "FAIL": 1, "BLOCKED": 2}


@app.command()
def run(
    product: str = typer.Option(..., "--product"),
    since: str = typer.Option(None, "--since", help="e.g. 30d, 12h, 2w. Omit for all time."),
    analyst: str = typer.Option(
        "fake", "--analyst", help="agent engine (not transport): fake | claude | codex"
    ),
    critic: str = typer.Option(
        "fake", "--critic", help="agent engine (not transport): fake | claude | codex | human"
    ),
    runner: str = typer.Option(
        "o7",
        "--runner",
        help="agent transport reaching --analyst/--critic's engine (irrelevant when the "
        "engine is 'fake', which is its own transport): " + ", ".join(sorted(RUNNER_FACTORIES)),
    ),
    db: Path = DB_OPTION,
    runs_dir: Path = RUNS_DIR_OPTION,
    run_id: str = RUN_ID_OPTION,
) -> None:
    """Run the pipeline: normalize -> dedup -> classify -> cluster -> score ->
    generate opportunities -> critique -> judge -> report -> verify."""
    if analyst == critic and analyst != "fake":
        # spec section 29's explicitly forbidden shortcut: silently letting one
        # provider stand in for both analyst and critic isn't independent
        # critique, so this refuses up front rather than producing a verdict
        # that looks reviewed but isn't (section 24: single_provider_unreviewed
        # must never pass the final gate).
        typer.echo(
            "error: single_provider_unreviewed -- --analyst and --critic must be "
            "different providers for a real run (fake+fake is fine for testing). "
            "Refusing to treat the same model reviewing its own output as an "
            "independent critique.",
            err=True,
        )
        raise typer.Exit(code=2)

    # Runner construction happens here, before product/store setup below --
    # it has no store dependency, and doing it first means a refusal (unknown
    # engine/runner, or the unverified-capability-profile check right after)
    # never has to open and then close a store it never used. "human" is
    # deliberately not a get_runner() branch: it is only ever legal for
    # --critic (an --analyst human falls through to get_runner's "unknown
    # engine" refusal, which is correct -- the analyst role always needs a
    # real generation call). critic_review's own human-mode skip
    # (graph/nodes/agents.py) never calls this runner; NeverCalledRunner
    # exists so a future bug that broke that skip would crash loudly instead
    # of silently faking a verdict -- and, per its verified_profiles(),
    # trivially clears the capability-profile check below too, since it can
    # never route anything anywhere.
    analyst_runner = get_runner(analyst, runner)
    critic_runner = NeverCalledRunner() if critic == "human" else get_runner(critic, runner)

    # Replaces the old hardcoded `if "codex" in (analyst, critic)` check:
    # the refusal is now keyed off what each constructed runner can actually
    # prove about itself (see agents/base.py::AgentRunner.verified_profiles),
    # not a hardcoded engine name. Zone 2 (classify/generate_opportunities/
    # critic_review) feeds untrusted evidence text to whichever engine is
    # selected, so both roles must clear this before anything else runs.
    _refuse_unverified_profiles(
        [("analyst", analyst, analyst_runner), ("critic", critic, critic_runner)], runner=runner
    )

    product_config = _load_product_or_exit(product)
    store = _open_store_or_exit(db)

    resolved_run_id = run_id or f"run-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    if not store.run_exists(resolved_run_id):
        store.create_run(resolved_run_id, product, since, analyst, critic)
    run_dir = runs_dir / product / resolved_run_id

    now = datetime.now(UTC)
    _write_task_yaml(run_dir, resolved_run_id, product, since, analyst, critic, now, runner=runner)

    ctx = RunContext(
        store=store,
        analyst_runner=analyst_runner,
        critic_runner=critic_runner,
        analyst_name=analyst,
        critic_name=critic,
        product=product_config,
        run_dir=run_dir,
        schemas_dir=SCHEMAS_DIR,
        now=now,
    )

    with open_checkpointer(run_dir / "checkpoints.sqlite") as checkpointer:
        compiled = build_graph(checkpointer)
        config: RunnableConfig = {"configurable": {"thread_id": resolved_run_id, "ctx": ctx}}
        final_state = run_or_resume(
            compiled,
            initial_state=initial_state(run_id=resolved_run_id, product=product, since=since),
            config=config,
        )

    _write_final_artifacts(run_dir, store, final_state)
    store.close()

    verdict = final_state.get("verdict") or "FAIL"
    typer.echo(f"run_id={resolved_run_id} verdict={verdict}")
    typer.echo(f"report: {run_dir / 'outputs' / 'report.md'}")
    if critic == "human" and verdict == "BLOCKED":
        typer.echo(
            "blocked reason: human review pending, not a schema failure -- run "
            f"`demand-radar review export --run {resolved_run_id} --db {db} "
            f"--runs-dir {runs_dir} --output <dir>` next"
        )
    if final_state["errors"]:
        typer.echo(f"{len(final_state['errors'])} error(s) recorded during the run:", err=True)
        for e in final_state["errors"]:
            typer.echo(f"  [{e['node']}] {e['message']}", err=True)
    raise typer.Exit(code=_VERDICT_EXIT_CODE.get(verdict, 1))


def _write_task_yaml(
    run_dir: Path,
    run_id: str,
    product: str,
    since: str | None,
    analyst: str,
    critic: str,
    now: datetime,
    *,
    runner: str = "o7",
) -> None:
    import yaml

    run_dir.mkdir(parents=True, exist_ok=True)
    task = {
        "run_id": run_id,
        "product": product,
        "since": since,
        "analyst": analyst,
        "critic": critic,
        # Recorded alongside analyst/critic (which engine) for the same
        # provenance reasons -- which *transport* reached that engine on
        # this run. Defaulted rather than required so existing callers that
        # write task.yaml directly (test fixtures predating --runner) don't
        # need updating; task.yaml has no schema/additionalProperties gate,
        # so this key is purely additive -- see review.py's task.yaml
        # readers, all of which use dict.get()/generic key access.
        "runner": runner,
        "started_at": now.isoformat(),
    }
    (run_dir / "task.yaml").write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")


def _write_final_artifacts(run_dir: Path, store: Store, final_state: DemandState) -> None:
    (run_dir / "state.json").write_text(
        json.dumps(final_state, indent=2, sort_keys=True), encoding="utf-8"
    )

    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    accepted = [store.get_evidence_item(eid) for eid in final_state["accepted_evidence_ids"]]
    _write_evidence_jsonl(evidence_dir / "accepted.jsonl", [e for e in accepted if e])
    duplicates = [store.get_evidence_item(eid) for eid in final_state["duplicate_evidence_ids"]]
    _write_evidence_jsonl(evidence_dir / "duplicates.jsonl", [e for e in duplicates if e])


def _write_evidence_jsonl(path: Path, items: list[EvidenceItem]) -> None:
    lines = [item.model_dump_json(by_alias=True, exclude_none=True) for item in items]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


@app.command()
def report(
    run_id: str = RUN_OPTION,
    fmt: str = typer.Option("markdown", "--format", help="markdown | json"),
    runs_dir: Path = RUNS_DIR_OPTION,
) -> None:
    """Print the report for an already-completed run. Never calls a model."""
    run_dir = _find_run_dir(runs_dir, run_id)
    if run_dir is None:
        typer.echo(f"error: run {run_id!r} not found under {runs_dir}", err=True)
        raise typer.Exit(code=1)

    path = run_dir / "outputs" / ("report.md" if fmt == "markdown" else "opportunities.json")
    if not path.is_file():
        typer.echo(
            f"error: {path} does not exist -- was the run interrupted before render_report?",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(path.read_text(encoding="utf-8"))


@app.command()
def verify(
    run_id: str = RUN_OPTION,
    runs_dir: Path = RUNS_DIR_OPTION,
) -> None:
    """Print verification.json for a run and exit non-zero unless verdict is PASS."""
    run_dir = _find_run_dir(runs_dir, run_id)
    if run_dir is None:
        typer.echo(f"error: run {run_id!r} not found under {runs_dir}", err=True)
        raise typer.Exit(code=1)
    path = run_dir / "verification.json"
    if not path.is_file():
        typer.echo(f"error: {path} does not exist -- the run never reached verify_run", err=True)
        raise typer.Exit(code=1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    typer.echo(json.dumps(payload, indent=2))
    raise typer.Exit(code=_VERDICT_EXIT_CODE.get(payload.get("verdict"), 1))


def _find_run_dir(runs_dir: Path, run_id: str) -> Path | None:
    if not runs_dir.is_dir():
        return None
    for product_dir in runs_dir.iterdir():
        candidate = product_dir / run_id
        if candidate.is_dir():
            return candidate
    return None


OUTPUT_OPTION = typer.Option(..., "--output", help="Directory to write the review packet into.")
REVIEW_INPUT_OPTION = typer.Option(..., "--input", exists=True, dir_okay=False)


@review_app.command("export")
def review_export(
    run_id: str = RUN_OPTION,
    db: Path = DB_OPTION,
    runs_dir: Path = RUNS_DIR_OPTION,
    output: Path = OUTPUT_OPTION,
) -> None:
    """Export a review packet (one dir per opportunity, packet-manifest.json,
    review-guide.md) for an already-completed --critic human run."""
    run_dir = _find_run_dir(runs_dir, run_id)
    if run_dir is None:
        typer.echo(f"error: run {run_id!r} not found under {runs_dir}", err=True)
        raise typer.Exit(code=1)
    run_row = None
    store = _open_store_or_exit(db)
    try:
        run_row = store.get_run(run_id)
        if run_row is None:
            typer.echo(f"error: run {run_id!r} not found in {db}", err=True)
            raise typer.Exit(code=1)
        result = export_review_packet(
            store=store,
            run_dir=run_dir,
            run_id=run_id,
            product=str(run_row["product"]),
            output_dir=output,
            generated_at=datetime.now(UTC),
        )
    except ReviewError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    typer.echo(f"review packet written to {result.output_dir}")
    typer.echo(f"opportunities: {len(result.opportunity_ids)}")
    for opp_id in result.opportunity_ids:
        typer.echo(f"  {opp_id}")
    typer.echo(
        f"packet-manifest.json sha256: {sha256_file(result.output_dir / 'packet-manifest.json')}"
    )


PACKET_OPTION = typer.Option(
    ..., "--packet", exists=True, file_okay=False, help="Path to an exported review packet."
)
FIXTURE_OUTPUT_OPTION = typer.Option(..., "--output", help="Path to write the fixture JSONL to.")


@review_app.command("generate-fixtures")
def review_generate_fixtures(
    run_id: str = RUN_OPTION,
    packet: Path = PACKET_OPTION,
    output: Path = FIXTURE_OUTPUT_OPTION,
) -> None:
    """TEST FIXTURE ONLY -- generates one synthetic ReviewFixtureEnvelope per
    opportunity in an exported review packet, for testing the export ->
    import -> finalize pipeline's mechanics. Never calls an agent, never
    reads evidence content, and is never a substantive review: every
    generated verdict carries a fatal objection and recommends
    `investigate`, never `experiment_ready`. Requires `review import
    --allow-test-fixture` to import; a real ReviewEnvelope never needs or
    accepts that flag."""
    try:
        result = generate_fixtures(
            run_id=run_id, packet_dir=packet, output_path=output, generated_at=datetime.now(UTC)
        )
    except ReviewError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("TEST FIXTURE ONLY -- synthetic pipeline-mechanics fixtures, not a review")
    typer.echo(f"generated {len(result.fixtures)} fixture(s) at {result.output_path}")
    for fixture in result.fixtures:
        typer.echo(f"  {fixture.opportunity_id}")


ALLOW_TEST_FIXTURE_OPTION = typer.Option(
    False,
    "--allow-test-fixture",
    help="Required to import synthetic ReviewFixtureEnvelope records "
    "(schemas/review-fixture-envelope.schema.json). A real human-authored "
    "ReviewEnvelope batch never needs or accepts this flag.",
)


@review_app.command("import")
def review_import(
    run_id: str = RUN_OPTION,
    input_path: Path = REVIEW_INPUT_OPTION,
    db: Path = DB_OPTION,
    runs_dir: Path = RUNS_DIR_OPTION,
    allow_test_fixture: bool = ALLOW_TEST_FIXTURE_OPTION,
) -> None:
    """Atomically import a batch of completed review envelopes (JSONL, one
    ReviewEnvelope per line). Any invalid envelope rejects the whole batch
    with zero mutation to the store or run_dir."""
    run_dir = _find_run_dir(runs_dir, run_id)
    if run_dir is None:
        typer.echo(f"error: run {run_id!r} not found under {runs_dir}", err=True)
        raise typer.Exit(code=1)
    store = _open_store_or_exit(db)
    try:
        result = import_reviews(
            store=store,
            run_dir=run_dir,
            run_id=run_id,
            input_path=input_path,
            imported_at=datetime.now(UTC),
            allow_test_fixture=allow_test_fixture,
        )
    except ReviewError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        store.close()

    if result.contains_test_fixture:
        typer.echo("TEST FIXTURE ONLY -- mechanics validated, no substantive review performed")
    typer.echo(f"imported {len(result.imported_opportunity_ids)} review(s):")
    for opp_id in result.imported_opportunity_ids:
        typer.echo(f"  {opp_id}")
    typer.echo(f"imported file archived at {result.imported_file_path}")
    typer.echo(f"imported file sha256: {result.imported_file_sha256}")


@app.command()
def finalize(
    run_id: str = RUN_OPTION,
    db: Path = DB_OPTION,
    runs_dir: Path = RUNS_DIR_OPTION,
) -> None:
    """Re-runs deterministic_judge -> render_report -> verify_run against the
    store's current state (after `review import`). Never calls an agent,
    never reclassifies evidence, never regenerates opportunities."""
    run_dir = _find_run_dir(runs_dir, run_id)
    if run_dir is None:
        typer.echo(f"error: run {run_id!r} not found under {runs_dir}", err=True)
        raise typer.Exit(code=1)
    store = _open_store_or_exit(db)
    run_row = store.get_run(run_id)
    if run_row is None:
        store.close()
        typer.echo(f"error: run {run_id!r} not found in {db}", err=True)
        raise typer.Exit(code=1)
    product_config = _load_product_or_exit(str(run_row["product"]))

    try:
        final_state = finalize_run(
            store=store,
            run_dir=run_dir,
            schemas_dir=SCHEMAS_DIR,
            product=product_config,
            run_id=run_id,
            now=datetime.now(UTC),
        )
    except FinalizeError as exc:
        store.close()
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    store.close()

    verdict = final_state.get("verdict") or "FAIL"
    typer.echo(f"run_id={run_id} verdict={verdict}")
    typer.echo(f"report: {run_dir / 'outputs' / 'report.md'}")
    raise typer.Exit(code=_VERDICT_EXIT_CODE.get(verdict, 1))


@inspect_app.command("evidence")
def inspect_evidence(evidence_id: str, db: Path = DB_OPTION) -> None:
    store = _open_store_or_exit(db)
    item = store.get_evidence_item(evidence_id)
    store.close()
    if item is None:
        typer.echo(f"error: no evidence item {evidence_id!r}", err=True)
        raise typer.Exit(code=1)
    typer.echo(item.model_dump_json(by_alias=True, exclude_none=True, indent=2))


@inspect_app.command("cluster")
def inspect_cluster(cluster_id: str, db: Path = DB_OPTION) -> None:
    store = _open_store_or_exit(db)
    cluster = store.get_problem_cluster(cluster_id)
    store.close()
    if cluster is None:
        typer.echo(f"error: no cluster {cluster_id!r}", err=True)
        raise typer.Exit(code=1)
    typer.echo(cluster.model_dump_json(by_alias=True, exclude_none=True, indent=2))


@inspect_app.command("opportunity")
def inspect_opportunity(opportunity_id: str, db: Path = DB_OPTION) -> None:
    store = _open_store_or_exit(db)
    card = store.get_opportunity_card(opportunity_id)
    verdict = store.get_critic_verdict(opportunity_id)
    store.close()
    if card is None:
        typer.echo(f"error: no opportunity {opportunity_id!r}", err=True)
        raise typer.Exit(code=1)
    typer.echo(card.model_dump_json(by_alias=True, exclude_none=True, indent=2))
    if verdict is not None:
        typer.echo("--- critic verdict ---")
        typer.echo(verdict.model_dump_json(by_alias=True, exclude_none=True, indent=2))


@app.command("smoke-agents")
def smoke_agents(
    runs_dir: Path = RUNS_DIR_OPTION,
) -> None:
    """Real, minimal, schema-bound authenticated smoke test for claude and
    codex -- spec section 24. Never runs in CI; never substitutes one
    provider's result for the other's."""
    from demand_radar.smoke import run_smoke_agents

    results = run_smoke_agents(runs_dir=runs_dir, schemas_dir=SCHEMAS_DIR)
    exit_code = 0
    for provider, outcome in results.items():
        typer.echo(
            f"{provider}: {outcome.status}" + (f" -- {outcome.detail}" if outcome.detail else "")
        )
        if outcome.status != "PASS":
            exit_code = 1
    raise typer.Exit(code=exit_code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
