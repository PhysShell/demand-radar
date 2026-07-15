"""The deterministic judge — spec section 16. This is the one place that
sets OpportunityCard.status. Nothing upstream (analyst or critic) is trusted
to assign a status; both may only recommend. externally_validated is not
reachable from this function at all — it requires imported behavioral
evidence entered by a human, outside this pipeline entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from demand_radar.config import AcceptanceThresholds
from demand_radar.models import CriticVerdict, OpportunityStatus


@dataclass
class JudgeInputs:
    unique_authors: int
    source_families: int
    problem_evidence_count: int
    workaround_evidence_count: int
    canonical_member_count: int
    dominant_single_duplicate_group: bool  # every item collapses into one duplicate group
    schema_valid: bool
    evidence_refs_valid: bool
    critic_verdict: CriticVerdict | None
    critic_agent_ok: bool  # False if the critic call itself was BLOCKED/FAILED, not just absent


@dataclass
class JudgeResult:
    status: OpportunityStatus
    reasons: list[str] = field(default_factory=list)


def judge_opportunity(inputs: JudgeInputs, thresholds: AcceptanceThresholds) -> JudgeResult:
    # 1. Structural validity is checked first and is non-negotiable: a
    #    schema failure or a fabricated/missing evidence reference means
    #    nothing else here can be trusted, independent of how good the
    #    underlying evidence might otherwise be.
    if not inputs.schema_valid or not inputs.evidence_refs_valid:
        reasons = []
        if not inputs.schema_valid:
            reasons.append("schema validation failed")
        if not inputs.evidence_refs_valid:
            reasons.append("one or more evidence_ids do not exist in the evidence store")
        return JudgeResult(status="rejected", reasons=reasons)

    # 2. No completed critic review -> can never be experiment_ready. Not a
    #    rejection either: the evidence may be perfectly fine, just unreviewed.
    if inputs.critic_verdict is None or not inputs.critic_agent_ok:
        return JudgeResult(status="investigate", reasons=["critic review not completed"])

    # 3. A fatal critic objection is an outright block, regardless of volume.
    fatal = [o for o in inputs.critic_verdict.objections if o.fatal]
    if fatal:
        return JudgeResult(
            status="rejected",
            reasons=[f"critic fatal objection ({o.code}): {o.statement}" for o in fatal],
        )
    if inputs.critic_verdict.recommended_status == "rejected":
        return JudgeResult(
            status="rejected",
            reasons=[inputs.critic_verdict.notes or "critic recommended rejection"],
        )

    # 4. Volume/independence gates (spec section 16). Any shortfall means
    #    "not enough evidence yet", not "wrong idea" -> investigate.
    gate_reasons: list[str] = []
    if inputs.unique_authors < thresholds.minimum_unique_authors:
        gate_reasons.append(
            f"unique_authors {inputs.unique_authors} below minimum "
            f"{thresholds.minimum_unique_authors}"
        )
    if inputs.source_families < thresholds.minimum_source_families:
        gate_reasons.append(
            f"source_families {inputs.source_families} below minimum "
            f"{thresholds.minimum_source_families}"
        )
    if inputs.problem_evidence_count < thresholds.minimum_problem_evidence:
        gate_reasons.append(
            f"problem evidence count {inputs.problem_evidence_count} below minimum "
            f"{thresholds.minimum_problem_evidence}"
        )
    if inputs.workaround_evidence_count < thresholds.minimum_workaround_evidence:
        gate_reasons.append(
            f"workaround evidence count {inputs.workaround_evidence_count} below minimum "
            f"{thresholds.minimum_workaround_evidence}"
        )
    if inputs.dominant_single_duplicate_group:
        gate_reasons.append("every item belongs to a single duplicate group")

    if gate_reasons:
        return JudgeResult(status="investigate", reasons=gate_reasons)

    # 5. All deterministic gates cleared and the critic raised nothing fatal.
    #    A critic recommendation of "investigate" still holds the line short
    #    of experiment_ready -- it is a recommendation this code chooses to
    #    honor, not an authority handed to the model.
    if inputs.critic_verdict.recommended_status == "investigate":
        return JudgeResult(
            status="investigate",
            reasons=[inputs.critic_verdict.notes or "critic recommended further investigation"],
        )

    return JudgeResult(status="experiment_ready", reasons=[])
