"""cli.py's runner-construction plumbing: get_runner's two independent
axes (engine: fake | claude | codex; runner/transport: o7) and
_refuse_unverified_profiles's capability-profile gate -- exercised
directly against the functions rather than through typer's CliRunner,
since neither depends on option parsing. CLI-level (CliRunner) coverage of
the same refusal composed with real argument parsing lives in
tests/integration/test_cli.py and test_review_workflow.py.
"""

from __future__ import annotations

import pytest
import typer

from demand_radar.agents.fake import FakeRunner
from demand_radar.agents.o7_invoke import O7InvokeRunner
from demand_radar.cli import RUNNER_FACTORIES, _refuse_unverified_profiles, get_runner


def test_get_runner_fake_engine_ignores_runner_axis() -> None:
    """The in-process fake is its own transport -- any --runner value at
    all (even one that isn't a real registered transport) is accepted."""
    assert isinstance(get_runner("fake", "o7"), FakeRunner)
    assert isinstance(get_runner("fake", "not-a-real-transport"), FakeRunner)


def test_get_runner_default_o7_maps_claude_and_codex_to_o7invokerunner() -> None:
    claude_runner = get_runner("claude", "o7")
    assert isinstance(claude_runner, O7InvokeRunner)
    assert claude_runner.engine == "claude"

    codex_runner = get_runner("codex", "o7")
    assert isinstance(codex_runner, O7InvokeRunner)
    assert codex_runner.engine == "codex"


def test_get_runner_unknown_runner_name_exits_1() -> None:
    with pytest.raises(typer.Exit) as exc_info:
        get_runner("claude", "not-a-real-transport")
    assert exc_info.value.exit_code == 1


def test_get_runner_unknown_engine_exits_1() -> None:
    """ "human" is a legal --critic value but not a get_runner() engine --
    only cli.py::run's own "critic == human -> NeverCalledRunner" special
    case handles it; calling get_runner directly with it hits the same
    "unknown engine" path --analyst human would."""
    with pytest.raises(typer.Exit) as exc_info:
        get_runner("human", "o7")
    assert exc_info.value.exit_code == 1


def test_runner_factories_registry_has_o7() -> None:
    assert "o7" in RUNNER_FACTORIES
    assert isinstance(RUNNER_FACTORIES["o7"]("claude"), O7InvokeRunner)


def test_refuse_unverified_profiles_passes_for_claude_and_fake() -> None:
    # Must not raise for either -- both verified_profiles() include
    # read-only-data.
    _refuse_unverified_profiles(
        [
            ("analyst", "claude", O7InvokeRunner(engine="claude")),
            ("critic", "fake", FakeRunner()),
        ],
        runner="o7",
    )


def test_refuse_unverified_profiles_refuses_codex() -> None:
    with pytest.raises(typer.Exit) as exc_info:
        _refuse_unverified_profiles(
            [("critic", "codex", O7InvokeRunner(engine="codex"))], runner="o7"
        )
    assert exc_info.value.exit_code == 2


def test_refuse_unverified_profiles_message_names_role_engine_and_runner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit):
        _refuse_unverified_profiles(
            [("analyst", "codex", O7InvokeRunner(engine="codex"))], runner="o7"
        )
    stderr = capsys.readouterr().err
    assert "unverified_capability_profile" in stderr
    assert "analyst engine 'codex'" in stderr
    assert "runner 'o7'" in stderr
