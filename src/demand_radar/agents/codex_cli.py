"""Codex CLI adapter — spec section 13.2.

IMPORTANT PROVENANCE NOTE (see docs/decisions.log.md): `codex` is not
installed in the environment this MVP was built in, so these flags could
not be checked against `codex --help` directly as the spec instructs.
They were verified instead against public OpenAI documentation and
cross-referenced against 007's own `judge/README.md`, which already runs
`codex --provider codex` in production. Re-verify against `codex --help`
before the first real use on a machine that has it installed --
`docs/trust-boundaries.md` and the smoke-agents command both surface this
as a residual risk rather than assuming it works.

Flags used: `codex -a never exec - --json --output-schema <file>
-s read-only -c features.shell_tool=false --skip-git-repo-check`
(approval policy is a *global* flag and must precede `exec`). No
OPENAI_API_KEY/CODEX_API_KEY is read or set -- both are explicitly
stripped from the subprocess environment so a stray key elsewhere in the
environment can never silently substitute for the ChatGPT subscription
login this is meant to exercise.

Residual risk carried over from 007 (docs/security-layers.md): `-s
read-only` denies writes but, unlike claude's `--tools ""`, does not
disable network -- codex has no one-flag network-off equivalent. Prefer
the claude runner for untrusted-content extraction/critique when a choice
exists; this adapter is still closed to writes and has no shell tool
(`features.shell_tool=false`).
"""

from __future__ import annotations

import json
import os
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
    "please run",
    "login",
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


class CodexCliRunner(AgentRunner):
    def __init__(self, *, binary: str = "codex", timeout_seconds: float = 120.0) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self._version = self._detect_version()

    def _detect_version(self) -> str | None:
        try:
            proc = subprocess.run(
                [self.binary, "--version"], capture_output=True, text=True, timeout=10
            )
            return proc.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _clean_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        return env

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
        del task_id, capability_profile
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / "stdout.jsonl"
        stderr_path = run_dir / "stderr.log"
        structured_output_path = run_dir / "result.json"
        started_at = datetime.now(UTC)
        prompt_hash = sha256_text(prompt)
        input_hashes = hash_input_paths(input_paths)

        # Written into run_dir rather than passed as the original schema file
        # path -- see strip_dollar_schema's docstring (agents/base.py) for why.
        stripped_schema_path = run_dir / "output-schema.json"
        stripped_schema_path.write_text(
            strip_dollar_schema(output_schema.read_text(encoding="utf-8")), encoding="utf-8"
        )

        argv = [
            self.binary,
            "-a",
            "never",  # approval policy is global, must precede `exec`
            "exec",
            "-",  # read the prompt from stdin
            "--json",
            "--output-schema",
            str(stripped_schema_path),
            "-s",
            "read-only",
            "-c",
            "features.shell_tool=false",
            "--skip-git-repo-check",
        ]

        def finish(
            *, status: str, schema_valid: bool, error_kind: str | None, exit_code: int | None
        ) -> AgentResult:
            return AgentResult(
                provider="codex-cli",
                command_version=self._version,
                model=None,  # not pinned -- see module docstring
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
                env=self._clean_env(),
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

        structured = _extract_last_schema_conforming_json(proc.stdout, output_schema)
        if structured is None:
            return finish(
                status="FAIL_INVALID_OUTPUT",
                schema_valid=False,
                error_kind="invalid_json",
                exit_code=proc.returncode,
            )

        structured_output_path.write_text(json.dumps(structured, indent=2), encoding="utf-8")
        return finish(status="PASS", schema_valid=True, error_kind=None, exit_code=proc.returncode)


def _extract_last_schema_conforming_json(stdout: str, output_schema: Path) -> dict[str, Any] | None:
    """`--json` emits one JSON object per line (progress events plus the
    final answer); take the *last* line that both parses and matches the
    requested schema. Best-effort and unverified against the real CLI (see
    module docstring) -- a known codex-cli issue forces intermediate
    progress messages into the schema too, which this cannot fully
    distinguish from the true final answer without a verified event-type
    field to key on.
    """
    import jsonschema

    schema = json.loads(output_schema.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    candidates: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and validator.is_valid(parsed):
            candidates.append(parsed)
    return candidates[-1] if candidates else None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
