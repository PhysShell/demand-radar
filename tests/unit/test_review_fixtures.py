"""Offline/lightweight tests for the Phase 2C-SMOKE synthetic review
fixture path: schemas/review-fixture-envelope.schema.json,
review.generate_fixtures, and import_reviews' fixture handling
(--allow-test-fixture, the run's test_fixture=true marker, and the
prohibition on mixing fixture and human reviews in one run). See
tests/integration/test_review_fixture_smoke.py for the full pipeline round
trip (generate -> import -> finalize) proving no agent is called and no
card reaches experiment_ready/externally_validated.
"""

from __future__ import annotations

import json
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
    ReviewFixtureEnvelope,
)
from demand_radar.review import (
    FIXTURE_GENERATOR_NAME,
    ReviewValidationError,
    generate_fixtures,
    hash_evidence_manifest,
    hash_opportunity_card,
    import_reviews,
    mark_run_as_test_fixture,
    validate_review_fixture_envelope,
)
from demand_radar.storage.sqlite import Store
from tests.factories import make_evidence_item

T0 = datetime(2026, 7, 16, tzinfo=UTC)


def _make_cluster(
    cluster_id: str = "cluster_test1", evidence_ids: list[str] | None = None
) -> ProblemCluster:
    return ProblemCluster(
        id=cluster_id,
        product="own-audit",
        label="a label",
        canonical_problem="a problem",
        member_evidence_ids=evidence_ids or ["ev_1"],
        independence=ClusterIndependence(unique_authors=1, source_families=1, duplicate_groups=0),
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


def _make_card(opp_id: str = "opp_test1", evidence_ids: list[str] | None = None) -> OpportunityCard:
    return OpportunityCard(
        id=opp_id,
        product="own-audit",
        cluster_ids=["cluster_test1"],
        problem=OpportunityProblem(statement="a problem"),
        persona=OpportunityPersona(primary="a persona"),
        context=OpportunityContext(situation="a situation"),
        evidence=OpportunityEvidence(
            evidence_ids=evidence_ids or ["ev_1", "ev_2"], unique_authors=2, source_families=2
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


def _make_fixture_envelope(
    *,
    card: OpportunityCard,
    evidence_items: list,
    run_id: str = "run-1",
    recommended_status: str = "investigate",
    objections: list | None = None,
    fatal_objection: bool = True,
    opportunity_hash: str | None = None,
    evidence_manifest_hash: str | None = None,
) -> ReviewFixtureEnvelope:
    return ReviewFixtureEnvelope.model_validate(
        {
            "schema": "demand-radar.review-fixture-envelope/1",
            "run_id": run_id,
            "opportunity_id": card.id,
            "fixture": {
                "kind": "test_fixture",
                "generator": FIXTURE_GENERATOR_NAME,
                "purpose": "pipeline_mechanics_only",
                "substantive_review_performed": False,
            },
            "generated_at": T0.isoformat(),
            "opportunity_hash": opportunity_hash or hash_opportunity_card(card),
            "evidence_manifest_hash": evidence_manifest_hash
            or hash_evidence_manifest(evidence_items),
            "verdict": {
                "schema": "demand-radar.critic-verdict/1",
                "opportunity_id": card.id,
                "recommended_status": recommended_status,
                "objections": (
                    [o.model_dump(mode="json") for o in objections]
                    if objections is not None
                    else (
                        [
                            {
                                "code": "other",
                                "statement": "Synthetic test fixture only.",
                                "evidence_ids": [],
                                "fatal": True,
                            }
                        ]
                        if fatal_objection
                        else []
                    )
                ),
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


def _store_with_opportunity(
    tmp_path: Path, *, run_id: str = "run-1", opp_id: str = "opp_test1"
) -> tuple[Store, OpportunityCard, dict]:
    db_path = tmp_path / f"{run_id}.db"
    store = Store.init(db_path)
    store.create_run(run_id, "own-audit", since=None, analyst="fake", critic="human")
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    store.insert_evidence_item(ev)
    store.upsert_problem_cluster(run_id, _make_cluster())
    card = _make_card(opp_id=opp_id, evidence_ids=["ev_1"])
    store.upsert_opportunity_card(run_id, card)
    return store, card, {"ev_1": ev}


# --- generate_fixtures ---------------------------------------------------------


def _write_packet_manifest(packet_dir: Path, opportunity_ids: list[str]) -> dict:
    manifest = {
        "run_id": "irrelevant-to-generation",
        "opportunity_ids": sorted(opportunity_ids),
        "opportunity_hashes": {
            oid: f"sha256:{'a' * 63}{i}" for i, oid in enumerate(opportunity_ids)
        },
        "evidence_manifest_hashes": {
            oid: f"sha256:{'b' * 63}{i}" for i, oid in enumerate(opportunity_ids)
        },
    }
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "packet-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_generate_fixtures_creates_one_envelope_per_opportunity(tmp_path: Path) -> None:
    packet_dir = tmp_path / "packet"
    _write_packet_manifest(packet_dir, ["opp_a", "opp_b", "opp_c"])
    result = generate_fixtures(
        run_id="smoke-1",
        packet_dir=packet_dir,
        output_path=tmp_path / "fixtures.jsonl",
        generated_at=T0,
    )
    assert [f.opportunity_id for f in result.fixtures] == ["opp_a", "opp_b", "opp_c"]
    for f in result.fixtures:
        assert f.run_id == "smoke-1"
        assert f.fixture.kind == "test_fixture"
        assert f.fixture.substantive_review_performed is False
        assert f.verdict.recommended_status == "investigate"
        assert any(o.fatal for o in f.verdict.objections)
    lines = result.output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3


def test_generate_fixtures_output_is_deterministic(tmp_path: Path) -> None:
    packet_dir = tmp_path / "packet"
    _write_packet_manifest(packet_dir, ["opp_b", "opp_a"])
    out1 = tmp_path / "f1.jsonl"
    out2 = tmp_path / "f2.jsonl"
    generate_fixtures(run_id="smoke-1", packet_dir=packet_dir, output_path=out1, generated_at=T0)
    generate_fixtures(run_id="smoke-1", packet_dir=packet_dir, output_path=out2, generated_at=T0)
    assert out1.read_bytes() == out2.read_bytes()


def test_generate_fixtures_uses_manifest_hashes_verbatim(tmp_path: Path) -> None:
    packet_dir = tmp_path / "packet"
    manifest = _write_packet_manifest(packet_dir, ["opp_a"])
    result = generate_fixtures(
        run_id="smoke-1",
        packet_dir=packet_dir,
        output_path=tmp_path / "fixtures.jsonl",
        generated_at=T0,
    )
    assert result.fixtures[0].opportunity_hash == manifest["opportunity_hashes"]["opp_a"]
    assert (
        result.fixtures[0].evidence_manifest_hash == manifest["evidence_manifest_hashes"]["opp_a"]
    )


# --- validate_review_fixture_envelope -------------------------------------------


def test_validate_review_fixture_envelope_accepts_a_well_formed_fixture() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_fixture_envelope(card=card, evidence_items=[ev])
    validate_review_fixture_envelope(  # must not raise
        envelope,
        run_id="run-1",
        card=card,
        evidence_items_by_id={"ev_1": ev},
        already_reviewed_opportunity_ids=set(),
        seen_in_this_batch=set(),
    )


def test_validate_review_fixture_envelope_rejects_experiment_ready_recommendation() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_fixture_envelope(
        card=card, evidence_items=[ev], recommended_status="experiment_ready", fatal_objection=False
    )
    with pytest.raises(ReviewValidationError, match="must not recommend experiment_ready"):
        validate_review_fixture_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


# --- import_reviews: fixture gating ---------------------------------------------


def test_import_rejects_fixture_without_allow_test_fixture_flag(tmp_path: Path) -> None:
    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )
    envelope = _make_fixture_envelope(card=card, evidence_items=list(evidence.values()))
    input_path = tmp_path / "fixtures.jsonl"
    _write_jsonl(input_path, [envelope])

    with pytest.raises(ReviewValidationError, match="--allow-test-fixture"):
        import_reviews(
            store=store, run_dir=run_dir, run_id="run-1", input_path=input_path, imported_at=T0
        )
    assert store.get_critic_verdict(card.id) is None
    store.close()


def test_import_rejects_fixture_in_run_without_any_task_yaml(tmp_path: Path) -> None:
    """A run_dir that was never even set up via `demand-radar run` (no
    task.yaml at all) -- the generic "ad hoc / normal run" case."""
    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    envelope = _make_fixture_envelope(card=card, evidence_items=list(evidence.values()))
    input_path = tmp_path / "fixtures.jsonl"
    _write_jsonl(input_path, [envelope])

    with pytest.raises(ReviewValidationError, match="not marked test_fixture=true"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id="run-1",
            input_path=input_path,
            imported_at=T0,
            allow_test_fixture=True,
        )
    assert store.get_critic_verdict(card.id) is None
    store.close()


def test_import_rejects_fixture_in_a_real_pipeline_run(tmp_path: Path) -> None:
    """A run_dir shaped like a real `demand-radar run --critic human`
    output (a genuine task.yaml with product/analyst/critic/started_at) but
    never marked test_fixture -- this is what protects the canonical run
    itself, generically, without hardcoding its run_id anywhere."""
    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "task.yaml").write_text(
        "run_id: run-1\nproduct: own-audit\nsince: null\nanalyst: claude\ncritic: human\n"
        "started_at: '2026-07-16T00:00:00+00:00'\n",
        encoding="utf-8",
    )
    envelope = _make_fixture_envelope(card=card, evidence_items=list(evidence.values()))
    input_path = tmp_path / "fixtures.jsonl"
    _write_jsonl(input_path, [envelope])

    with pytest.raises(ReviewValidationError, match="not marked test_fixture=true"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id="run-1",
            input_path=input_path,
            imported_at=T0,
            allow_test_fixture=True,
        )
    assert store.get_critic_verdict(card.id) is None
    store.close()


def test_import_rejects_mixing_fixture_and_human_reviews_in_one_run(tmp_path: Path) -> None:
    from demand_radar.models import ReviewAttestation, ReviewEnvelope, Reviewer

    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )

    fixture_envelope = _make_fixture_envelope(card=card, evidence_items=list(evidence.values()))
    fixture_path = tmp_path / "fixture.jsonl"
    _write_jsonl(fixture_path, [fixture_envelope])
    import_reviews(
        store=store,
        run_dir=run_dir,
        run_id="run-1",
        input_path=fixture_path,
        imported_at=T0,
        allow_test_fixture=True,
    )

    human_envelope = ReviewEnvelope(
        run_id="run-1",
        opportunity_id=card.id,
        reviewer=Reviewer(kind="human", id="reviewer-1", conflict="none"),
        reviewed_at=T0,
        opportunity_hash=hash_opportunity_card(card),
        evidence_manifest_hash=hash_evidence_manifest(list(evidence.values())),
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
    human_path = tmp_path / "human.jsonl"
    _write_jsonl(human_path, [human_envelope])
    with pytest.raises(
        ReviewValidationError, match="test fixture review -- human reviews cannot be mixed"
    ):
        import_reviews(
            store=store, run_dir=run_dir, run_id="run-1", input_path=human_path, imported_at=T0
        )
    store.close()


def test_import_rejects_stale_opportunity_hash_for_fixture(tmp_path: Path) -> None:
    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )
    envelope = _make_fixture_envelope(
        card=card, evidence_items=list(evidence.values()), opportunity_hash=f"sha256:{'0' * 64}"
    )
    input_path = tmp_path / "fixtures.jsonl"
    _write_jsonl(input_path, [envelope])

    with pytest.raises(ReviewValidationError, match="opportunity_hash stale or tampered"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id="run-1",
            input_path=input_path,
            imported_at=T0,
            allow_test_fixture=True,
        )
    assert store.get_critic_verdict(card.id) is None
    assert not (run_dir / "reviews" / "provenance.jsonl").exists()
    store.close()


def test_import_rejects_stale_evidence_manifest_hash_for_fixture(tmp_path: Path) -> None:
    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )
    envelope = _make_fixture_envelope(
        card=card,
        evidence_items=list(evidence.values()),
        evidence_manifest_hash=f"sha256:{'0' * 64}",
    )
    input_path = tmp_path / "fixtures.jsonl"
    _write_jsonl(input_path, [envelope])

    with pytest.raises(ReviewValidationError, match="evidence_manifest_hash stale or tampered"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id="run-1",
            input_path=input_path,
            imported_at=T0,
            allow_test_fixture=True,
        )
    store.close()


def test_import_rejects_fixture_for_unknown_opportunity(tmp_path: Path) -> None:
    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )
    unknown_card = _make_card(opp_id="opp_does_not_exist", evidence_ids=["ev_1"])
    envelope = _make_fixture_envelope(card=unknown_card, evidence_items=list(evidence.values()))
    input_path = tmp_path / "fixtures.jsonl"
    _write_jsonl(input_path, [envelope])

    with pytest.raises(ReviewValidationError, match="does not exist"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id="run-1",
            input_path=input_path,
            imported_at=T0,
            allow_test_fixture=True,
        )
    store.close()


def test_import_rejects_fixture_with_malformed_nested_critic_verdict(tmp_path: Path) -> None:
    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )
    raw = {
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
        "evidence_manifest_hash": hash_evidence_manifest(list(evidence.values())),
        "verdict": {
            "schema": "demand-radar.critic-verdict/1",
            "opportunity_id": card.id,
            "recommended_status": "not_a_real_status",  # malformed: not in the enum
            "objections": [],
            "overclaim_check": {"overclaims": False, "statement": ""},
            "notes": "",
        },
    }
    input_path = tmp_path / "fixtures.jsonl"
    input_path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    with pytest.raises(ReviewValidationError, match="does not match ReviewFixtureEnvelope schema"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id="run-1",
            input_path=input_path,
            imported_at=T0,
            allow_test_fixture=True,
        )
    store.close()


