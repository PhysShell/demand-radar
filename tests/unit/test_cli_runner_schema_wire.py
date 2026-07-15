"""Regression coverage for docs/decisions.log.md's "real bug found by the
real smoke test" entry: `claude --json-schema` rejects a schema carrying a
`$schema` meta-key. `strip_dollar_schema` (agents/base.py) is the fix;
these tests pin it against the real schema files (not a synthetic dict) so
a future schema-authoring change can't silently reintroduce `$schema` onto
the wire. subprocess.run is monkeypatched -- neither test touches a real
`claude`/`codex` binary.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from demand_radar.agents import claude_cli, codex_cli
from demand_radar.agents.base import READ_ONLY_DATA_PROFILE, strip_dollar_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFICATION_SCHEMA = REPO_ROOT / "schemas" / "classification.schema.json"


def test_strip_dollar_schema_removes_only_dollar_schema() -> None:
    original = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://schemas.demand-radar.dev/x/1",
            "type": "object",
        }
    )
    stripped = json.loads(strip_dollar_schema(original))
    assert "$schema" not in stripped
    assert stripped["$id"] == "https://schemas.demand-radar.dev/x/1"
    assert stripped["type"] == "object"


def test_strip_dollar_schema_is_a_noop_without_the_key() -> None:
    original = json.dumps({"type": "object", "properties": {}})
    assert json.loads(strip_dollar_schema(original)) == {"type": "object", "properties": {}}


def test_real_schema_files_declare_dollar_schema() -> None:
    """Sanity check that this regression test is actually exercising the
    failure condition -- if schema authoring ever drops `$schema`, this
    (and the live bug) stop being relevant and this assertion should be
    revisited, not silently left green for the wrong reason."""
    schema = json.loads(CLASSIFICATION_SCHEMA.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


class _FakeCompletedProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_claude_runner_strips_dollar_schema_from_json_schema_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_argv: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        captured_argv.extend(argv)
        envelope = json.dumps({"result": json.dumps({"acknowledged": True})})
        return _FakeCompletedProcess(stdout=envelope)

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    runner = claude_cli.ClaudeCodeRunner()
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=CLASSIFICATION_SCHEMA,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )

    assert result.status == "PASS"
    schema_flag_index = captured_argv.index("--json-schema")
    sent_schema = json.loads(captured_argv[schema_flag_index + 1])
    assert "$schema" not in sent_schema
    assert "$id" in sent_schema  # only $schema is stripped, not the whole meta-key family


def test_codex_runner_writes_stripped_schema_copy_not_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_argv: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        captured_argv.extend(argv)
        return _FakeCompletedProcess(stdout=json.dumps({"acknowledged": True}))

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    runner = codex_cli.CodexCliRunner()
    run_dir = tmp_path / "run"
    runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=CLASSIFICATION_SCHEMA,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=run_dir,
    )

    schema_flag_index = captured_argv.index("--output-schema")
    sent_schema_path = Path(captured_argv[schema_flag_index + 1])
    assert sent_schema_path != CLASSIFICATION_SCHEMA
    assert sent_schema_path.parent == run_dir
    sent_schema = json.loads(sent_schema_path.read_text(encoding="utf-8"))
    assert "$schema" not in sent_schema

    # the real schema file on disk is never mutated
    original_schema = json.loads(CLASSIFICATION_SCHEMA.read_text(encoding="utf-8"))
    assert original_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_claude_runner_reports_not_installed_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    runner = claude_cli.ClaudeCodeRunner()
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=CLASSIFICATION_SCHEMA,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "BLOCKED_NOT_INSTALLED"


def test_codex_runner_reports_timeout_as_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    runner = codex_cli.CodexCliRunner()
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=CLASSIFICATION_SCHEMA,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "BLOCKED_TIMEOUT"
