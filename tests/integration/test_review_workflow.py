"""Integration tests for the Phase 2C human review workflow: --critic
human, review export/import, and finalize -- run through the real
LangGraph pipeline (not mocked), reusing the same
fixture_fake_outputs.py canned analyst scenarios as
test_fixture_pipeline.py, with the critic step deferred to a human instead
of a live/fake critic call.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from demand_radar.agents.base import AgentRunner, NeverCalledRunner
from demand_radar.agents.fake import FakeRunner
from demand_radar.cli import _write_final_artifacts, _write_task_yaml, app
from demand_radar.config import load_product_config_by_name
from demand_radar.graph.build import build_graph, open_checkpointer, run_or_resume
from demand_radar.graph.nodes.clustering import _cluster_id_for
from demand_radar.graph.state import DemandState, RunContext, initial_state
from demand_radar.ingest.jsonl import ingest_jsonl_file
from demand_radar.review import (
    FinalizeError,
    ReviewValidationError,
    export_review_packet,
    finalize_run,
    import_reviews,
)
from demand_radar.storage.sqlite import Store
from tests.integration import fixture_fake_outputs as ffo

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCT = "own-audit"
FIXTURE = REPO_ROOT / "fixtures" / "mixed-demand-signals.jsonl"

runner = CliRunner()


def _cluster_ids_by_key() -> dict[str, str]:
    return {
        ffo.STRONG_PROBLEM_KEY: _cluster_id_for([ffo.STRONG_PROBLEM_KEY]),
        ffo.WEAK_PROBLEM_KEY: _cluster_id_for([ffo.WEAK_PROBLEM_KEY]),
        ffo.VIRAL_PROBLEM_KEY: _cluster_id_for([ffo.VIRAL_PROBLEM_KEY]),
        ffo.EDUCATIONAL_PROBLEM_KEY: _cluster_id_for([ffo.EDUCATIONAL_PROBLEM_KEY]),
    }


def _opportunity_id(cluster_id: str) -> str:
    return f"opp_{cluster_id.removeprefix('cluster_')}"


def run_human_review_pipeline(
    tmp_path: Path, run_id: str = "run-human-e2e"
) -> tuple[Store, Path, DemandState]:
    """Same shape as test_fixture_pipeline.py::run_fixture_pipeline, but
    critic="human": the analyst gets real canned scenarios (classify +
    generate_opportunities), the critic role is a NeverCalledRunner since
    critic_review must skip it entirely in this mode. run_dir is laid out
    the same way cli.py's run() lays it out (runs_dir/product/run_id), and
    task.yaml/state.json are written via the same private helpers cli.py
    uses, since finalize_run() and review export's provenance reader both
    read those files back from run_dir.
    """
    db_path = tmp_path / "demand.db"
    run_dir = tmp_path / "runs" / PRODUCT / run_id
    checkpoint_path = tmp_path / "checkpoints.sqlite"

    store = Store.init(db_path)
    ingest_jsonl_file(FIXTURE, product=PRODUCT, store=store)
    store.create_run(run_id, PRODUCT, since=None, analyst="fake", critic="human")

    cluster_id_for_key = _cluster_ids_by_key()
    analyst_scenarios = dict(ffo.classify_scenarios())
    analyst_scenarios.update(ffo.opportunity_scenarios_by_cluster_id(cluster_id_for_key))
    analyst_runner = FakeRunner(provider_name="fake-analyst", scenarios=analyst_scenarios)
    critic_runner: AgentRunner = NeverCalledRunner()

    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    now = datetime.now(UTC)
    _write_task_yaml(run_dir, run_id, PRODUCT, None, "fake", "human", now)

    ctx = RunContext(
        store=store,
        analyst_runner=analyst_runner,
        critic_runner=critic_runner,
        analyst_name="fake",
        critic_name="human",
        product=product_config,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        now=now,
    )

    with open_checkpointer(checkpoint_path) as checkpointer:
        compiled = build_graph(checkpointer)
        config = {"configurable": {"thread_id": run_id, "ctx": ctx}}
        final_state = run_or_resume(
            compiled,
            initial_state=initial_state(run_id=run_id, product=PRODUCT, since=None),
            config=config,
        )

    _write_final_artifacts(run_dir, store, final_state)

    return store, run_dir, final_state


# --- --critic human mode itself --------------------------------------------------


def test_critic_human_mode_produces_blocked_verdict_with_zero_agent_calls(tmp_path: Path) -> None:
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    assert final_state["verdict"] == "BLOCKED"
    verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
    assert verification["checks"]["critic_status"] == "NOT_RUN"
    assert verification["checks"]["analyst_status"] == "PASS"
    assert not (run_dir / "agents" / "critic").exists()  # no critic call artifacts at all
    store.close()


def test_critic_human_mode_every_opportunity_lands_on_investigate(tmp_path: Path) -> None:
    store, _run_dir, final_state = run_human_review_pipeline(tmp_path)
    cards = store.list_opportunity_cards_for_run(final_state["run_id"])
    assert len(cards) == 4
    for card in cards:
        assert card.status == "investigate"
        assert card.rejection_reasons == ["critic review not completed"]
    store.close()


# --- review export -----------------------------------------------------------


def test_review_export_writes_expected_files_and_hashes(tmp_path: Path) -> None:
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    output_dir = tmp_path / "packet"
    result = export_review_packet(
        store=store,
        run_dir=run_dir,
        run_id=final_state["run_id"],
        product=PRODUCT,
        output_dir=output_dir,
        generated_at=datetime.now(UTC),
    )
    assert len(result.opportunity_ids) == 4
    for opp_id in result.opportunity_ids:
        card_dir = output_dir / opp_id
        assert (card_dir / "opportunity.json").is_file()
        assert (card_dir / "evidence.jsonl").is_file()
        template = json.loads((card_dir / "review-template.json").read_text(encoding="utf-8"))
        assert template["reviewer"] == {"kind": "human", "id": "", "conflict": ""}
        assert template["attestation"] == {
            "reviewed_primary_evidence": False,
            "review_not_generated_by_analyst_provider": False,
        }
        assert template["opportunity_hash"] == result.packet_manifest["opportunity_hashes"][opp_id]
    manifest = json.loads((output_dir / "packet-manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == final_state["run_id"]
    assert set(manifest["opportunity_hashes"]) == set(result.opportunity_ids)
    assert set(manifest["evidence_manifest_hashes"]) == set(result.opportunity_ids)
    assert (output_dir / "review-guide.md").is_file()
    assert "reasons to reject" in (output_dir / "review-guide.md").read_text(encoding="utf-8")
    store.close()


def test_review_export_refuses_a_run_with_no_opportunities(tmp_path: Path) -> None:
    db_path = tmp_path / "demand.db"
    store = Store.init(db_path)
    store.create_run("empty-run", PRODUCT, since=None, analyst="fake", critic="human")
    with pytest.raises(Exception, match="no opportunities"):
        export_review_packet(
            store=store,
            run_dir=tmp_path / "run",
            run_id="empty-run",
            product=PRODUCT,
            output_dir=tmp_path / "packet",
            generated_at=datetime.now(UTC),
        )
    store.close()


# --- review import -------------------------------------------------------------


def _complete_envelope(
    template: dict[str, Any], *, recommended_status: str, objections: list | None = None
) -> dict[str, Any]:
    filled = copy.deepcopy(template)
    filled["reviewer"] = {"kind": "human", "id": "reviewer-1", "conflict": "none"}
    filled["reviewed_at"] = datetime.now(UTC).isoformat()
    filled["attestation"] = {
        "reviewed_primary_evidence": True,
        "review_not_generated_by_analyst_provider": True,
    }
    filled["verdict"]["recommended_status"] = recommended_status
    filled["verdict"]["objections"] = objections or []
    return filled


def _export(store: Store, run_dir: Path, run_id: str, output_dir: Path):
    return export_review_packet(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        product=PRODUCT,
        output_dir=output_dir,
        generated_at=datetime.now(UTC),
    )


def _write_batch(path: Path, envelopes: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in envelopes) + "\n", encoding="utf-8")


def test_review_import_accepts_a_full_valid_batch(tmp_path: Path) -> None:
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    run_id = final_state["run_id"]
    output_dir = tmp_path / "packet"
    packet = _export(store, run_dir, run_id, output_dir)

    envelopes = [
        _complete_envelope(
            json.loads((output_dir / opp_id / "review-template.json").read_text(encoding="utf-8")),
            recommended_status="investigate",
        )
        for opp_id in packet.opportunity_ids
    ]
    input_path = tmp_path / "completed-reviews.jsonl"
    _write_batch(input_path, envelopes)

    result = import_reviews(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        input_path=input_path,
        imported_at=datetime.now(UTC),
    )
    assert set(result.imported_opportunity_ids) == set(packet.opportunity_ids)
    for opp_id in packet.opportunity_ids:
        assert store.get_critic_verdict(opp_id) is not None
    assert result.imported_file_path.is_file()
    provenance_lines = (
        (run_dir / "reviews" / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert len(provenance_lines) == 4
    store.close()


def test_review_import_rejects_stale_hash_and_writes_nothing(tmp_path: Path) -> None:
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    run_id = final_state["run_id"]
    output_dir = tmp_path / "packet"
    packet = _export(store, run_dir, run_id, output_dir)

    envelopes = [
        _complete_envelope(
            json.loads((output_dir / opp_id / "review-template.json").read_text(encoding="utf-8")),
            recommended_status="investigate",
        )
        for opp_id in packet.opportunity_ids
    ]
    envelopes[0]["opportunity_hash"] = f"sha256:{'0' * 64}"  # tamper with one
    input_path = tmp_path / "completed-reviews.jsonl"
    _write_batch(input_path, envelopes)

    with pytest.raises(ReviewValidationError, match="stale or tampered"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id=run_id,
            input_path=input_path,
            imported_at=datetime.now(UTC),
        )
    # atomic: not even the other 3, valid envelopes were written
    for opp_id in packet.opportunity_ids:
        assert store.get_critic_verdict(opp_id) is None
    assert not (run_dir / "reviews" / "provenance.jsonl").exists()
    store.close()


def test_review_import_rejects_evidence_id_from_a_different_card(tmp_path: Path) -> None:
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    run_id = final_state["run_id"]
    output_dir = tmp_path / "packet"
    packet = _export(store, run_dir, run_id, output_dir)

    opp_a, opp_b = packet.opportunity_ids[0], packet.opportunity_ids[1]
    card_b = store.get_opportunity_card(opp_b)
    assert card_b is not None
    foreign_evidence_id = card_b.evidence.evidence_ids[0]

    template = json.loads((output_dir / opp_a / "review-template.json").read_text(encoding="utf-8"))
    envelope = _complete_envelope(
        template,
        recommended_status="rejected",
        objections=[
            {
                "code": "other",
                "statement": "borrowed evidence from a different card",
                "evidence_ids": [foreign_evidence_id],
                "fatal": True,
            }
        ],
    )
    input_path = tmp_path / "completed-reviews.jsonl"
    _write_batch(input_path, [envelope])

    with pytest.raises(ReviewValidationError, match="does not belong to opportunity"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id=run_id,
            input_path=input_path,
            imported_at=datetime.now(UTC),
        )
    assert store.get_critic_verdict(opp_a) is None
    store.close()


def test_review_import_rejects_duplicate_review_across_batches(tmp_path: Path) -> None:
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    run_id = final_state["run_id"]
    output_dir = tmp_path / "packet"
    packet = _export(store, run_dir, run_id, output_dir)
    opp_id = packet.opportunity_ids[0]
    template = json.loads(
        (output_dir / opp_id / "review-template.json").read_text(encoding="utf-8")
    )
    envelope = _complete_envelope(template, recommended_status="investigate")

    first_path = tmp_path / "first.jsonl"
    _write_batch(first_path, [envelope])
    import_reviews(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        input_path=first_path,
        imported_at=datetime.now(UTC),
    )

    second_path = tmp_path / "second.jsonl"
    _write_batch(second_path, [envelope])
    with pytest.raises(ReviewValidationError, match="already has an imported review"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id=run_id,
            input_path=second_path,
            imported_at=datetime.now(UTC),
        )
    store.close()


def test_review_import_partial_batch_leaves_critic_status_incomplete(tmp_path: Path) -> None:
    """Partial review sets are allowed, but must not read as a completed
    critic pass -- see graph/nodes/finalize.py's critic_produced_any fix."""
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    run_id = final_state["run_id"]
    output_dir = tmp_path / "packet"
    packet = _export(store, run_dir, run_id, output_dir)

    only_one = packet.opportunity_ids[0]
    template = json.loads(
        (output_dir / only_one / "review-template.json").read_text(encoding="utf-8")
    )
    envelope = _complete_envelope(template, recommended_status="investigate")
    input_path = tmp_path / "completed-reviews.jsonl"
    _write_batch(input_path, [envelope])
    import_reviews(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        input_path=input_path,
        imported_at=datetime.now(UTC),
    )

    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    final = finalize_run(
        store=store,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        product=product_config,
        run_id=run_id,
        now=datetime.now(UTC),
    )
    verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
    assert verification["checks"]["critic_status"] != "PASS"
    assert final["verdict"] == "BLOCKED"
    store.close()


