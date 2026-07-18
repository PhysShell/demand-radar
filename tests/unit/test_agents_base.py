"""agents/base.py -- the parts not already covered incidentally through
test_fake_runner.py/test_o7_invoke_runner.py: NeverCalledRunner's
verified_profiles() and the AgentRunner Protocol's verified_profiles
member itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from demand_radar.agents.base import READ_ONLY_DATA_PROFILE, AgentRunner, NeverCalledRunner


def test_never_called_runner_verified_profiles_is_vacuously_full() -> None:
    """run() always raises, so NeverCalledRunner can never actually route
    anything anywhere -- reporting the full profile set is honest, not
    optimistic, about a runner that provably never executes at all."""
    runner = NeverCalledRunner()
    assert runner.verified_profiles() == frozenset({READ_ONLY_DATA_PROFILE})


def test_never_called_runner_still_raises_on_run() -> None:
    """verified_profiles() being vacuously full must not be mistaken for
    run() becoming safe to call -- it still always raises."""
    runner = NeverCalledRunner()
    with pytest.raises(AssertionError, match="must never call an agent"):
        runner.run(
            task_id="t",
            prompt="p",
            input_paths=[],
            output_schema=Path("unused.json"),
            capability_profile=READ_ONLY_DATA_PROFILE,
            run_dir=Path("unused-dir"),
        )


def test_never_called_runner_satisfies_the_agent_runner_protocol() -> None:
    # runtime_checkable Protocol -- isinstance() checks for the presence of
    # both members (run, verified_profiles), not their signatures.
    assert isinstance(NeverCalledRunner(), AgentRunner)