def test_import_fixture_batch_is_atomic(tmp_path: Path) -> None:
    store, card_a, evidence_a = _store_with_opportunity(tmp_path, opp_id="opp_a")
    card_b = _make_card(opp_id="opp_b", evidence_ids=["ev_2"])
    store.upsert_opportunity_card("run-1", card_b)
    ev_b = make_evidence_item(source_id="2", published_at=T0, raw_text="t2").model_copy(
        update={"id": "ev_2"}
    )
    store.insert_evidence_item(ev_b)

    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )

    good_envelope = _make_fixture_envelope(card=card_a, evidence_items=list(evidence_a.values()))
    bad_envelope = _make_fixture_envelope(
        card=card_b, evidence_items=[ev_b], opportunity_hash=f"sha256:{'0' * 64}"
    )
    input_path = tmp_path / "fixtures.jsonl"
    _write_jsonl(input_path, [good_envelope, bad_envelope])

    with pytest.raises(ReviewValidationError, match="opportunity_hash stale or tampered"):
        import_reviews(
            store=store,
            run_dir=run_dir,
            run_id="run-1",
            input_path=input_path,
            imported_at=T0,
            allow_test_fixture=True,
        )
    # atomic: not even the first, valid envelope was written
    assert store.get_critic_verdict(card_a.id) is None
    assert store.get_critic_verdict(card_b.id) is None
    assert not (run_dir / "reviews" / "provenance.jsonl").exists()
    store.close()


def test_import_marks_fixture_provenance_as_synthetic(tmp_path: Path) -> None:
    store, card, evidence = _store_with_opportunity(tmp_path)
    run_dir = tmp_path / "run"
    mark_run_as_test_fixture(
        run_dir, kind="synthetic_review_pipeline_smoke", canonical_parent_run="parent-1"
    )
    envelope = _make_fixture_envelope(card=card, evidence_items=list(evidence.values()))
    input_path = tmp_path / "fixtures.jsonl"
    _write_jsonl(input_path, [envelope])

    result = import_reviews(
        store=store,
        run_dir=run_dir,
        run_id="run-1",
        input_path=input_path,
        imported_at=T0,
        allow_test_fixture=True,
    )
    assert result.contains_test_fixture is True
    assert store.get_critic_verdict(card.id) is not None

    provenance_lines = (
        (run_dir / "reviews" / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert len(provenance_lines) == 1
    record = json.loads(provenance_lines[0])
    assert record["source_kind"] == "test_fixture"
    assert record["fixture"]["kind"] == "test_fixture"
    assert record["fixture"]["substantive_review_performed"] is False
    store.close()