# --- finalize --------------------------------------------------------------------


def test_finalize_calls_no_agent_and_reaches_pass_after_full_review(tmp_path: Path) -> None:
    """The direct proof required for `demand-radar finalize`: it must never
    call an agent. finalize_run() constructs NeverCalledRunner for both
    roles internally -- if deterministic_judge/render_report/verify_run
    ever referenced ctx.analyst_runner/ctx.critic_runner, this test would
    fail with an uncaught AssertionError instead of completing.
    """
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    run_id = final_state["run_id"]
    output_dir = tmp_path / "packet"
    packet = _export(store, run_dir, run_id, output_dir)

    strong_cluster_id = _cluster_ids_by_key()[ffo.STRONG_PROBLEM_KEY]
    strong_opp_id = _opportunity_id(strong_cluster_id)

    envelopes = []
    for opp_id in packet.opportunity_ids:
        template = json.loads(
            (output_dir / opp_id / "review-template.json").read_text(encoding="utf-8")
        )
        status = "experiment_ready" if opp_id == strong_opp_id else "rejected"
        objections = (
            []
            if status == "experiment_ready"
            else [
                {
                    "code": "other",
                    "statement": "not a fit",
                    "evidence_ids": [],
                    "fatal": True,
                }
            ]
        )
        envelopes.append(
            _complete_envelope(template, recommended_status=status, objections=objections)
        )
    input_path = tmp_path / "completed-reviews.jsonl"
    _write_batch(input_path, envelopes)
    import_reviews(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        input_path=input_path,
        imported_at=datetime.now(UTC),
    )

    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    final = finalize_run(  # would raise AssertionError from NeverCalledRunner if it called an agent
        store=store,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        product=product_config,
        run_id=run_id,
        now=datetime.now(UTC),
    )

    strong_card = store.get_opportunity_card(strong_opp_id)
    assert strong_card is not None
    assert strong_card.status == "experiment_ready"
    verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
    assert verification["checks"]["critic_status"] == "PASS"
    assert final["verdict"] == "PASS"
    store.close()


