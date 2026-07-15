"""`O7InvokeRunner` -- the runner backed by 007's `o7 invoke` primitive
(see docs/o7-invoke.md). `subprocess.run` is monkeypatched throughout: none
of these tests touch a real `o7`/`claude`/`codex` binary. Live coverage of
the actual subprocess call lives in 007's own `cargo test` (`invoke.rs`) and
the cross-repo conformance gate.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from demand_radar.agents import o7_invoke
from demand_radar.agents.base import READ_ONLY_DATA_PROFILE
from demand_radar.agents.o7_invoke import O7InvokeRunner, _parse_epoch_tag

SCHEMA_PATH_NAME = "schema.json"


def _schema_path(tmp_path: Path) -> Path:
    path = tmp_path / SCHEMA_PATH_NAME
    schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 1, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_parse_epoch_tag_roundtrips() -> None:
    dt = _parse_epoch_tag("epoch:1700000000")
    assert dt == datetime.fromtimestamp(1700000000, tz=UTC)


def test_parse_epoch_tag_rejects_unrecognized_shape() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        _parse_epoch_tag("2024-01-01T00:00:00Z")


def test_o7_not_installed_is_blocked_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(o7_invoke.subprocess, "run", fake_run)
    runner = O7InvokeRunner(engine="claude")
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "BLOCKED_NOT_INSTALLED"
    assert result.error_kind == "o7_not_installed"


def test_o7_timeout_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(o7_invoke.subprocess, "run", fake_run)
    runner = O7InvokeRunner(engine="codex")
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "BLOCKED_TIMEOUT"
    assert result.error_kind == "timeout"


def test_missing_meta_json_is_fail_invalid_output_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`o7 invoke` exits before writing any artifact for a handful of
    pre-call validation failures (bad --engine, unreadable --schema). This
    runner must degrade to a classified AgentResult, never raise."""

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=1, stdout="", stderr="unknown --capability-profile")

    monkeypatch.setattr(o7_invoke.subprocess, "run", fake_run)
    runner = O7InvokeRunner(engine="claude")
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )
    assert result.status == "FAIL_INVALID_OUTPUT"
    assert result.error_kind == "o7_invoke_no_meta_json"


def test_pass_meta_json_is_translated_into_agent_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_argv: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        captured_argv.extend(argv)
        out_dir = Path(argv[argv.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        meta = {
            "schema": 1,
            "provider": "claude-cli",
            "command_version": "2.1.210 (Claude Code)",
            "model": None,
            "started_at": "epoch:1700000000",
            "finished_at": "epoch:1700000005",
            "exit_code": 0,
            "status": "PASS",
            "stdout_path": str(out_dir / "stdout.raw"),
            "stderr_path": str(out_dir / "stderr.log"),
            "structured_output_path": str(out_dir / "result.json"),
            "schema_valid": True,
            "prompt_hash": "sha256:" + "a" * 64,
            "input_hashes": [],
        }
        (out_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(o7_invoke.subprocess, "run", fake_run)
    runner = O7InvokeRunner(engine="claude")
    result = runner.run(
        task_id="t",
        prompt="p",
        input_paths=[],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )

    assert result.status == "PASS"
    assert result.provider == "claude-cli"
    assert result.command_version == "2.1.210 (Claude Code)"
    assert result.schema_valid is True
    assert result.started_at == datetime.fromtimestamp(1700000000, tz=UTC)
    assert result.finished_at == datetime.fromtimestamp(1700000005, tz=UTC)

    assert "--engine" in captured_argv
    assert captured_argv[captured_argv.index("--engine") + 1] == "claude"
    assert "--capability-profile" in captured_argv
    assert captured_argv[captured_argv.index("--capability-profile") + 1] == READ_ONLY_DATA_PROFILE


def test_input_paths_are_written_as_manifest_and_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_argv: list[str] = []
    input_file = tmp_path / "input.txt"
    input_file.write_text("some input", encoding="utf-8")

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        captured_argv.extend(argv)
        return _FakeCompletedProcess(returncode=1)

    monkeypatch.setattr(o7_invoke.subprocess, "run", fake_run)
    runner = O7InvokeRunner(engine="claude")
    runner.run(
        task_id="t",
        prompt="p",
        input_paths=[input_file],
        output_schema=_schema_path(tmp_path),
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / "run",
    )

    assert "--input-manifest" in captured_argv
    manifest_path = Path(captured_argv[captured_argv.index("--input-manifest") + 1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {"input_paths": [str(input_file)]}
