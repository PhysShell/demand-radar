"""Analyst (Claude) opportunity-generation prompt — one ProblemCluster in,
one OpportunityCandidate out. The candidate schema is a deliberate SUBSET of
schemas/opportunity-card.schema.json: it omits id/product/cluster_ids,
evidence stats, signals.demand_score/confidence, and status entirely, so the
model has no field to (mis)populate for anything this pipeline computes
itself — see docs/architecture.md "candidate-to-card assembly boundary".
"""

from __future__ import annotations

from typing import Any

from demand_radar.agents.prompts.trust import TRUST_PREAMBLE, wrap_untrusted
from demand_radar.config import ProductConfig
from demand_radar.models import Classification, EvidenceItem, ProblemCluster

OPPORTUNITY_CANDIDATE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OpportunityCandidate",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "problem",
        "persona",
        "context",
        "current_workarounds",
        "existing_substitutes",
        "possible_wedges",
        "risks",
        "cheapest_experiment",
    ],
    "properties": {
        "problem": {
            "type": "object",
            "additionalProperties": False,
            "required": ["statement"],
            "properties": {"statement": {"type": "string", "minLength": 1, "maxLength": 500}},
        },
        "persona": {
            "type": "object",
            "additionalProperties": False,
            "required": ["primary"],
            "properties": {"primary": {"type": "string", "minLength": 1, "maxLength": 120}},
        },
        "context": {
            "type": "object",
            "additionalProperties": False,
            "required": ["situation"],
            "properties": {"situation": {"type": "string", "minLength": 1, "maxLength": 500}},
        },
        "current_workarounds": {
            "type": "array",
            "maxItems": 15,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "existing_substitutes": {
            "type": "array",
            "maxItems": 15,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "possible_wedges": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "offer"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "concierge_service",
                            "cli",
                            "desktop_tool",
                            "browser_extension",
                            "plugin",
                            "saas",
                            "other",
                        ],
                    },
                    "offer": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        },
        "risks": {
            "type": "array",
            "maxItems": 15,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "cheapest_experiment": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "hypothesis",
                "input",
                "output",
                "commitment_event",
                "success_threshold",
                "failure_threshold",
            ],
            "properties": {
                "hypothesis": {"type": "string", "minLength": 1, "maxLength": 400},
                "input": {"type": "string", "minLength": 1, "maxLength": 300},
                "output": {"type": "string", "minLength": 1, "maxLength": 300},
                "commitment_event": {"type": "string", "minLength": 1, "maxLength": 300},
                "success_threshold": {"type": "string", "minLength": 1, "maxLength": 300},
                "failure_threshold": {"type": "string", "minLength": 1, "maxLength": 300},
            },
        },
    },
}


def build_opportunity_prompt(
    cluster: ProblemCluster,
    classifications: list[Classification],
    evidence_by_id: dict[str, EvidenceItem],
    product: ProductConfig,
) -> str:
    task = f"""\
TASK: propose ONE opportunity candidate for the problem cluster below, for \
the product "{product.display_name}".

You will receive the classified evidence supporting this cluster. Every \
field you return must be a short, honest synthesis of what the evidence \
actually shows -- never overclaim beyond it, and never invent a workaround, \
substitute, or wedge that isn't grounded in the evidence or a plainly \
reasonable inference from it. Do not assign any status, score, or evidence \
count -- this pipeline computes those deterministically from data you \
never see in this call.

Fields to return:
- problem.statement: one sentence naming the real underlying problem.
- persona.primary: the single best-fit persona across the evidence.
- context.situation: one sentence on the shared situation.
- current_workarounds: workarounds actually described in the evidence \
(deduplicated, short).
- existing_substitutes: named tools/products the evidence mentions people \
already tried or considered.
- possible_wedges: 1-3 plausible product wedges (type + one-line offer) \
that would address this problem, chosen from: concierge_service, cli, \
desktop_tool, browser_extension, plugin, saas, other.
- risks: short list of real risks/uncertainties visible in the evidence \
(e.g. small sample, one persona type, unclear willingness to pay).
- cheapest_experiment: the cheapest real test of demand (hypothesis, input, \
output, commitment_event, success_threshold, failure_threshold) -- never a \
plan that assumes the product is already built.
"""

    evidence_sections = []
    for c in classifications:
        item = evidence_by_id.get(c.evidence_id)
        if item is None:
            continue
        summary = (
            f"persona={c.persona.label!r} situation={c.situation.statement!r} "
            f"problem={c.problem.statement!r} desired_outcome={c.desired_outcome.statement!r} "
            f"workarounds={c.current_workarounds!r} signals={c.signals.model_dump()!r}\n"
            f"raw_text: {item.content.raw_text}"
        )
        evidence_sections.append(wrap_untrusted(c.evidence_id, summary))

    return (
        f"{TRUST_PREAMBLE}\n\n{task}\n"
        f"Cluster label: {cluster.label}\nCanonical problem so far: {cluster.canonical_problem}\n\n"
        + "\n".join(evidence_sections)
    )