def test_finalize_archives_preliminary_artifacts_before_overwriting(tmp_path: Path) -> None:
    store, run_dir, final_state = run_human_review_pipeline(tmp_path)
    run_id = final_state["run_id"]
    preliminary_report = (run_dir / "outputs" / "report.md").read_text(encoding="utf-8")
    preliminary_verification = (run_dir / "verification.json").read_text(encoding="utf-8")

    output_dir = tmp_path / "packet"
    packet = _export(store, run_dir, run_id, output_dir)
    envelopes = [
        _complete_envelope(
            json.loads((output_dir / opp_id / "review-template.json").read_text(encoding="utf-8")),
            recommended_status="investigate",
        )
        for opp_id in packet.opportunity_ids
    ]
    input_path = tmp_path / "completed-reviews.jsonl"
    _write_batch(input_path, envelopes)
    import_reviews(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        input_path=input_path,
        imported_at=datetime.now(UTC),
    )

    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    finalize_run(
        store=store,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        product=product_config,
        run_id=run_id,
        now=datetime.now(UTC),
    )

    archived_report = (run_dir / "preliminary" / "outputs" / "report.md").read_text(
        encoding="utf-8"
    )
    archived_verification = (run_dir / "preliminary" / "verification.json").read_text(
        encoding="utf-8"
    )
    assert archived_report == preliminary_report
    assert archived_verification == preliminary_verification
    store.close()


