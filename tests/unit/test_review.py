"""Offline tests for demand_radar.review: hashing and review-envelope
validation. No Store, no run_dir, no filesystem -- see
tests/integration/test_review_workflow.py for the full pipeline round trip
(export -> import -> finalize) and the CLI-level wiring tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from demand_radar.models import (
    CheapestExperiment,
    CriticVerdict,
    Objection,
    OpportunityCard,
    OpportunityContext,
    OpportunityEvidence,
    OpportunityPersona,
    OpportunityProblem,
    OpportunitySignals,
    OverclaimCheck,
    ReviewAttestation,
    ReviewEnvelope,
    Reviewer,
)
from demand_radar.review import (
    ReviewValidationError,
    hash_evidence_manifest,
    hash_opportunity_card,
    validate_review_envelope,
)
from tests.factories import make_evidence_item

T0 = datetime(2026, 6, 1, tzinfo=UTC)


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


def _make_envelope(
    *,
    card: OpportunityCard,
    evidence_items: list,
    run_id: str = "run-1",
    recommended_status: str = "investigate",
    objections: list | None = None,
    opportunity_hash: str | None = None,
    evidence_manifest_hash: str | None = None,
) -> ReviewEnvelope:
    return ReviewEnvelope(
        run_id=run_id,
        opportunity_id=card.id,
        reviewer=Reviewer(kind="human", id="reviewer-1", conflict="none"),
        reviewed_at=T0,
        opportunity_hash=opportunity_hash or hash_opportunity_card(card),
        evidence_manifest_hash=evidence_manifest_hash or hash_evidence_manifest(evidence_items),
        attestation=ReviewAttestation(
            reviewed_primary_evidence=True, review_not_generated_by_analyst_provider=True
        ),
        verdict=CriticVerdict(
            opportunity_id=card.id,
            recommended_status=recommended_status,
            objections=objections or [],
            overclaim_check=OverclaimCheck(overclaims=False, statement=""),
            notes="",
        ),
    )


# --- hashing --------------------------------------------------------------------


def test_hash_opportunity_card_is_deterministic() -> None:
    card = _make_card()
    assert hash_opportunity_card(card) == hash_opportunity_card(card)


def test_hash_opportunity_card_changes_when_card_content_changes() -> None:
    card_a = _make_card()
    card_b = card_a.model_copy(update={"status": "rejected"})
    assert hash_opportunity_card(card_a) != hash_opportunity_card(card_b)


def test_hash_evidence_manifest_is_order_independent() -> None:
    a = make_evidence_item(source_id="a", published_at=T0, raw_text="text a")
    b = make_evidence_item(source_id="b", published_at=T0, raw_text="text b")
    assert hash_evidence_manifest([a, b]) == hash_evidence_manifest([b, a])


def test_hash_evidence_manifest_changes_when_membership_changes() -> None:
    a = make_evidence_item(source_id="a", published_at=T0, raw_text="text a")
    b = make_evidence_item(source_id="b", published_at=T0, raw_text="text b")
    assert hash_evidence_manifest([a]) != hash_evidence_manifest([a, b])


# --- validate_review_envelope ----------------------------------------------------


def test_validate_review_envelope_accepts_a_well_formed_envelope() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(card=card, evidence_items=[ev])
    validate_review_envelope(  # must not raise
        envelope,
        run_id="run-1",
        card=card,
        evidence_items_by_id={"ev_1": ev},
        already_reviewed_opportunity_ids=set(),
        seen_in_this_batch=set(),
    )


def test_validate_review_envelope_rejects_run_id_mismatch() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(card=card, evidence_items=[ev], run_id="run-1")
    with pytest.raises(ReviewValidationError, match="run_id mismatch"):
        validate_review_envelope(
            envelope,
            run_id="run-DIFFERENT",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


def test_validate_review_envelope_rejects_missing_opportunity() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(card=card, evidence_items=[ev])
    with pytest.raises(ReviewValidationError, match="does not exist"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=None,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


def test_validate_review_envelope_rejects_stale_opportunity_hash() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(card=card, evidence_items=[ev], opportunity_hash=f"sha256:{'0' * 64}")
    with pytest.raises(ReviewValidationError, match="opportunity_hash stale or tampered"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


def test_validate_review_envelope_rejects_stale_evidence_manifest_hash() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(
        card=card, evidence_items=[ev], evidence_manifest_hash=f"sha256:{'0' * 64}"
    )
    with pytest.raises(ReviewValidationError, match="evidence_manifest_hash stale or tampered"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


def test_validate_review_envelope_rejects_unknown_objection_evidence() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    objections = [
        Objection(code="other", statement="x", evidence_ids=["ev_nonexistent"], fatal=False)
    ]
    envelope = _make_envelope(card=card, evidence_items=[ev], objections=objections)
    with pytest.raises(ReviewValidationError, match="unknown evidence_id"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


def test_validate_review_envelope_rejects_objection_evidence_outside_card() -> None:
    """ev_2 exists in the store (it belongs to some other card) but is not
    part of *this* card's evidence -- must still be rejected."""
    card = _make_card(evidence_ids=["ev_1"])
    ev1 = make_evidence_item(source_id="1", published_at=T0, raw_text="t1").model_copy(
        update={"id": "ev_1"}
    )
    ev2 = make_evidence_item(source_id="2", published_at=T0, raw_text="t2").model_copy(
        update={"id": "ev_2"}
    )
    objections = [Objection(code="other", statement="x", evidence_ids=["ev_2"], fatal=False)]
    envelope = _make_envelope(card=card, evidence_items=[ev1], objections=objections)
    with pytest.raises(ReviewValidationError, match="does not belong to opportunity"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev1, "ev_2": ev2},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


def test_validate_review_envelope_rejects_verdict_opportunity_id_mismatch() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(card=card, evidence_items=[ev])
    envelope = envelope.model_copy(
        update={"verdict": envelope.verdict.model_copy(update={"opportunity_id": "opp_other"})}
    )
    with pytest.raises(ReviewValidationError, match="does not match"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


def test_validate_review_envelope_rejects_incomplete_attestation() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(card=card, evidence_items=[ev])
    envelope = envelope.model_copy(
        update={
            "attestation": ReviewAttestation(
                reviewed_primary_evidence=False, review_not_generated_by_analyst_provider=True
            )
        }
    )
    with pytest.raises(ReviewValidationError, match="reviewed_primary_evidence must be true"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch=set(),
        )


def test_validate_review_envelope_rejects_duplicate_within_batch() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(card=card, evidence_items=[ev])
    with pytest.raises(ReviewValidationError, match="duplicate review"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids=set(),
            seen_in_this_batch={card.id},
        )


def test_validate_review_envelope_rejects_already_reviewed_opportunity() -> None:
    card = _make_card(evidence_ids=["ev_1"])
    ev = make_evidence_item(source_id="1", published_at=T0, raw_text="t").model_copy(
        update={"id": "ev_1"}
    )
    envelope = _make_envelope(card=card, evidence_items=[ev])
    with pytest.raises(ReviewValidationError, match="already has an imported review"):
        validate_review_envelope(
            envelope,
            run_id="run-1",
            card=card,
            evidence_items_by_id={"ev_1": ev},
            already_reviewed_opportunity_ids={card.id},
            seen_in_this_batch=set(),
        )


def test_review_envelope_rejects_non_human_reviewer_kind_at_construction() -> None:
    """reviewer.kind is Literal["human"] -- Pydantic itself refuses any
    other value; validate_review_envelope does not need its own check."""
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        Reviewer(kind="model", id="x", conflict="none")  # type: ignore[arg-type]
