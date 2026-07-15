"""Command-line entry point. Every command returns a non-zero exit code on
error, never turns a failure into a quiet empty success, and never hides a
partial/malformed result — spec section 9.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import typer
from langchain_core.runnables import RunnableConfig

from demand_radar import _warnings  # noqa: F401
from demand_radar.agents.base import AgentRunner
from demand_radar.agents.fake import FakeRunner
from demand_radar.config import ProductConfig, load_product_config_by_name
from demand_radar.graph.build import build_graph, open_checkpointer, run_or_resume
from demand_radar.graph.state import DemandState, RunContext, initial_state
from demand_radar.ingest.jsonl import ingest_jsonl_file
from demand_radar.ingest.rss import ingest_rss_feed
from demand_radar.models import EvidenceItem
from demand_radar.storage.sqlite import DatabaseNotInitializedError, Store

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Evidence-backed opportunity discovery."
)
inspect_app = typer.Typer(no_args_is_help=True, help="Look up one stored record by id.")
app.add_typer(inspect_app, name="inspect")

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


def get_runner(name: str) -> AgentRunner:
    if name == "fake":
        return FakeRunner(provider_name="fake")
    if name in ("claude", "codex"):
        from demand_radar.agents.o7_invoke import O7InvokeRunner

        return O7InvokeRunner(engine=name)
    typer.echo(f"error: unknown runner {name!r}; expected fake, claude, or codex", err=True)
    raise typer.Exit(code=1)


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
    analyst: str = typer.Option("fake", "--analyst", help="fake | claude | codex"),
    critic: str = typer.Option("fake", "--critic", help="fake | claude | codex"),
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

    if "codex" in (analyst, critic):
        # Zone 2 (classify/generate_opportunities/critic_review) feeds
        # untrusted evidence text to whichever engine is selected. Claude's
        # closed-world guarantee is structural (`--tools ""` removes the
        # tool surface entirely) and live-verified in this environment.
        # Codex's is not: `o7 invoke`'s codex path relies on `--sandbox
        # read-only` (denies writes, not network) plus an *unverified*
        # `-c features.shell_tool=false` -- neither has ever been observed
        # against a real codex install (see docs/trust-boundaries.md,
        # 007/docs/o7-invoke.md). Refusing rather than silently accepting
        # untrusted content into an engine whose tool-removal isn't proven
        # -- lift this once a live install + adversarial smoke test confirms
        # the flag actually does what it claims.
        typer.echo(
            "error: codex_unverified_for_untrusted_content -- Codex's closed-world "
            "guarantee (no shell tool) is not verified against a live install and "
            "must not be used for --analyst/--critic, which process untrusted "
            "evidence text. Use --analyst claude --critic fake (or vice versa) "
            "until this is lifted. `demand-radar smoke-agents` may still probe "
            "codex reachability -- it sends no evidence content.",
            err=True,
        )
        raise typer.Exit(code=2)

    product_config = _load_product_or_exit(product)
    store = _open_store_or_exit(db)

    resolved_run_id = run_id or f"run-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    if not store.run_exists(resolved_run_id):
        store.create_run(resolved_run_id, product, since, analyst, critic)
    run_dir = runs_dir / product / resolved_run_id

    try:
        analyst_runner = get_runner(analyst)
        critic_runner = get_runner(critic)
    except typer.Exit:
        store.close()
        raise

    now = datetime.now(UTC)
    _write_task_yaml(run_dir, resolved_run_id, product, since, analyst, critic, now)

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
) -> None:
    import yaml

    run_dir.mkdir(parents=True, exist_ok=True)
    task = {
        "run_id": run_id,
        "product": product,
        "since": since,
        "analyst": analyst,
        "critic": critic,
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