def test_finalize_without_a_run_raises_finalize_error(tmp_path: Path) -> None:
    db_path = tmp_path / "demand.db"
    store = Store.init(db_path)
    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    with pytest.raises(FinalizeError, match="does not exist"):
        finalize_run(
            store=store,
            run_dir=tmp_path / "never-ran",
            schemas_dir=REPO_ROOT / "schemas",
            product=product_config,
            run_id="never-ran",
            now=datetime.now(UTC),
        )
    store.close()


# --- CLI-level wiring (guards, human-review-pending message) -------------------


def test_cli_run_refuses_codex_analyst_even_with_critic_human(tmp_path: Path) -> None:
    """The existing codex refusal must compose correctly with the new
    "human" critic value -- codex is still refused regardless of what the
    other role is set to."""
    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "own-audit",
            "--analyst",
            "codex",
            "--critic",
            "human",
            "--db",
            str(db),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 2
    assert "codex_unverified_for_untrusted_content" in result.stdout


def test_cli_run_refuses_analyst_human_as_an_unknown_runner(tmp_path: Path) -> None:
    """--analyst human is not a legal value -- human is only ever a critic
    mode. Falls through to get_runner's existing "unknown runner" refusal."""
    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "own-audit",
            "--analyst",
            "human",
            "--critic",
            "fake",
            "--db",
            str(db),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 1
    assert "unknown runner" in result.stdout


