"""Agent output acceptance criteria -- spec section 23.3, exercised through
FakeRunner so no test here depends on real Claude/Codex authentication."""

from __future__ import annotations

import json
from pathlib import Path

from demand_radar.agents.base import READ_ONLY_DATA_PROFILE
from demand_radar.agents.fake import FakeRunner, FakeScenario

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["evidence_id", "value"],
    "properties": {
        "evidence_id": {"type": "string", "pattern": "^ev_"},
        "value": {"type": "number"},
    },
}


def _schema_path(tmp_path: Path) -> Path:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(SCHEMA), encoding="utf-8")
    return path


def test_valid_output_passes_schema(tmp_path: Path) -> None:
    runner = FakeRunner(
        scenarios={"t": FakeScenario(kind="success", output={"evidence_id": "ev_1", "value": 1.0})}
    )
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "PASS"
    assert result.schema_valid is True


def test_malformed_json_fails(tmp_path: Path) -> None:
    runner = FakeRunner(scenarios={"t": FakeScenario(kind="malformed_json")})
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "FAIL_INVALID_OUTPUT"
    assert result.schema_valid is False


def test_unknown_evidence_id_shape_fails_schema(tmp_path: Path) -> None:
    """A response missing the required evidence_id field (as if it invented
    an unrecognized shape) fails schema validation."""
    runner = FakeRunner(
        scenarios={"t": FakeScenario(kind="schema_violation", output={"value": 1.0})}
    )
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "FAIL_SCHEMA"


def test_agent_auth_error_is_blocked_not_a_silent_empty_success(tmp_path: Path) -> None:
    runner = FakeRunner(scenarios={"t": FakeScenario(kind="auth_failure")})
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "BLOCKED_AUTH"
    assert result.status != "PASS"


def test_usage_limit_is_blocked(tmp_path: Path) -> None:
    runner = FakeRunner(scenarios={"t": FakeScenario(kind="usage_limit")})
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "BLOCKED_USAGE"


def test_timeout_kills_and_reports_blocked(tmp_path: Path) -> None:
    runner = FakeRunner(scenarios={"t": FakeScenario(kind="timeout")})
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "BLOCKED_TIMEOUT"
    assert result.exit_code is None


def test_result_carries_prompt_and_input_hashes(tmp_path: Path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("some input", encoding="utf-8")
    runner = FakeRunner(
        scenarios={"t": FakeScenario(kind="success", output={"evidence_id": "ev_1", "value": 1.0})}
    )
    result = runner.run(
        task_id="t",
        prompt="the prompt text",
        input_paths=[input_file],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.prompt_hash.startswith("sha256:")
    assert len(result.input_hashes) == 1


def test_stdout_and_stderr_saved_separately(tmp_path: Path) -> None:
    runner = FakeRunner(scenarios={"t": FakeScenario(kind="auth_failure")})
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert Path(result.stdout_path).exists()
    assert Path(result.stderr_path).exists()
    assert result.stdout_path != result.stderr_path
