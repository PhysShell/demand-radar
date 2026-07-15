"""Keeps schemas/*.json and models.py in sync: one golden instance per
schema, validated against both. If a model field and its schema diverge,
one of these two validations fails."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from demand_radar.models import (
    Classification,
    CriticVerdict,
    EvidenceItem,
    OpportunityCard,
    ProblemCluster,
    Verification,
)

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def _validate_against_schema(payload: dict, schema_file: str) -> None:
    schema = json.loads((SCHEMAS_DIR / schema_file).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize(
    "schema_file",
    [
        "evidence-item.schema.json",
        "classification.schema.json",
        "problem-cluster.schema.json",
        "opportunity-card.schema.json",
        "critic-verdict.schema.json",
        "verification.schema.json",
    ],
)
def test_schema_file_is_valid_json_schema(schema_file: str) -> None:
    schema = json.loads((SCHEMAS_DIR / schema_file).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


def test_evidence_item_round_trips_through_schema() -> None:
    item = EvidenceItem.model_validate(
        {
            "schema": "demand-radar.evidence-item/1",
            "id": "ev_abc123",
            "product": "own-audit",
            "source": {
                "kind": "reddit_post",
                "provider": "imported-jsonl",
                "source_id": "s1",
                "url": "https://example.com/1",
                "source_family": "reddit",
            },
            "author": {"stable_hash": f"sha256:{'a' * 64}"},
            "timestamps": {
                "published_at": "2026-06-01T00:00:00Z",
                "collected_at": "2026-06-02T00:00:00Z",
            },
            "content": {
                "raw_text": "hello",
                "normalized_text": "hello",
                "language": "en",
                "content_hash": f"sha256:{'b' * 64}",
            },
            "collection": {"query_id": "q1", "collector_version": "v1", "import_batch_id": "b1"},
            "trust": {"untrusted_external_content": True},
        }
    )
    dumped = json.loads(item.model_dump_json(by_alias=True, exclude_none=True))
    _validate_against_schema(dumped, "evidence-item.schema.json")


def test_classification_round_trips_through_schema() -> None:
    classification = Classification.model_validate(
        {
            "schema": "demand-radar.classification/1",
            "evidence_id": "ev_abc123",
            "relevance": {"product_fit": 0.9, "confidence": 0.8, "relevant": True},
            "persona": {"label": "wpf-developer"},
            "situation": {"statement": "maintaining a legacy app"},
            "problem": {"statement": "leak", "problem_key": "leak-key"},
            "desired_outcome": {"statement": "a fix"},
            "current_workarounds": ["manual tracing"],
            "signals": {"pain": 0.5, "urgency": 0.5, "commercial_intent": 0.5, "commitment": 0.0},
            "mentioned_tools": ["dotMemory"],
            "evidence_spans": [{"start": 0, "end": 5, "supports": "problem"}],
        }
    )
    dumped = json.loads(classification.model_dump_json(by_alias=True, exclude_none=True))
    _validate_against_schema(dumped, "classification.schema.json")


def test_problem_cluster_round_trips_through_schema() -> None:
    cluster = ProblemCluster.model_validate(
        {
            "schema": "demand-radar.problem-cluster/1",
            "id": "cluster_abc123",
            "product": "own-audit",
            "label": "Missing Ownership Path",
            "canonical_problem": "no ownership trace",
            "member_evidence_ids": ["ev_1", "ev_2"],
            "independence": {"unique_authors": 2, "source_families": 2, "duplicate_groups": 0},
            "signals": {
                "frequency": 0.5,
                "growth": 0.5,
                "pain": 0.5,
                "urgency": 0.5,
                "commercial_intent": 0.5,
                "commitment": 0.5,
                "product_fit": 0.5,
                "saturation": 0.1,
            },
            "first_seen": "2026-06-01T00:00:00Z",
            "last_seen": "2026-07-01T00:00:00Z",
        }
    )
    dumped = json.loads(cluster.model_dump_json(by_alias=True, exclude_none=True))
    _validate_against_schema(dumped, "problem-cluster.schema.json")


def test_opportunity_card_with_null_rejection_reasons_omits_the_field() -> None:
    """Regression test for the LangGraph/JSON-Schema None bug found while
    building the pipeline (docs/decisions.log.md): `rejection_reasons: null`
    fails the schema (declared as array|absent, not array|null), so the
    canonical serialization must omit it, not emit null."""
    card = OpportunityCard.model_validate(
        {
            "schema": "demand-radar.opportunity-card/1",
            "id": "opp_abc123",
            "product": "own-audit",
            "cluster_ids": ["cluster_abc123"],
            "problem": {"statement": "p"},
            "persona": {"primary": "dev"},
            "context": {"situation": "s"},
            "evidence": {"evidence_ids": ["ev_1"], "unique_authors": 3, "source_families": 2},
            "signals": {"demand_score": 5.0, "confidence": 0.5},
            "current_workarounds": [],
            "existing_substitutes": [],
            "possible_wedges": [],
            "risks": [],
            "cheapest_experiment": {
                "hypothesis": "h",
                "input": "i",
                "output": "o",
                "commitment_event": "c",
                "success_threshold": "s",
                "failure_threshold": "f",
            },
            "status": "experiment_ready",
        }
    )
    assert card.rejection_reasons is None
    dumped = json.loads(card.model_dump_json(by_alias=True, exclude_none=True))
    assert "rejection_reasons" not in dumped
    _validate_against_schema(dumped, "opportunity-card.schema.json")


def test_critic_verdict_round_trips_through_schema() -> None:
    verdict = CriticVerdict.model_validate(
        {
            "schema": "demand-radar.critic-verdict/1",
            "opportunity_id": "opp_abc123",
            "recommended_status": "investigate",
            "objections": [
                {
                    "code": "audience_lacks_budget",
                    "statement": "no budget",
                    "evidence_ids": [],
                    "fatal": False,
                }
            ],
            "overclaim_check": {"overclaims": False, "statement": "fine"},
            "notes": "ok",
        }
    )
    dumped = json.loads(verdict.model_dump_json(by_alias=True, exclude_none=True))
    _validate_against_schema(dumped, "critic-verdict.schema.json")


def test_verification_round_trips_through_schema() -> None:
    verification = Verification.model_validate(
        {
            "schema": "demand-radar.verification/1",
            "run_id": "run-1",
            "verdict": "PASS",
            "checks": {
                "schemas_valid": "PASS",
                "evidence_refs_valid": "PASS",
                "duplicate_inflation_absent": "PASS",
                "deterministic_rules_passed": "PASS",
                "report_generated": "PASS",
                "analyst_status": "PASS",
                "critic_status": "PASS",
            },
            "artifacts": {
                "input_manifest_hash": f"sha256:{'a' * 64}",
                "opportunities_hash": f"sha256:{'b' * 64}",
                "report_hash": f"sha256:{'c' * 64}",
            },
        }
    )
    dumped = json.loads(verification.model_dump_json(by_alias=True, exclude_none=True))
    _validate_against_schema(dumped, "verification.schema.json")


def test_unknown_field_rejected_by_model() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        Classification.model_validate(
            {
                "schema": "demand-radar.classification/1",
                "evidence_id": "ev_1",
                "relevance": {"product_fit": 0.5, "confidence": 0.5, "relevant": True},
                "persona": {"label": "x"},
                "situation": {"statement": "x"},
                "problem": {"statement": "x", "problem_key": "x"},
                "desired_outcome": {"statement": "x"},
                "current_workarounds": [],
                "signals": {"pain": 0, "urgency": 0, "commercial_intent": 0, "commitment": 0},
                "mentioned_tools": [],
                "evidence_spans": [],
                "unexpected_extra_field": "should not be allowed",
            }
        )
