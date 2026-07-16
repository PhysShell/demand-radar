"""Write-phase atomicity for import_reviews: a batch must either commit
completely or leave zero trace -- for both human and fixture review
batches, whether the failure happens on the first write or partway
through a larger one. See tests/unit/test_review.py and
tests/unit/test_review_fixtures.py for validation-phase atomicity (a batch
invalid before any write starts); these tests specifically inject a
failure *during* the write phase itself, after validation has already
passed for every envelope in the batch.

Two distinct write-phase windows are covered, found in two separate
arbiter review rounds:

1. Failure while inserting verdicts into SQLite, before either file is
   published (``test_*_import_write_phase_failure_rolls_back_completely``,
   ``test_second_batch_write_phase_failure_leaves_first_batchs_provenance_untouched``).
2. Failure during or after the two filesystem publish steps themselves --
   the import artifact rename, the provenance os.replace, or the final
   SQLite commit that comes after both (the
   ``test_*_publish_failure_*`` and ``test_sqlite_commit_failure_*`` tests
   below). The first round of tests only covered window 1; the code used
   to run both ``rename()`` calls after the SQLite commit and outside the
   try/except, so a failure in window 2 was not actually protected despite
   the report's claim -- these tests prove window 2 is now covered too.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from demand_radar.models import (
    CheapestExperiment,
    ClusterIndependence,
    ClusterSignals,
    CriticVerdict,
    OpportunityCard,
    OpportunityContext,
    OpportunityEvidence,
    OpportunityPersona,
    OpportunityProblem,
    OpportunitySignals,
    OverclaimCheck,
    ProblemCluster,
    ReviewAttestation,
    ReviewEnvelope,
    Reviewer,
    ReviewFixtureEnvelope,
)
from demand_radar.review import (
    FIXTURE_GENERATOR_NAME,
    ReviewWritePhaseError,
    hash_evidence_manifest,
    hash_opportunity_card,
    import_reviews,
    mark_run_as_test_fixture,
)
from demand_radar.storage.sqlite import Store
from tests.factories import make_evidence_item

T0 = datetime(2026, 7, 16, tzinfo=UTC)
OPP_IDS = ["opp_a", "opp_b", "opp_c"]


def _make_cluster() -> ProblemCluster:
    return ProblemCluster(
        id="cluster_test1",
        product="own-audit",
        label="a label",
        canonical_problem="a problem",
        member_evidence_ids=[f"ev_{oid.removeprefix('opp_')}" for oid in OPP_IDS],
        independence=ClusterIndependence(unique_authors=3, source_families=1, duplicate_groups=0),
        signals=ClusterSignals(
            frequency=0.5,
            growth=0.5,
            pain=0.5,
            urgency=0.5,
            commercial_intent=0.5,
            commitment=0.5,
            product_fit=0.5,
            saturation=0.1,
        ),
        first_seen=T0,
        last_seen=T0,
    )


def _make_card(opp_id: str, evidence_id: str) -> OpportunityCard:
    return OpportunityCard(
        id=opp_id,
        product="own-audit",
        cluster_ids=["cluster_test1"],
        problem=OpportunityProblem(statement="a problem"),
        persona=OpportunityPersona(primary="a persona"),
        context=OpportunityContext(situation="a situation"),
        evidence=OpportunityEvidence(
            evidence_ids=[evidence_id], unique_authors=1, source_families=1
        ),
        signals=OpportunitySignals(demand_score=5.0, confidence=0.5),
        cheapest_experiment=CheapestExperiment(
            hypothesis="h",
            input="i",
            output="o",
            commitment_event="c",
            success_threshold="s",
            failure_threshold="f",
        ),
        status="investigate",
        rejection_reasons=["critic review not completed"],
    )


def _store_with_opportunities(tmp_path: Path) -> tuple[Store, dict, dict]:
    """Three opportunities, each with its own single evidence item, all in
    one shared cluster -- enough to inject a failure on the 2nd of 3
    writes."""
    store = Store.init(tmp_path / "demand.db")
    store.create_run("run-1", "own-audit", since=None, analyst="fake", critic="human")

    evidence = {}
    for oid in OPP_IDS:
        eid = f"ev_{oid.removeprefix('opp_')}"
        ev = make_evidence_item(
            source_id=oid, published_at=T0, raw_text=f"text for {oid}"
        ).model_copy(update={"id": eid})
        store.insert_evidence_item(ev)
        evidence[oid] = ev

    store.upsert_problem_cluster("run-1", _make_cluster())

    cards = {}
    for oid in OPP_IDS:
        card = _make_card(oid, evidence[oid].id)
        store.upsert_opportunity_card("run-1", card)
        cards[oid] = card

    return store, cards, evidence


def _make_human_envelope(*, card: OpportunityCard, evidence_item) -> ReviewEnvelope:
    return ReviewEnvelope(
        run_id="run-1",
        opportunity_id=card.id,
        reviewer=Reviewer(kind="human", id="reviewer-1", conflict="none"),
        reviewed_at=T0,
        opportunity_hash=hash_opportunity_card(card),
        evidence_manifest_hash=hash_evidence_manifest([evidence_item]),
        attestation=ReviewAttestation(
            reviewed_primary_evidence=True, review_not_generated_by_analyst_provider=True
        ),
        verdict=CriticVerdict(
            opportunity_id=card.id,
            recommended_status="investigate",
            objections=[],
            overclaim_check=OverclaimCheck(overclaims=False, statement=""),
            notes="",
        ),
    )


def _make_fixture_envelope(*, card: OpportunityCard, evidence_item) -> ReviewFixtureEnvelope:
    return ReviewFixtureEnvelope.model_validate(
        {
            "schema": "demand-radar.review-fixture-envelope/1",
            "run_id": "run-1",
            "opportunity_id": card.id,
            "fixture": {
                "kind": "test_fixture",
                "generator": FIXTURE_GENERATOR_NAME,
                "purpose": "pipeline_mechanics_only",
                "substantive_review_performed": False,
            },
            "generated_at": T0.isoformat(),
            "opportunity_hash": hash_opportunity_card(card),
            "evidence_manifest_hash": hash_evidence_manifest([evidence_item]),
            "verdict": {
                "schema": "demand-radar.critic-verdict/1",
                "opportunity_id": card.id,
                "recommended_status": "investigate",
                "objections": [
                    {"code": "other", "statement": "Synthetic.", "evidence_ids": [], "fatal": True}
                ],
                "overclaim_check": {"overclaims": False, "statement": ""},
                "notes": "TEST FIXTURE ONLY",
            },
        }
    )


def _write_jsonl(path: Path, envelopes: list) -> None:
    path.write_text(
        "\n".join(e.model_dump_json(by_alias=True, exclude_none=True) for e in envelopes) + "\n",
        encoding="utf-8",
    )


def _fail_on_nth_call(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    """Makes Store._stage_critic_verdict raise on its n-th invocation across
    the whole test (1-indexed), succeeding normally on every other call --
    a controlled write-phase failure injected partway through a batch."""
    original = Store._stage_critic_verdict
    call_count = {"n": 0}

    def _maybe_boom(self: Store, run_id: str, verdict: CriticVerdict) -> None:
        call_count["n"] += 1
        if call_count["n"] == n:
            raise RuntimeError("simulated write-phase failure")
        original(self, run_id, verdict)

    monkeypatch.setattr(Store, "_stage_critic_verdict", _maybe_boom)


def _fail_os_replace_for(
    monkeypatch: pytest.MonkeyPatch, *, matches: Callable[[Path], bool]
) -> None:
    """Makes os.replace raise the first time its destination matches the
    given predicate; every other call -- including any unrelated to our
    two staged publish paths -- delegates to the real os.replace. Matching
    by destination shape (which directory, which filename) instead of a
    global call count lets each test target exactly one of the two publish
    steps regardless of call order or how many envelopes are in the
    batch."""
    original = os.replace

    def _maybe_boom(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if matches(Path(dst)):
            raise RuntimeError("simulated publish failure")
        original(src, dst)

    monkeypatch.setattr(os, "replace", _maybe_boom)


def _assert_no_staging_or_backup_files(run_dir: Path) -> None:
    assert list(run_dir.rglob("*.staging")) == []
    assert list(run_dir.rglob("*.bak")) == []


def test_human_import_write_phase_failure_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, cards, evidence = _store_with_opportunities(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    envelopes = [
        _make_human_envelope(card=cards[oid], evidence_item=evidence[oid]) for oid in OPP_IDS
    ]
    input_path = tmp_path / "batch.jsonl"
    _write_jsonl(input_path, envelopes)

    _fail_on_nth_call(monkeypatch, n=2)  # fails on the 2nd of 3 opportunities

    with pytest.raises(ReviewWritePhaseError, match="rolled back"):
        import_reviews(
            store=store, run_dir=run_dir, run_id="run-1", input_path=input_path, imported_at=T0
        )

    for oid in OPP_IDS:
        assert store.get_critic_verdict(oid) is None, f"{oid} must have zero verdict after rollback"
    assert not (run_dir / "reviews" / "provenance.jsonl").exists()
    imports_dir = run_dir / "reviews" / "imports"
    assert not imports_dir.exists() or list(imports_dir.iterdir()) == []
    store.close()


def test_fixture_import_write_phase_failure_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, cards, evidence = _store_with_opportunities(tmp_path)
    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )

    envelopes = [
        _make_fixture_envelope(card=cards[oid], evidence_item=evidence[oid]) for oid in OPP_IDS
    ]
    input_path = tmp_path / "batch.jsonl"
    _write_jsonl(input_path, envelopes)

    _fail_on_nth_call(monkeypatch, n=2)  # fails on the 2nd of 3 opportunities

    with pytest.raises(ReviewWritePhaseError, match="rolled back"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id="run-1",
            input_path=input_path,
            imported_at=T0,
            allow_test_fixture=True,
        )

    for oid in OPP_IDS:
        assert store.get_critic_verdict(oid) is None, f"{oid} must have zero verdict after rollback"
    assert not (run_dir / "reviews" / "provenance.jsonl").exists()
    imports_dir = run_dir / "reviews" / "imports"
    assert not imports_dir.exists() or list(imports_dir.iterdir()) == []
    store.close()


def test_second_batch_write_phase_failure_leaves_first_batchs_provenance_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stronger property than "provenance doesn't exist": a batch that
    fails must not corrupt or extend provenance.jsonl content that a prior,
    successful import already wrote."""
    store, cards, evidence = _store_with_opportunities(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    first_envelope = _make_human_envelope(card=cards["opp_a"], evidence_item=evidence["opp_a"])
    first_path = tmp_path / "first.jsonl"
    _write_jsonl(first_path, [first_envelope])
    import_reviews(
        store=store, run_dir=run_dir, run_id="run-1", input_path=first_path, imported_at=T0
    )
    provenance_path = run_dir / "reviews" / "provenance.jsonl"
    provenance_before = provenance_path.read_text(encoding="utf-8")
    assert len(provenance_before.splitlines()) == 1
    imports_before = sorted((run_dir / "reviews" / "imports").iterdir())
    assert len(imports_before) == 1

    second_envelopes = [
        _make_human_envelope(card=cards[oid], evidence_item=evidence[oid])
        for oid in ["opp_b", "opp_c"]
    ]
    second_path = tmp_path / "second.jsonl"
    _write_jsonl(second_path, second_envelopes)
    _fail_on_nth_call(monkeypatch, n=1)  # fails immediately on the first write of this 2nd batch

    with pytest.raises(ReviewWritePhaseError, match="rolled back"):
        import_reviews(
            store=store, run_dir=run_dir, run_id="run-1", input_path=second_path, imported_at=T0
        )

    assert store.get_critic_verdict("opp_a") is not None  # untouched by the failed 2nd batch
    assert store.get_critic_verdict("opp_b") is None
    assert store.get_critic_verdict("opp_c") is None
    assert provenance_path.read_text(encoding="utf-8") == provenance_before
    assert sorted((run_dir / "reviews" / "imports").iterdir()) == imports_before
    store.close()


def test_import_artifact_publish_failure_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first of the two publish steps -- the import artifact's
    os.replace into reviews/imports/ -- fails, after verdicts were already
    staged (inserted, not committed) in SQLite. Must prove the exact gap
    the arbiter found: the DB transaction must not be committed just
    because it *could* be, and nothing durable may be left behind."""
    store, cards, evidence = _store_with_opportunities(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    envelopes = [
        _make_human_envelope(card=cards[oid], evidence_item=evidence[oid]) for oid in OPP_IDS
    ]
    input_path = tmp_path / "batch.jsonl"
    _write_jsonl(input_path, envelopes)

    _fail_os_replace_for(monkeypatch, matches=lambda p: p.parent.name == "imports")

    with pytest.raises(ReviewWritePhaseError, match="rolled back"):
        import_reviews(
            store=store, run_dir=run_dir, run_id="run-1", input_path=input_path, imported_at=T0
        )

    for oid in OPP_IDS:
        assert store.get_critic_verdict(oid) is None
    assert not (run_dir / "reviews" / "provenance.jsonl").exists()
    imports_dir = run_dir / "reviews" / "imports"
    assert not imports_dir.exists() or list(imports_dir.iterdir()) == []
    _assert_no_staging_or_backup_files(run_dir)
    store.close()


def test_provenance_publish_failure_after_artifact_published_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second publish step (provenance's os.replace) fails after the
    first (the import artifact's os.replace) already succeeded. The
    already-published import artifact must be un-published (deleted), not
    left as an orphan pointing at verdicts that were never committed --
    this is the exact scenario the original renames-outside-try/except
    code could not handle."""
    store, cards, evidence = _store_with_opportunities(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    envelopes = [
        _make_human_envelope(card=cards[oid], evidence_item=evidence[oid]) for oid in OPP_IDS
    ]
    input_path = tmp_path / "batch.jsonl"
    _write_jsonl(input_path, envelopes)

    _fail_os_replace_for(monkeypatch, matches=lambda p: p.name == "provenance.jsonl")

    with pytest.raises(ReviewWritePhaseError, match="rolled back"):
        import_reviews(
            store=store, run_dir=run_dir, run_id="run-1", input_path=input_path, imported_at=T0
        )

    for oid in OPP_IDS:
        assert store.get_critic_verdict(oid) is None
    assert not (run_dir / "reviews" / "provenance.jsonl").exists()
    imports_dir = run_dir / "reviews" / "imports"
    assert (
        not imports_dir.exists() or list(imports_dir.iterdir()) == []
    ), "the already-published import artifact must be un-published"
    _assert_no_staging_or_backup_files(run_dir)
    store.close()


def test_second_batch_provenance_publish_failure_restores_first_batch_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same window as the test above, but against a run with a prior
    successful import already on disk -- proves the backup-and-restore
    path (provenance_existed_before=True), not just the
    delete-the-new-file path a first-ever import exercises."""
    store, cards, evidence = _store_with_opportunities(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    first_envelope = _make_human_envelope(card=cards["opp_a"], evidence_item=evidence["opp_a"])
    first_path = tmp_path / "first.jsonl"
    _write_jsonl(first_path, [first_envelope])
    import_reviews(
        store=store, run_dir=run_dir, run_id="run-1", input_path=first_path, imported_at=T0
    )
    provenance_path = run_dir / "reviews" / "provenance.jsonl"
    provenance_before = provenance_path.read_text(encoding="utf-8")
    imports_before = sorted((run_dir / "reviews" / "imports").iterdir())
    assert len(imports_before) == 1

    second_envelopes = [
        _make_human_envelope(card=cards[oid], evidence_item=evidence[oid])
        for oid in ["opp_b", "opp_c"]
    ]
    second_path = tmp_path / "second.jsonl"
    _write_jsonl(second_path, second_envelopes)

    _fail_os_replace_for(monkeypatch, matches=lambda p: p.name == "provenance.jsonl")

    with pytest.raises(ReviewWritePhaseError, match="rolled back"):
        import_reviews(
            store=store, run_dir=run_dir, run_id="run-1", input_path=second_path, imported_at=T0
        )

    assert store.get_critic_verdict("opp_a") is not None
    assert store.get_critic_verdict("opp_b") is None
    assert store.get_critic_verdict("opp_c") is None
    assert (
        provenance_path.read_text(encoding="utf-8") == provenance_before
    ), "must be restored from backup byte-identical, not just non-empty"
    assert (
        sorted((run_dir / "reviews" / "imports").iterdir()) == imports_before
    ), "2nd batch's artifact must be un-published; 1st batch's must survive untouched"
    _assert_no_staging_or_backup_files(run_dir)
    store.close()


def test_sqlite_commit_failure_after_both_files_published_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hardest window, named explicitly by the arbiter: both files are
    already published on disk when the final store.commit() itself raises.
    Must undo both publishes and leave zero verdicts committed."""
    store, cards, evidence = _store_with_opportunities(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    envelopes = [
        _make_human_envelope(card=cards[oid], evidence_item=evidence[oid]) for oid in OPP_IDS
    ]
    input_path = tmp_path / "batch.jsonl"
    _write_jsonl(input_path, envelopes)

    def _boom_commit(self: Store) -> None:
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(Store, "commit", _boom_commit)

    with pytest.raises(ReviewWritePhaseError, match="rolled back"):
        import_reviews(
            store=store, run_dir=run_dir, run_id="run-1", input_path=input_path, imported_at=T0
        )

    for oid in OPP_IDS:
        assert store.get_critic_verdict(oid) is None
    assert not (run_dir / "reviews" / "provenance.jsonl").exists()
    imports_dir = run_dir / "reviews" / "imports"
    assert not imports_dir.exists() or list(imports_dir.iterdir()) == []
    _assert_no_staging_or_backup_files(run_dir)
    store.close()
