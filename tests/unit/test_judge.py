"""The deterministic judge -- spec section 16 and acceptance 23.6."""

from __future__ import annotations

from demand_radar.config import AcceptanceThresholds
from demand_radar.models import CriticVerdict, Objection, OverclaimCheck
from demand_radar.scoring.judge import JudgeInputs, judge_opportunity

THRESHOLDS = AcceptanceThresholds(
    minimum_unique_authors=3,
    minimum_source_families=2,
    minimum_problem_evidence=3,
    minimum_workaround_evidence=1,
)


def _passing_verdict(recommended_status: str = "experiment_ready") -> CriticVerdict:
    return CriticVerdict(
        opportunity_id="opp_test",
        recommended_status=recommended_status,  # type: ignore[arg-type]
        objections=[],
        overclaim_check=OverclaimCheck(overclaims=False, statement=""),
        notes="",
    )


def _base_inputs(**overrides: object) -> JudgeInputs:
    defaults: dict[str, object] = {
        "unique_authors": 5,
        "source_families": 3,
        "problem_evidence_count": 5,
        "workaround_evidence_count": 2,
        "canonical_member_count": 5,
        "dominant_single_duplicate_group": False,
        "schema_valid": True,
        "evidence_refs_valid": True,
        "critic_verdict": _passing_verdict(),
        "critic_agent_ok": True,
    }
    defaults.update(overrides)
    return JudgeInputs(**defaults)  # type: ignore[arg-type]


def test_all_gates_clear_yields_experiment_ready() -> None:
    result = judge_opportunity(_base_inputs(), THRESHOLDS)
    assert result.status == "experiment_ready"
    assert result.reasons == []


def test_schema_invalid_is_rejected_not_investigate() -> None:
    result = judge_opportunity(_base_inputs(schema_valid=False), THRESHOLDS)
    assert result.status == "rejected"
    assert any("schema" in r for r in result.reasons)


def test_missing_evidence_refs_is_rejected() -> None:
    result = judge_opportunity(_base_inputs(evidence_refs_valid=False), THRESHOLDS)
    assert result.status == "rejected"


def test_no_critic_verdict_yields_investigate_never_experiment_ready() -> None:
    result = judge_opportunity(_base_inputs(critic_verdict=None, critic_agent_ok=False), THRESHOLDS)
    assert result.status == "investigate"
    assert result.status != "experiment_ready"


def test_fatal_objection_blocks_experiment_ready_even_with_perfect_volume() -> None:
    verdict = CriticVerdict(
        opportunity_id="opp_test",
        recommended_status="rejected",
        objections=[
            Objection(
                code="single_viral_source_dominant",
                statement="one post dominates",
                evidence_ids=[],
                fatal=True,
            )
        ],
        overclaim_check=OverclaimCheck(overclaims=False, statement=""),
        notes="",
    )
    result = judge_opportunity(_base_inputs(critic_verdict=verdict), THRESHOLDS)
    assert result.status == "rejected"


def test_non_fatal_objection_does_not_block_experiment_ready() -> None:
    verdict = CriticVerdict(
        opportunity_id="opp_test",
        recommended_status="experiment_ready",
        objections=[
            Objection(
                code="implementation_cost_dominates",
                statement="expensive to build",
                evidence_ids=[],
                fatal=False,
            )
        ],
        overclaim_check=OverclaimCheck(overclaims=False, statement=""),
        notes="",
    )
    result = judge_opportunity(_base_inputs(critic_verdict=verdict), THRESHOLDS)
    assert result.status == "experiment_ready"


def test_unique_authors_below_minimum_yields_investigate() -> None:
    result = judge_opportunity(_base_inputs(unique_authors=2), THRESHOLDS)
    assert result.status == "investigate"
    assert any("unique_authors" in r for r in result.reasons)


def test_source_families_below_minimum_yields_investigate() -> None:
    result = judge_opportunity(_base_inputs(source_families=1), THRESHOLDS)
    assert result.status == "investigate"


def test_no_pain_evidence_yields_investigate() -> None:
    result = judge_opportunity(_base_inputs(problem_evidence_count=0), THRESHOLDS)
    assert result.status == "investigate"


def test_no_workaround_evidence_yields_investigate() -> None:
    result = judge_opportunity(_base_inputs(workaround_evidence_count=0), THRESHOLDS)
    assert result.status == "investigate"


def test_dominant_single_duplicate_group_yields_investigate() -> None:
    result = judge_opportunity(_base_inputs(dominant_single_duplicate_group=True), THRESHOLDS)
    assert result.status == "investigate"
    assert any("duplicate group" in r for r in result.reasons)


def test_critic_recommends_rejected_is_honored() -> None:
    result = judge_opportunity(
        _base_inputs(critic_verdict=_passing_verdict("rejected")), THRESHOLDS
    )
    assert result.status == "rejected"


def test_critic_recommends_investigate_is_honored_even_with_good_volume() -> None:
    result = judge_opportunity(
        _base_inputs(critic_verdict=_passing_verdict("investigate")), THRESHOLDS
    )
    assert result.status == "investigate"


def test_externally_validated_is_not_a_reachable_status() -> None:
    """No combination of inputs can produce externally_validated -- it isn't
    even in JudgeResult's possible outputs, by construction (there is no
    code path that assigns it)."""
    import inspect

    source = inspect.getsource(judge_opportunity)
    assert "externally_validated" not in source


def test_thresholds_are_read_from_argument_not_hardcoded() -> None:
    lenient = AcceptanceThresholds(
        minimum_unique_authors=1,
        minimum_source_families=1,
        minimum_problem_evidence=1,
        minimum_workaround_evidence=0,
    )
    result = judge_opportunity(_base_inputs(unique_authors=1, source_families=1), lenient)
    assert result.status == "experiment_ready"
