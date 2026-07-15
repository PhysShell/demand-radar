"""Renders report.md — spec section 21. Pure formatting over already-computed
data: no LLM call, no DB access, no scoring logic lives here. Never copies
long stretches of raw_text (spec: "Report не должен содержать длинные копии
исходных публикаций") — evidence is cited by id + a short excerpt only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from demand_radar.models import (
    ClusterIndependence,
    CriticVerdict,
    EvidenceItem,
    OpportunityCard,
    ProblemCluster,
)

_EXCERPT_LEN = 140


def _excerpt(text: str, length: int = _EXCERPT_LEN) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


@dataclass
class ReportContext:
    run_id: str
    product: str
    since: str | None
    clusters: list[ProblemCluster]
    opportunities: list[OpportunityCard]
    critic_verdicts: dict[str, CriticVerdict]
    evidence_by_id: dict[str, EvidenceItem]
    total_evidence_count: int
    canonical_evidence_count: int
    duplicate_group_count: int
    pre_verification_checks: dict[str, str]
    collection_blind_spots: list[str] = field(default_factory=list)
    growth_threshold: float = 0.5


def _cluster_line(cluster: ProblemCluster) -> str:
    ind: ClusterIndependence = cluster.independence
    return (
        f"- **{cluster.label}** (`{cluster.id}`) — {ind.unique_authors} unique authors, "
        f"{ind.source_families} source families, {len(cluster.member_evidence_ids)} evidence items "
        f"({ind.duplicate_groups} duplicate group(s) folded in). "
        f"pain={cluster.signals.pain:.2f} "
        f"commercial_intent={cluster.signals.commercial_intent:.2f} "
        f"growth={cluster.signals.growth:.2f}\n"
        f"  {cluster.canonical_problem}"
    )


def _evidence_links(
    evidence_ids: list[str], evidence_by_id: dict[str, EvidenceItem], limit: int = 3
) -> str:
    lines = []
    for eid in evidence_ids[:limit]:
        item = evidence_by_id.get(eid)
        if item is None:
            lines.append(f"  - `{eid}` (not found in evidence store)")
            continue
        url_part = f" — {item.source.url}" if item.source.url else " — (no URL)"
        lines.append(f"  - `{eid}`{url_part}: “{_excerpt(item.content.raw_text)}”")
    remaining = len(evidence_ids) - limit
    if remaining > 0:
        lines.append(f"  - (+{remaining} more evidence item(s))")
    return "\n".join(lines)


def _opportunity_block(
    card: OpportunityCard, verdict: CriticVerdict | None, evidence_by_id: dict[str, EvidenceItem]
) -> str:
    lines = [
        f"### {card.problem.statement} (`{card.id}`)",
        "",
        f"- **Status:** `{card.status}`",
        f"- **Persona:** {card.persona.primary}",
        f"- **Situation:** {card.context.situation}",
        f"- **Unique authors:** {card.evidence.unique_authors} · "
        f"**Source families:** {card.evidence.source_families}",
        f"- **Demand score:** {card.signals.demand_score:.2f} "
        f"(ranking heuristic, not a market-size estimate — see docs/scoring.md) · "
        f"**Confidence:** {card.signals.confidence:.2f}",
    ]
    if card.current_workarounds:
        lines.append("- **Current workarounds:**")
        lines.extend(f"  - {w}" for w in card.current_workarounds)
    if card.existing_substitutes:
        lines.append("- **Existing substitutes:**")
        lines.extend(f"  - {s}" for s in card.existing_substitutes)
    if card.possible_wedges:
        lines.append("- **Possible wedges:**")
        lines.extend(f"  - `{w.type}`: {w.offer}" for w in card.possible_wedges)
    lines.append("- **Strongest evidence links:**")
    lines.append(_evidence_links(card.evidence.evidence_ids, evidence_by_id))
    if verdict is not None:
        lines.append(
            f"- **Critic recommendation:** `{verdict.recommended_status}`"
            + (f" — {verdict.notes}" if verdict.notes else "")
        )
        if verdict.objections:
            lines.append("- **Critic objections:**")
            for o in verdict.objections:
                fatal_marker = " (FATAL)" if o.fatal else ""
                lines.append(f"  - `{o.code}`{fatal_marker}: {o.statement}")
    else:
        lines.append("- **Critic recommendation:** not yet reviewed")
    lines.append(
        f"- **Cheapest experiment:** {card.cheapest_experiment.hypothesis} "
        f"(input: {card.cheapest_experiment.input}; output: {card.cheapest_experiment.output}; "
        f"commitment event: {card.cheapest_experiment.commitment_event})"
    )
    if card.risks:
        lines.append("- **Risks:**")
        lines.extend(f"  - {r}" for r in card.risks)
    if card.rejection_reasons:
        lines.append(f"- **Why not further along ({card.status}):**")
        lines.extend(f"  - {r}" for r in card.rejection_reasons)
    return "\n".join(lines)


def render_report(ctx: ReportContext) -> str:
    by_status: dict[str, list[OpportunityCard]] = {}
    for card in ctx.opportunities:
        by_status.setdefault(card.status, []).append(card)

    sections: list[str] = ["# Demand Radar Report", ""]

    sections += [
        "## Run summary",
        "",
        f"- Run: `{ctx.run_id}` · Product: `{ctx.product}` · Since: `{ctx.since or 'all time'}`",
        f"- Evidence ingested: {ctx.total_evidence_count} total, {ctx.canonical_evidence_count} "
        f"independent after dedup, folded into {ctx.duplicate_group_count} duplicate group(s)",
        f"- Problem clusters: {len(ctx.clusters)}",
        f"- Opportunities: {len(ctx.opportunities)} "
        f"(experiment_ready={len(by_status.get('experiment_ready', []))}, "
        f"investigate={len(by_status.get('investigate', []))}, "
        f"rejected={len(by_status.get('rejected', []))}, "
        f"observed={len(by_status.get('observed', []))})",
        "",
    ]

    sections += ["## Newly observed problem clusters", ""]
    if ctx.clusters:
        sections.append(
            "_All clusters below are newly formed by this run — cross-run cluster-identity "
            "tracking (the same problem persisting across multiple runs) is out of scope for "
            "this MVP; see docs/decisions.log.md._"
        )
        sections += [
            _cluster_line(c)
            for c in sorted(ctx.clusters, key=lambda c: c.signals.frequency, reverse=True)
        ]
    else:
        sections.append("_None._")
    sections.append("")

    growing = [c for c in ctx.clusters if c.signals.growth >= ctx.growth_threshold]
    sections += ["## Growing clusters", ""]
    if growing:
        sections += [
            _cluster_line(c) for c in sorted(growing, key=lambda c: c.signals.growth, reverse=True)
        ]
    else:
        sections.append(
            f"_None above the {ctx.growth_threshold:.2f} growth-signal display threshold._"
        )
    sections.append("")

    by_commercial = sorted(ctx.clusters, key=lambda c: c.signals.commercial_intent, reverse=True)[
        :5
    ]
    sections += ["## Strongest commercial signals", ""]
    if by_commercial:
        sections += [_cluster_line(c) for c in by_commercial]
    else:
        sections.append("_None._")
    sections.append("")

    sections += ["## Opportunity candidates", ""]
    live = (
        by_status.get("experiment_ready", [])
        + by_status.get("investigate", [])
        + by_status.get("observed", [])
    )
    if live:
        for card in sorted(live, key=lambda c: c.signals.demand_score, reverse=True):
            sections.append(
                _opportunity_block(card, ctx.critic_verdicts.get(card.id), ctx.evidence_by_id)
            )
            sections.append("")
    else:
        sections.append("_None._")
        sections.append("")

    sections += ["## Rejected ideas and reasons", ""]
    rejected = by_status.get("rejected", [])
    if rejected:
        for card in rejected:
            sections.append(f"- **{card.problem.statement}** (`{card.id}`)")
            for reason in card.rejection_reasons or []:
                sections.append(f"  - {reason}")
    else:
        sections.append("_None rejected this run._")
    sections.append("")

    sections += ["## Recommended cheapest experiments", ""]
    experiment_ready = by_status.get("experiment_ready", [])
    if experiment_ready:
        for card in experiment_ready:
            e = card.cheapest_experiment
            sections.append(f"- **{card.problem.statement}** (`{card.id}`)")
            sections.append(f"  - Hypothesis: {e.hypothesis}")
            sections.append(f"  - Input: {e.input} → Output: {e.output}")
            sections.append(f"  - Commitment event: {e.commitment_event}")
            sections.append(f"  - Success: {e.success_threshold} · Failure: {e.failure_threshold}")
    else:
        sections.append("_No opportunity reached experiment_ready this run._")
    sections.append("")

    sections += ["## Evidence gaps", ""]
    gaps = [
        f"`{card.problem.statement}` (`{card.id}`): {'; '.join(card.rejection_reasons)}"
        for card in by_status.get("investigate", [])
        if card.rejection_reasons
    ]
    if gaps:
        sections += [f"- {g}" for g in gaps]
    else:
        sections.append("_None flagged._")
    sections.append("")

    sections += ["## Collection blind spots", ""]
    if ctx.collection_blind_spots:
        sections.append(
            "_Configured problem queries with zero matching evidence this run — either the "
            "problem doesn't occur, or collection hasn't reached it yet:_"
        )
        sections += [f"- `{q}`" for q in ctx.collection_blind_spots]
    else:
        sections.append("_Every configured problem query surfaced at least one evidence item._")
    sections.append("")

    sections += ["## Verification", ""]
    sections.append(
        "_Pre-hash checks computed at report-render time; the signed record with artifact "
        "hashes is written to `verification.json` after this report (see docs/architecture.md)._"
    )
    for name, status in ctx.pre_verification_checks.items():
        sections.append(f"- `{name}`: **{status}**")
    sections.append("")

    return "\n".join(sections)
