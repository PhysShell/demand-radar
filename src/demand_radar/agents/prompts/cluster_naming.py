"""Analyst (Claude) 'preliminary clustering assistance' — spec section 14.
One call per run, not per cluster: the model never sees or returns evidence
ids, only the problem_key labels that deterministic grouping (by exact
problem_key match, in graph/nodes/clustering.py) already produced. It can
only propose which of those *keys* belong together and what to call the
merged group -- membership itself is expanded and validated entirely by
code afterward (verify_clusters), so a hallucinated or dropped evidence_id
is structurally impossible here: the model never had one to hallucinate.
"""

from __future__ import annotations

from typing import Any

from demand_radar.agents.prompts.trust import TRUST_PREAMBLE, wrap_untrusted

CLUSTER_PROPOSAL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ClusterProposal",
    "type": "object",
    "additionalProperties": False,
    "required": ["clusters"],
    "properties": {
        "clusters": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["problem_keys", "label", "canonical_problem"],
                "properties": {
                    "problem_keys": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                    "label": {"type": "string", "minLength": 1, "maxLength": 100},
                    "canonical_problem": {"type": "string", "minLength": 1, "maxLength": 400},
                },
            },
        }
    },
}


def build_cluster_naming_prompt(candidate_groups: dict[str, list[str]]) -> str:
    """candidate_groups: problem_key -> representative problem statements
    (already deterministically grouped; this call only labels/merges keys).
    """
    task = """\
TASK: you will receive a list of problem_key groups, each with a few \
representative problem statements already extracted from evidence (you do \
not see the evidence itself here, only these short statements). For each \
INPUT problem_key, decide:
- does it stand alone as its own cluster, or does it describe the exact \
same underlying problem as one or more OTHER listed problem_keys and \
should be merged with them?
- what short, human-readable label and one-sentence canonical_problem best \
describes the (possibly merged) cluster?

Every problem_key you were given must appear in exactly one output \
cluster's problem_keys list -- do not drop any, and do not invent a \
problem_key that was not listed below. Only merge keys that describe the \
literal same problem, not merely a related or adjacent one.
"""
    groups_text = "\n".join(
        f"- {key}: " + " | ".join(statements[:3]) for key, statements in candidate_groups.items()
    )
    return f"{TRUST_PREAMBLE}\n\n{task}\n" + wrap_untrusted("candidate_problem_keys", groups_text)
