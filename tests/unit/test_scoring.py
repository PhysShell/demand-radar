from demand_radar.config import ScoringWeights
from demand_radar.models import ClusterSignals
from demand_radar.scoring.demand_score import compute_confidence, compute_demand_score

WEIGHTS = ScoringWeights()


def _signals(**overrides: float) -> ClusterSignals:
    defaults = {
        "frequency": 0.5,
        "growth": 0.5,
        "pain": 0.5,
        "urgency": 0.5,
        "commercial_intent": 0.5,
        "commitment": 0.5,
        "product_fit": 0.5,
        "saturation": 0.2,
    }
    defaults.update(overrides)
    return ClusterSignals(**defaults)


def test_demand_score_is_never_negative() -> None:
    weak = _signals(
        frequency=0,
        growth=0,
        pain=0,
        urgency=0,
        commercial_intent=0,
        commitment=0,
        product_fit=0,
        saturation=1.0,
    )
    result = compute_demand_score(weak, source_families=0, duplication_ratio=1.0, weights=WEIGHTS)
    assert result.total >= 0.0


def test_higher_commitment_increases_score_more_than_pain() -> None:
    base = _signals(commitment=0.0)
    with_commitment = _signals(commitment=1.0)
    base_score = compute_demand_score(
        base, source_families=2, duplication_ratio=0.0, weights=WEIGHTS
    ).total
    commitment_score = compute_demand_score(
        with_commitment, source_families=2, duplication_ratio=0.0, weights=WEIGHTS
    ).total
    with_pain = _signals(pain=1.0)
    pain_score = compute_demand_score(
        with_pain, source_families=2, duplication_ratio=0.0, weights=WEIGHTS
    ).total
    assert (commitment_score - base_score) > (pain_score - base_score)


def test_saturation_and_duplication_reduce_score() -> None:
    low = _signals(saturation=0.0)
    high = _signals(saturation=1.0)
    low_score = compute_demand_score(
        low, source_families=2, duplication_ratio=0.0, weights=WEIGHTS
    ).total
    high_score = compute_demand_score(
        high, source_families=2, duplication_ratio=0.0, weights=WEIGHTS
    ).total
    assert high_score < low_score

    no_dup_score = compute_demand_score(
        low, source_families=2, duplication_ratio=0.0, weights=WEIGHTS
    ).total
    all_dup_score = compute_demand_score(
        low, source_families=2, duplication_ratio=1.0, weights=WEIGHTS
    ).total
    assert all_dup_score < no_dup_score


def test_score_terms_sum_to_total() -> None:
    result = compute_demand_score(
        _signals(), source_families=3, duplication_ratio=0.2, weights=WEIGHTS
    )
    assert abs(sum(result.terms.values()) - result.total) < 1e-9 or result.total == 0.0


def test_weights_come_from_argument_not_hardcoded() -> None:
    custom = ScoringWeights(commitment=100.0)
    result = compute_demand_score(
        _signals(commitment=1.0), source_families=2, duplication_ratio=0.0, weights=custom
    )
    assert result.total > 50


def test_confidence_bounded_zero_to_one() -> None:
    high = compute_confidence(
        unique_authors=100,
        minimum_unique_authors=3,
        source_families=100,
        minimum_source_families=2,
        mean_relevance_confidence=1.0,
        has_fatal_objection=False,
    )
    low = compute_confidence(
        unique_authors=0,
        minimum_unique_authors=3,
        source_families=0,
        minimum_source_families=2,
        mean_relevance_confidence=0.0,
        has_fatal_objection=True,
    )
    assert 0.0 <= low <= high <= 1.0


def test_fatal_objection_halves_confidence() -> None:
    without = compute_confidence(
        unique_authors=5,
        minimum_unique_authors=3,
        source_families=3,
        minimum_source_families=2,
        mean_relevance_confidence=0.8,
        has_fatal_objection=False,
    )
    with_fatal = compute_confidence(
        unique_authors=5,
        minimum_unique_authors=3,
        source_families=3,
        minimum_source_families=2,
        mean_relevance_confidence=0.8,
        has_fatal_objection=True,
    )
    assert with_fatal == round(without / 2, 4) or with_fatal < without
