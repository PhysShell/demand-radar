"""LangGraph resumability -- spec section 12.3 and acceptance 23.5. Crashes
a node mid-run, then resumes with entirely fresh Store/runner objects (a
new Store connection, new FakeRunner instances) to simulate a genuine
process restart, not just continuing in the same Python objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from demand_radar.agents.base import AgentResult
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
RUN_ID = "run-resume-test"


class CrashOnceRunner:
    """Wraps a real AgentRunner; raises on its first .run() call only, as
    if the process died mid-call -- then delegates normally afterward."""

    def __init__(self, inner: FakeRunner) -> None:
        self.inner = inner
        self.calls = 0

    def run(self, **kwargs: object) -> AgentResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated crash: process died mid agent-call")
        return self.inner.run(**kwargs)  # type: ignore[arg-type]


def _cluster_ids_by_key() -> dict[str, str]:
    return {
        ffo.STRONG_PROBLEM_KEY: _cluster_id_for([ffo.STRONG_PROBLEM_KEY]),
        ffo.WEAK_PROBLEM_KEY: _cluster_id_for([ffo.WEAK_PROBLEM_KEY]),
        ffo.VIRAL_PROBLEM_KEY: _cluster_id_for([ffo.VIRAL_PROBLEM_KEY]),
        ffo.EDUCATIONAL_PROBLEM_KEY: _cluster_id_for([ffo.EDUCATIONAL_PROBLEM_KEY]),
    }


def _make_ctx(db_path: Path, run_dir: Path, *, crash_analyst_once: bool) -> RunContext:
    store = Store.open(db_path)
    cluster_id_for_key = _cluster_ids_by_key()
    analyst_scenarios = dict(ffo.classify_scenarios())
    analyst_scenarios.update(ffo.opportunity_scenarios_by_cluster_id(cluster_id_for_key))
    critic_scenarios = {
        f"critic:opp_{cid.removeprefix('cluster_')}": ffo.critic_scenario_for(
            f"opp_{cid.removeprefix('cluster_')}", problem_key_hint=key
        )
        for key, cid in cluster_id_for_key.items()
    }
    analyst_runner: FakeRunner | CrashOnceRunner = FakeRunner(
        provider_name="fake-analyst", scenarios=analyst_scenarios
    )
    if crash_analyst_once:
        analyst_runner = CrashOnceRunner(analyst_runner)
    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    return RunContext(
        store=store,
        analyst_runner=analyst_runner,  # type: ignore[arg-type]
        critic_runner=FakeRunner(provider_name="fake-critic", scenarios=critic_scenarios),
        analyst_name="fake",
        critic_name="fake",
        product=product_config,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        now=datetime.now(UTC),
    )


def test_run_resumes_after_crash_without_rerunning_completed_nodes(tmp_path: Path) -> None:
    db_path = tmp_path / "demand.db"
    run_dir = tmp_path / "run"
    checkpoint_path = tmp_path / "checkpoints.sqlite"

    store = Store.init(db_path)
    ingest_jsonl_file(
        REPO_ROOT / "fixtures" / "mixed-demand-signals.jsonl", product=PRODUCT, store=store
    )
    store.create_run(RUN_ID, PRODUCT, since=None, analyst="fake", critic="fake")
    store.close()

    # Attempt 1: crashes on the first analyst call, inside `classify`.
    with open_checkpointer(checkpoint_path) as checkpointer:
        compiled = build_graph(checkpointer)
        ctx1 = _make_ctx(db_path, run_dir, crash_analyst_once=True)
        config = {"configurable": {"thread_id": RUN_ID, "ctx": ctx1}}
        crashed = False
        try:
            run_or_resume(
                compiled,
                initial_state=initial_state(run_id=RUN_ID, product=PRODUCT, since=None),
                config=config,
            )
        except RuntimeError:
            crashed = True
        ctx1.store.close()
    assert crashed, "expected the first attempt to crash"

    # Checkpoint should reflect: deduplicate already ran (accepted_evidence_ids
    # populated), classify had not yet completed (classifications still empty).
    with open_checkpointer(checkpoint_path) as checkpointer:
        compiled = build_graph(checkpointer)
        snapshot = compiled.get_state({"configurable": {"thread_id": RUN_ID}})
        assert snapshot.next == ("classify",)
        assert snapshot.values.get("classifications") == []
        assert snapshot.values.get("accepted_evidence_ids")

    # Attempt 2: brand-new Store connection and brand-new runner instances,
    # simulating a genuine process restart, not just continuing in memory.
    with open_checkpointer(checkpoint_path) as checkpointer:
        compiled = build_graph(checkpointer)
        ctx2 = _make_ctx(db_path, run_dir, crash_analyst_once=False)
        config2 = {"configurable": {"thread_id": RUN_ID, "ctx": ctx2}}
        final_state = run_or_resume(
            compiled,
            initial_state=initial_state(run_id=RUN_ID, product=PRODUCT, since=None),
            config=config2,
        )

        assert final_state["verdict"] == "PASS"
        cards = ctx2.store.list_opportunity_cards_for_run(RUN_ID)
        statuses = {c.status for c in cards}
        assert "experiment_ready" in statuses
        assert "rejected" in statuses
        ctx2.store.close()


def test_resuming_an_unstarted_thread_id_is_a_fresh_run(tmp_path: Path) -> None:
    """get_state on a thread_id with no prior checkpoint must not be
    confused with a resume -- run_or_resume falls back to a fresh invoke."""
    db_path = tmp_path / "demand.db"
    run_dir = tmp_path / "run"
    checkpoint_path = tmp_path / "checkpoints.sqlite"

    store = Store.init(db_path)
    ingest_jsonl_file(
        REPO_ROOT / "fixtures" / "mixed-demand-signals.jsonl", product=PRODUCT, store=store
    )
    store.create_run("run-brand-new", PRODUCT, since=None, analyst="fake", critic="fake")

    with open_checkpointer(checkpoint_path) as checkpointer:
        compiled = build_graph(checkpointer)
        ctx = _make_ctx(db_path, run_dir, crash_analyst_once=False)
        config = {"configurable": {"thread_id": "run-brand-new", "ctx": ctx}}
        final_state = run_or_resume(
            compiled,
            initial_state=initial_state(run_id="run-brand-new", product=PRODUCT, since=None),
            config=config,
        )
        assert final_state["verdict"] == "PASS"
        ctx.store.close()