def test_cli_run_with_critic_human_prints_pending_message_not_schema_failure(
    tmp_path: Path,
) -> None:
    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "own-audit",
            "--analyst",
            "fake",
            "--critic",
            "human",
            "--db",
            str(db),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "cli-human-run",
        ],
    )
    assert result.exit_code == 2
    assert "verdict=BLOCKED" in result.stdout
    assert "human review pending, not a schema failure" in result.stdout


def test_cli_review_export_import_finalize_round_trip(tmp_path: Path) -> None:
    """CLI-subcommand-level smoke test for the wiring (arg parsing, store
    open/close, exit codes) -- the store/run_dir are still set up by a
    direct pipeline call, since the CLI's own `run` cannot inject configured
    FakeRunner scenarios and would produce zero opportunities."""
    store, run_dir, final_state = run_human_review_pipeline(tmp_path, run_id="cli-packet-run")
    run_id = final_state["run_id"]
    db_path = tmp_path / "demand.db"
    runs_dir = tmp_path / "runs"
    store.close()

    output_dir = tmp_path / "packet"
    export_result = runner.invoke(
        app,
        [
            "review",
            "export",
            "--run",
            run_id,
            "--db",
            str(db_path),
            "--runs-dir",
            str(runs_dir),
            "--output",
            str(output_dir),
        ],
    )
    assert export_result.exit_code == 0, export_result.stdout
    assert (output_dir / "packet-manifest.json").is_file()

    opp_ids = json.loads((output_dir / "packet-manifest.json").read_text(encoding="utf-8"))[
        "opportunity_ids"
    ]
    envelopes = [
        _complete_envelope(
            json.loads((output_dir / opp_id / "review-template.json").read_text(encoding="utf-8")),
            recommended_status="investigate",
        )
        for opp_id in opp_ids
    ]
    input_path = tmp_path / "completed-reviews.jsonl"
    _write_batch(input_path, envelopes)

    import_result = runner.invoke(
        app,
        [
            "review",
            "import",
            "--run",
            run_id,
            "--input",
            str(input_path),
            "--db",
            str(db_path),
            "--runs-dir",
            str(runs_dir),
        ],
    )
    assert import_result.exit_code == 0, import_result.stdout
    assert f"imported {len(opp_ids)} review" in import_result.stdout

    finalize_result = runner.invoke(
        app,
        [
            "finalize",
            "--run",
            run_id,
            "--db",
            str(db_path),
            "--runs-dir",
            str(runs_dir),
        ],
    )
    # PASS, not BLOCKED: critic_status only tracks whether every opportunity
    # has a verdict, not what the verdict recommended -- none of these cards
    # reach experiment_ready, so the threshold/duplicate-inflation checks are
    # vacuously satisfied and the run-level verdict reports a clean pipeline.
    assert finalize_result.exit_code == 0, finalize_result.stdout
    assert f"run_id={run_id}" in finalize_result.stdout
