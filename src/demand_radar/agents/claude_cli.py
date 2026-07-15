"""Claude Code CLI adapter — spec section 13.1. Subscription-backed CLI
execution inspired by 007's `judge` backend (docs/decisions.log.md): closed
world (`--tools ""`, `--strict-mcp-config`, no `--mcp-config`, empty
`--setting-sources` so no project CLAUDE.md/hooks/permissions get picked up
implicitly), prompt via stdin rather than argv (spec's own
docs/security-layers.md rationale: argv is world-readable via /proc,
shell history, and has a size limit), and no Anthropic API key -- this
shells out to the already-authenticated `claude` binary, exactly as a human
running Claude Code would, and never reads its credential storage directly.

Flags verified against `claude --help` (v2.1.210) in this environment, per
spec section 13.1's instruction to check current flags before implementing
-- see docs/decisions.log.md for the one flag combination that could only
be checked this way rather than against `codex --help` (agents/codex_cli.py).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from demand_radar.agents.base import (
    AgentRunner,
    hash_input_paths,
    sha256_text,
    strip_dollar_schema,
)
from demand_radar.models import AgentResult

_AUTH_FAILURE_MARKERS = (
    "not logged in",
    "please log in",
    "please run `claude login`",
    "/login",
    "authentication",
    "unauthorized",
    "no active session",
)
_USAGE_LIMIT_MARKERS = (
    "usage limit",
    "rate limit",
    "quota",
    "exceeded your",
    "upgrade your plan",
    "resets at",
)


class ClaudeCodeRunner(AgentRunner):
    def __init__(
        self,
        *,
        binary: str = "claude",
        model: str = "sonnet",
        timeout_seconds: float = 120.0,
        max_budget_usd: float = 0.50,
    ) -> None:
        self.binary = binary
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_budget_usd = max_budget_usd
        self._version = self._detect_version()

    def _detect_version(self) -> str | None:
        try:
            proc = subprocess.run(
                [self.binary, "--version"], capture_output=True, text=True, timeout=10
            )
            return proc.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def run(
        self,
        *,
        task_id: str,
        prompt: str,
        input_paths: Sequence[Path],
        output_schema: Path,
        capability_profile: str,
        run_dir: Path,
    ) -> AgentResult:
        del task_id, capability_profile  # not needed by this provider; part of the shared contract
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / "stdout.jsonl"
        stderr_path = run_dir / "stderr.log"
        structured_output_path = run_dir / "result.json"
        started_at = datetime.now(UTC)
        prompt_hash = sha256_text(prompt)
        input_hashes = hash_input_paths(input_paths)
        schema_text = strip_dollar_schema(output_schema.read_text(encoding="utf-8"))

        argv = [
            self.binary,
            "-p",
            "--output-format",
            "json",
            "--input-format",
            "text",
            "--json-schema",
            schema_text,
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--model",
            self.model,
            "--max-budget-usd",
            str(self.max_budget_usd),
        ]

        def finish(
            *, status: str, schema_valid: bool, error_kind: str | None, exit_code: int | None
        ) -> AgentResult:
            return AgentResult(
                provider="claude-cli",
                command_version=self._version,
                model=self.model,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                exit_code=exit_code,
                status=status,  # type: ignore[arg-type]
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                structured_output_path=str(structured_output_path) if schema_valid else None,
                schema_valid=schema_valid,
                prompt_hash=prompt_hash,
                input_hashes=input_hashes,
                error_kind=error_kind,
            )

        try:
            proc = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=run_dir,
            )
        except FileNotFoundError:
            stderr_path.write_text(f"{self.binary} not found on PATH\n")
            stdout_path.write_text("")
            return finish(
                status="BLOCKED_NOT_INSTALLED",
                schema_valid=False,
                error_kind="not_installed",
                exit_code=None,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(_as_text(exc.stdout))
            stderr_path.write_text(_as_text(exc.stderr))
            return finish(
                status="BLOCKED_TIMEOUT", schema_valid=False, error_kind="timeout", exit_code=None
            )

        stdout_path.write_text(proc.stdout)
        stderr_path.write_text(proc.stderr)
        combined_lower = (proc.stdout + proc.stderr).lower()

        if proc.returncode != 0:
            if any(marker in combined_lower for marker in _AUTH_FAILURE_MARKERS):
                return finish(
                    status="BLOCKED_AUTH",
                    schema_valid=False,
                    error_kind="auth",
                    exit_code=proc.returncode,
                )
            if any(marker in combined_lower for marker in _USAGE_LIMIT_MARKERS):
                return finish(
                    status="BLOCKED_USAGE",
                    schema_valid=False,
                    error_kind="usage_limit",
                    exit_code=proc.returncode,
                )
            return finish(
                status="FAIL_INVALID_OUTPUT",
                schema_valid=False,
                error_kind="nonzero_exit",
                exit_code=proc.returncode,
            )

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return finish(
                status="FAIL_INVALID_OUTPUT",
                schema_valid=False,
                error_kind="invalid_json",
                exit_code=proc.returncode,
            )

        # --output-format json wraps the model's answer in a result envelope;
        # `result` holds the actual (already schema-constrained) JSON text.
        structured_text = envelope.get("result") if isinstance(envelope, dict) else None
        if not isinstance(structured_text, str):
            return finish(
                status="FAIL_INVALID_OUTPUT",
                schema_valid=False,
                error_kind="unexpected_envelope",
                exit_code=proc.returncode,
            )
        try:
            structured = json.loads(structured_text)
        except json.JSONDecodeError:
            return finish(
                status="FAIL_INVALID_OUTPUT",
                schema_valid=False,
                error_kind="invalid_json",
                exit_code=proc.returncode,
            )

        structured_output_path.write_text(json.dumps(structured, indent=2), encoding="utf-8")
        return finish(status="PASS", schema_valid=True, error_kind=None, exit_code=proc.returncode)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
