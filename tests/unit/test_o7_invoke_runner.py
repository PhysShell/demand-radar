"""`O7InvokeRunner` -- the runner backed by 007's `o7 invoke` primitive
(see docs/o7-invoke.md). `subprocess.run` is monkeypatched throughout: none
of these tests touch a real `o7`/`claude`/`codex` binary. Live coverage of
the actual subprocess call lives in 007's own `cargo test` (`invoke.rs`) and
the cross-repo conformance gate.

Every real (non-version-handshake) test below must first satisfy the
`o7 --version` probe O7InvokeRunner now runs on its first `run()` call
(see the version-handshake section at the bottom of this file) before its
own scenario under test is reached -- `_version_ok_response`/
`_is_version_probe` are the shared helpers for that.
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
from demand_radar.agents.o7_invoke import (
    SUPPORTED_O7_VERSION_LINE,
    O7InvokeRunner,
    _parse_epoch_tag,
)

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


def _is_version_probe(argv: list[str]) -> bool:
    return argv[1:] == ["--version"]


def _version_ok_response() -> _FakeCompletedProcess:
    """A supported `o7 --version` reply -- SUPPORTED_O7_VERSION_LINE's own
    line, patch 0, matching the real `o7 0.1.0` this constant documents."""
    major, minor = SUPPORTED_O7_VERSION_LINE
    return _FakeCompletedProcess(returncode=0, stdout=f"o7 {major}.{minor}.0\n")


def test_parse_epoch_tag_roundtrips() -> None:
    dt = _parse_epoch_tag("epoch:1700000000")
    assert dt == datetime.fromtimestamp(1700000000, tz=UTC)


def test_parse_epoch_tag_rejects_unrecognized_shape() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        _parse_epoch_tag("2024-01-01T00:00:00Z")


def test_o7_not_installed_is_blocked_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FileNotFoundError at the *invoke* call (version probe already
    passed) still degrades to BLOCKED_NOT_INSTALLED, same as a
    FileNotFoundError at the probe itself would (see the version-handshake
    section below) -- the two are deliberately indistinguishable to a
    caller, since either way `o7` could not be run."""

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        if _is_version_probe(argv):
            return _version_ok_response()
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
        if _is_version_probe(argv):
            return _version_ok_response()
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
        if _is_version_probe(argv):
            return _version_ok_response()
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
        if _is_version_probe(argv):
            return _version_ok_response()
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
        if _is_version_probe(argv):
            return _version_ok_response()
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


# --- verified_profiles ---------------------------------------------------


def test_verified_profiles_claude_includes_read_only_data() -> None:
    """Claude's `--tools ""` is structural and live-verified -- see
    docs/trust-boundaries.md and O7InvokeRunner.verified_profiles's own
    docstring."""
    runner = O7InvokeRunner(engine="claude")
    assert runner.verified_profiles() == frozenset({READ_ONLY_DATA_PROFILE})


def test_verified_profiles_codex_is_empty() -> None:
    """Codex's closed-world flags have never been exercised against a live
    install -- verified_profiles() must not claim a profile it cannot
    prove, so this is empty until a live adversarial smoke test lifts it."""
    runner = O7InvokeRunner(engine="codex")
    assert runner.verified_profiles() == frozenset()


# --- o7 version handshake -------------------------------------------------


def test_version_handshake_rejects_unsupported_minor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        assert _is_version_probe(argv), "invoke must not be reached for an unsupported version"
        return _FakeCompletedProcess(returncode=0, stdout="o7 9.9.9\n")

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
    assert result.error_kind == "o7_version_unsupported"


def test_version_handshake_rejects_garbage_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        assert _is_version_probe(argv), "invoke must not be reached for unparsable output"
        return _FakeCompletedProcess(returncode=0, stdout="not a version string at all\n")

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
    assert result.error_kind == "o7_version_probe_failed"


def test_version_handshake_passes_and_probes_only_once_per_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A supported version lets run() proceed to the actual invoke call;
    across two run() calls on the same instance, the `--version` probe
    itself must fire exactly once."""
    version_probe_calls = 0
    invoke_calls = 0

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        nonlocal version_probe_calls, invoke_calls
        if _is_version_probe(argv):
            version_probe_calls += 1
            return _version_ok_response()
        invoke_calls += 1
        # No meta.json written -- FAIL_INVALID_OUTPUT is an acceptable,
        # already-covered outcome for the invoke call; this test only cares
        # that the invoke call is reached at all, and how many times the
        # version probe itself fires.
        return _FakeCompletedProcess(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(o7_invoke.subprocess, "run", fake_run)
    runner = O7InvokeRunner(engine="claude")

    for _ in range(2):
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

    assert version_probe_calls == 1
    assert invoke_calls == 2
