"""demand_score is a ranking heuristic, not a market-size estimate and not a
probability of commercial success — see docs/scoring.md. All weights come
from config.ScoringWeights (product YAML or its defaults), never from a
prompt string.
"""

from __future__ import annotations

from dataclasses import dataclass

from demand_radar.config import ScoringWeights
from demand_radar.models import ClusterSignals

# Source-family count at which source_diversity saturates to 1.0. Not a
# real-mode threshold (see config.AcceptanceThresholds) — purely a scaling
# constant for this one input. 5 covers this MVP's fixture source families
# (reddit, forum, github, review-site, discord) without one extra family
# swamping the term.
MAX_SOURCE_FAMILIES_FOR_DIVERSITY = 5


@dataclass
class DemandScoreResult:
    total: float
    terms: dict[str, float]


def compute_demand_score(
    signals: ClusterSignals,
    *,
    source_families: int,
    duplication_ratio: float,
    weights: ScoringWeights,
) -> DemandScoreResult:
    source_diversity = min(1.0, source_families / MAX_SOURCE_FAMILIES_FOR_DIVERSITY)
    terms = {
        "frequency": signals.frequency * weights.frequency,
        "recent_growth": signals.growth * weights.recent_growth,
        "pain": signals.pain * weights.pain,
        "urgency": signals.urgency * weights.urgency,
        "commercial_intent": signals.commercial_intent * weights.commercial_intent,
        "commitment": signals.commitment * weights.commitment,
        "product_fit": signals.product_fit * weights.product_fit,
        "source_diversity": source_diversity * weights.source_diversity,
        # saturation/duplication weights are already negative in config
        # defaults, so every term here is a plain `value * weight` — sign
        # lives in the weight, not in a scattered minus sign at each site.
        "saturation": signals.saturation * weights.saturation,
        "duplication": duplication_ratio * weights.duplication,
    }
    total = max(0.0, sum(terms.values()))
    return DemandScoreResult(total=total, terms=terms)


def compute_confidence(
    *,
    unique_authors: int,
    minimum_unique_authors: int,
    source_families: int,
    minimum_source_families: int,
    mean_relevance_confidence: float,
    has_fatal_objection: bool,
) -> float:
    """0..1 confidence that the card's supporting evidence is solid — a
    weighted blend of how far past the acceptance minimums the evidence sits
    plus the analyst's own average relevance confidence, halved by any fatal
    critic objection. Not a market/success probability either."""
    author_ratio = min(1.0, unique_authors / max(1, minimum_unique_authors))
    family_ratio = min(1.0, source_families / max(1, minimum_source_families))
    base = 0.4 * author_ratio + 0.3 * family_ratio + 0.3 * mean_relevance_confidence
    if has_fatal_objection:
        base *= 0.5
    return round(min(1.0, max(0.0, base)), 4)
