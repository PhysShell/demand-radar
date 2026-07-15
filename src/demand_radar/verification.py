"""Deterministic post-hoc verification — spec section 20. Every check here
is plain code re-inspecting stored data; none of it calls a model. The same
`compute_checks` runs twice: once (informational) when render_report writes
report.md, and once more (canonical) by verify_run once report.md exists and
can be hashed into verification.json — see graph/nodes/finalize.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from demand_radar.config import AcceptanceThresholds
from demand_radar.models import AgentRunStatus, CheckStatus, OpportunityCard
from demand_radar.storage.sqlite import Store


def check_schemas_valid(opportunities: list[OpportunityCard], schemas_dir: Path) -> CheckStatus:
    schema = json.loads((schemas_dir / "opportunity-card.schema.json").read_text(encoding="utf-8"))
    for card in opportunities:
        try:
            jsonschema.validate(
                json.loads(card.model_dump_json(by_alias=True, exclude_none=True)), schema
            )
        except jsonschema.ValidationError:
            return "FAIL"
    return "PASS"


def check_evidence_refs_valid(store: Store, opportunities: list[OpportunityCard]) -> CheckStatus:
    for card in opportunities:
        for eid in card.evidence.evidence_ids:
            if not store.evidence_exists(eid):
                return "FAIL"
    return "PASS"


def check_duplicate_inflation_absent(
    store: Store, product: str, opportunities: list[OpportunityCard]
) -> CheckStatus:
    duplicate_ids = {link.evidence_id for link in store.list_duplicate_links(product)}
    for card in opportunities:
        if card.status != "experiment_ready":
            continue
        canonical_referenced = [
            eid for eid in card.evidence.evidence_ids if eid not in duplicate_ids
        ]
        if len(canonical_referenced) < 2:
            return "FAIL"
    return "PASS"


def check_deterministic_rules_passed(
    thresholds: AcceptanceThresholds, opportunities: list[OpportunityCard]
) -> CheckStatus:
    for card in opportunities:
        if card.status == "externally_validated":
            return "FAIL"  # unreachable via this pipeline; a model cannot set it
        if card.status != "experiment_ready":
            continue
        if card.evidence.unique_authors < thresholds.minimum_unique_authors:
            return "FAIL"
        if card.evidence.source_families < thresholds.minimum_source_families:
            return "FAIL"
    return "PASS"


def derive_agent_status(
    errors: list[dict[str, Any]], nodes: set[str], produced_any: bool
) -> AgentRunStatus:
    relevant = [e for e in errors if e.get("node") in nodes]
    for e in relevant:
        message = str(e.get("message", ""))
        for status in ("BLOCKED_AUTH", "BLOCKED_USAGE", "BLOCKED_TIMEOUT", "BLOCKED_NOT_INSTALLED"):
            if status in message:
                return status
    if produced_any:
        return "PASS"
    if relevant:
        return "FAIL_INVALID_OUTPUT"
    return "NOT_RUN"


def compute_checks(
    *,
    store: Store,
    product: str,
    opportunities: list[OpportunityCard],
    schemas_dir: Path,
    thresholds: AcceptanceThresholds,
    errors: list[dict[str, Any]],
    analyst_produced_any: bool,
    critic_produced_any: bool,
    report_generated: bool,
) -> dict[str, str]:
    return {
        "schemas_valid": check_schemas_valid(opportunities, schemas_dir),
        "evidence_refs_valid": check_evidence_refs_valid(store, opportunities),
        "duplicate_inflation_absent": check_duplicate_inflation_absent(
            store, product, opportunities
        ),
        "deterministic_rules_passed": check_deterministic_rules_passed(thresholds, opportunities),
        "report_generated": "PASS" if report_generated else "FAIL",
        "analyst_status": derive_agent_status(
            errors, {"classify", "generate_opportunities"}, analyst_produced_any
        ),
        "critic_status": derive_agent_status(errors, {"critic_review"}, critic_produced_any),
    }


def overall_verdict(checks: dict[str, str]) -> str:
    blocked_values = {
        "BLOCKED_AUTH",
        "BLOCKED_USAGE",
        "BLOCKED_TIMEOUT",
        "BLOCKED_NOT_INSTALLED",
        "NOT_RUN",
    }
    if checks["analyst_status"] in blocked_values or checks["critic_status"] in blocked_values:
        return "BLOCKED"
    if all(v == "PASS" for k, v in checks.items() if k not in ("analyst_status", "critic_status")):
        if checks["analyst_status"] == "PASS" and checks["critic_status"] == "PASS":
            return "PASS"
    return "FAIL"
