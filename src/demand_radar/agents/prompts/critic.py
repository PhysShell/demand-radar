"""Codex critic prompt — one assembled (pre-status) OpportunityCard in, one
CriticVerdict out, using schemas/critic-verdict.schema.json directly as the
output schema (unlike the analyst calls, this one *is* fully LLM-facing --
nothing here is code-owned besides the opportunity_id echo, which is
validated against the known opportunity, not trusted blindly).

The critic's job is to find reasons to reject -- spec section 14 -- not to
improve the pitch. It never sees a "status" field to fill in and its
recommended_status is explicitly non-authoritative (scoring/judge.py has
final say).
"""

from __future__ import annotations

from demand_radar.agents.prompts.trust import TRUST_PREAMBLE, wrap_untrusted
from demand_radar.models import EvidenceItem, OpportunityCard

_OBJECTION_GUIDE = """\
Look specifically for these failure modes, and raise an objection with the \
matching code whenever the evidence supports it:
- complaint_not_purchase_intent: people are venting, not signaling they'd pay.
- single_viral_source_dominant: one popular post dominates the evidence \
count, with little independent corroboration.
- duplicate_or_copied_content: reposts/quotes are being double-counted as \
independent demand.
- audience_lacks_budget: the persona plausibly cannot or will not pay for \
a solution.
- free_substitute_sufficient: an adequate free/open-source substitute \
already exists.
- educational_not_commercial: this is a learning/how-to problem, not one \
people would pay to have solved for them.
- removes_enjoyable_activity: the proposed fix would remove an activity \
people actually enjoy doing themselves.
- required_input_unavailable: the solution needs an input (data, access, \
integration) that is not realistically available.
- implementation_cost_dominates: the cost to build plausibly dwarfs the \
demonstrated demand.
- evidence_supports_different_wedge: the evidence actually points to a \
different product shape than the one proposed.
- overclaims_evidence: the problem/persona/context statements assert more \
than the cited evidence actually shows.
Mark an objection `fatal: true` only when it alone should block \
experiment_ready; otherwise `fatal: false`. Do not soften or rewrite the \
opportunity -- only critique it. Set recommended_status to \
"experiment_ready" only if you found no fatal objection and the evidence \
genuinely supports commercial demand; otherwise "investigate" or \
"rejected".\
"""


def build_critic_prompt(
    card: OpportunityCard,
    evidence_by_id: dict[str, EvidenceItem],
) -> str:
    task = f"""\
TASK: critique the opportunity candidate below (`{card.id}`) for the \
product "{card.product}". You did not write it and have no attachment to \
it -- your job is exclusively to find reasons it should NOT be trusted as \
proven commercial demand yet.

{_OBJECTION_GUIDE}

opportunity_id to echo in your response: {card.id}
"""

    candidate_summary = wrap_untrusted(
        f"{card.id}:candidate",
        f"problem: {card.problem.statement}\n"
        f"persona: {card.persona.primary}\n"
        f"situation: {card.context.situation}\n"
        f"current_workarounds: {card.current_workarounds}\n"
        f"existing_substitutes: {card.existing_substitutes}\n"
        f"possible_wedges: {[(w.type, w.offer) for w in card.possible_wedges]}\n"
        f"cheapest_experiment: {card.cheapest_experiment.model_dump()}\n"
        f"unique_authors: {card.evidence.unique_authors}, "
        f"source_families: {card.evidence.source_families}\n"
        f"demand_score (ranking heuristic, not a probability): {card.signals.demand_score:.2f}",
    )

    evidence_sections = []
    for eid in card.evidence.evidence_ids:
        item = evidence_by_id.get(eid)
        if item is None:
            continue
        evidence_sections.append(
            wrap_untrusted(
                eid,
                f"source_family: {item.source.source_family}\nraw_text: {item.content.raw_text}",
            )
        )

    return f"{TRUST_PREAMBLE}\n\n{task}\n{candidate_summary}\n" + "\n".join(evidence_sections)
