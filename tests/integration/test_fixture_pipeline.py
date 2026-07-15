"""The fixture end-to-end run -- spec section 22's required outcomes, run
through the real LangGraph pipeline with FakeRunner. This is the test that
proves the deterministic core, clustering, scoring, and judge all compose
correctly, not just individually.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from demand_radar.agents.fake import FakeRunner
from demand_radar.config import load_product_config_by_name
from demand_radar.graph.build import build_graph, open_checkpointer, run_or_resume
from demand_radar.graph.nodes.clustering import _cluster_id_for
from demand_radar.graph.state import RunContext, initial_state
from demand_radar.ingest.jsonl import ingest_jsonl_file
from demand_radar.storage.sqlite import Store
from tests.integration import fixture_fake_outputs as ffo

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCT = "own-audit"


def _cluster_ids_by_key() -> dict[str, str]:
    return {
        ffo.STRONG_PROBLEM_KEY: _cluster_id_for([ffo.STRONG_PROBLEM_KEY]),
        ffo.WEAK_PROBLEM_KEY: _cluster_id_for([ffo.WEAK_PROBLEM_KEY]),
        ffo.VIRAL_PROBLEM_KEY: _cluster_id_for([ffo.VIRAL_PROBLEM_KEY]),
        ffo.EDUCATIONAL_PROBLEM_KEY: _cluster_id_for([ffo.EDUCATIONAL_PROBLEM_KEY]),
    }


def _opportunity_id(cluster_id: str) -> str:
    return f"opp_{cluster_id.removeprefix('cluster_')}"


def _build_runners(cluster_id_for_key: dict[str, str]) -> tuple[FakeRunner, FakeRunner]:
    analyst_scenarios = dict(ffo.classify_scenarios())
    analyst_scenarios.update(ffo.opportunity_scenarios_by_cluster_id(cluster_id_for_key))
    critic_scenarios = {
        f"critic:{_opportunity_id(cid)}": ffo.critic_scenario_for(
            _opportunity_id(cid), problem_key_hint=key
        )
        for key, cid in cluster_id_for_key.items()
    }
    return (
        FakeRunner(provider_name="fake-analyst", scenarios=analyst_scenarios),
        FakeRunner(provider_name="fake-critic", scenarios=critic_scenarios),
    )


def run_fixture_pipeline(tmp_path: Path, run_id: str = "run-fixture-e2e") -> tuple[Store, dict]:
    db_path = tmp_path / "demand.db"
    run_dir = tmp_path / "run"
    checkpoint_path = tmp_path / "checkpoints.sqlite"

    store = Store.init(db_path)
    ingest_jsonl_file(
        REPO_ROOT / "fixtures" / "mixed-demand-signals.jsonl", product=PRODUCT, store=store
    )
    store.create_run(run_id, PRODUCT, since=None, analyst="fake", critic="fake")

    cluster_id_for_key = _cluster_ids_by_key()
    analyst_runner, critic_runner = _build_runners(cluster_id_for_key)
    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")

    ctx = RunContext(
        store=store,
        analyst_runner=analyst_runner,
        critic_runner=critic_runner,
        analyst_name="fake",
        critic_name="fake",
        product=product_config,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        now=datetime.now(UTC),
    )

    with open_checkpointer(checkpoint_path) as checkpointer:
        compiled = build_graph(checkpointer)
        config = {"configurable": {"thread_id": run_id, "ctx": ctx}}
        final_state = run_or_resume(
            compiled,
            initial_state=initial_state(run_id=run_id, product=PRODUCT, since=None),
            config=config,
        )

    return store, final_state


def test_fixture_run_reaches_pass_verdict(tmp_path: Path) -> None:
    store, final_state = run_fixture_pipeline(tmp_path)
    assert final_state["verdict"] == "PASS"
    store.close()


def test_fixture_run_produces_at_least_one_experiment_ready(tmp_path: Path) -> None:
    store, final_state = run_fixture_pipeline(tmp_path)
    cards = store.list_opportunity_cards_for_run(final_state["run_id"])
    experiment_ready = [c for c in cards if c.status == "experiment_ready"]
    assert len(experiment_ready) >= 1
    store.close()


def test_fixture_run_produces_at_least_one_investigate(tmp_path: Path) -> None:
    store, final_state = run_fixture_pipeline(tmp_path)
    cards = store.list_opportunity_cards_for_run(final_state["run_id"])
    investigate = [c for c in cards if c.status == "investigate"]
    assert len(investigate) >= 1
    store.close()


def test_fixture_run_produces_at_least_two_rejected(tmp_path: Path) -> None:
    store, final_state = run_fixture_pipeline(tmp_path)
    cards = store.list_opportunity_cards_for_run(final_state["run_id"])
    rejected = [c for c in cards if c.status == "rejected"]
    assert len(rejected) >= 2
    store.close()


def test_rejected_candidates_have_distinct_reasons_not_hidden(tmp_path: Path) -> None:
    store, final_state = run_fixture_pipeline(tmp_path)
    cards = store.list_opportunity_cards_for_run(final_state["run_id"])
    rejected = [c for c in cards if c.status == "rejected"]
    for card in rejected:
        assert card.rejection_reasons
    reasons = [tuple(sorted(c.rejection_reasons or [])) for c in rejected]
    assert len(set(reasons)) == len(reasons)  # each rejection is reasoned differently
    store.close()


def test_experiment_ready_card_clears_all_thresholds(tmp_path: Path) -> None:
    store, final_state = run_fixture_pipeline(tmp_path)
    cards = store.list_opportunity_cards_for_run(final_state["run_id"])
    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    for card in cards:
        if card.status != "experiment_ready":
            continue
        assert card.evidence.unique_authors >= product_config.thresholds.minimum_unique_authors
        assert card.evidence.source_families >= product_config.thresholds.minimum_source_families
        assert card.rejection_reasons is None
    store.close()


def test_viral_post_alone_cannot_reach_experiment_ready(tmp_path: Path) -> None:
    """The one-viral-post cluster must never pass regardless of engagement
    -- spec section 18 rule 7."""
    store, final_state = run_fixture_pipeline(tmp_path)
    viral_cluster_id = _cluster_ids_by_key()[ffo.VIRAL_PROBLEM_KEY]
    card = store.get_opportunity_card(_opportunity_id(viral_cluster_id))
    assert card is not None
    assert card.status != "experiment_ready"
    store.close()


def test_all_evidence_ids_on_every_card_exist_in_store(tmp_path: Path) -> None:
    store, final_state = run_fixture_pipeline(tmp_path)
    cards = store.list_opportunity_cards_for_run(final_state["run_id"])
    for card in cards:
        for eid in card.evidence.evidence_ids:
            assert store.get_evidence_item(eid) is not None
    store.close()


def test_verification_json_matches_final_verdict(tmp_path: Path) -> None:
    import json

    run_dir = tmp_path / "run"
    store, final_state = run_fixture_pipeline(tmp_path)
    verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
    assert verification["verdict"] == final_state["verdict"]
    assert verification["checks"]["schemas_valid"] == "PASS"
    assert verification["checks"]["evidence_refs_valid"] == "PASS"
    assert verification["checks"]["duplicate_inflation_absent"] == "PASS"
    store.close()


def test_report_md_generated_and_reasonably_sized(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    store, _final_state = run_fixture_pipeline(tmp_path)
    report_text = (run_dir / "outputs" / "report.md").read_text(encoding="utf-8")
    assert "# Demand Radar Report" in report_text
    assert "## Opportunity candidates" in report_text
    assert "## Rejected ideas and reasons" in report_text
    store.close()


def test_report_does_not_contain_full_raw_evidence_dumps(tmp_path: Path) -> None:
    """Spec section 21: the report must not contain long copies of source
    posts -- only short excerpts."""
    run_dir = tmp_path / "run"
    store, final_state = run_fixture_pipeline(tmp_path)
    report_text = (run_dir / "outputs" / "report.md").read_text(encoding="utf-8")
    long_items = [
        item
        for eid in final_state["accepted_evidence_ids"]
        if (item := store.get_evidence_item(eid)) and len(item.content.raw_text) > 140
    ]
    store.close()
    assert (
        long_items
    ), "fixture should contain at least one evidence item longer than the excerpt limit"
    for item in long_items:
        assert item.content.raw_text not in report_text
