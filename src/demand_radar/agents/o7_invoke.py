"""Runner backed by `o7 invoke` (007's read-only, schema-bound single-shot
agent primitive — `007/src/invoke.rs`) instead of shelling out to `claude`/
`codex` directly. Supersedes `claude_cli.py`/`codex_cli.py`: 007 now owns the
one place that knows `claude`'s/`codex`'s actual non-interactive flags, the
`$schema`-stripping fix, and the closed-world capability profile; this module
only knows how to shell out to `o7` and translate its `meta.json` into this
project's own `AgentResult` shape. See `docs/o7-invoke.md` for the migration
record and the cross-repo conformance gate this runner must pass.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from demand_radar.agents.base import AgentRunner, hash_input_paths, sha256_text
from demand_radar.models import AgentResult


def _parse_epoch_tag(value: str) -> datetime:
    """`o7 invoke`'s `meta.json` timestamps are `"epoch:<seconds>"`, not
    RFC3339 (007/src/invoke.rs::now_epoch_tag -- no chrono dependency for one
    timestamp). Parsed here rather than asking 007 to grow a date-formatting
    dependency for a field this runner can parse in one line.
    """
    prefix = "epoch:"
    if not value.startswith(prefix):
        msg = f"unrecognized o7 invoke timestamp shape: {value!r}"
        raise ValueError(msg)
    return datetime.fromtimestamp(int(value[len(prefix) :]), tz=UTC)


class O7InvokeRunner(AgentRunner):
    """`engine` selects which backend `o7 invoke` itself calls (`claude` |
    `codex`); this class never talks to `claude`/`codex` directly and holds
    no closed-world flag knowledge of its own -- that lives entirely in `o7`.
    """

    def __init__(
        self,
        *,
        engine: str,
        o7_binary: str = "o7",
        model: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.engine = engine
        self.o7_binary = o7_binary
        self.model = model
        self.timeout_seconds = timeout_seconds

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
        del task_id  # part of the shared contract; o7 invoke takes no task id
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt_hash = sha256_text(prompt)
        input_hashes = hash_input_paths(input_paths)
        started_at = datetime.now(UTC)

        prompt_file = run_dir / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        argv = [
            self.o7_binary,
            "invoke",
            "--engine",
            self.engine,
            "--prompt-file",
            str(prompt_file),
            "--schema",
            str(output_schema),
            "--capability-profile",
            capability_profile,
            "--out",
            str(run_dir / "o7-out"),
            "--timeout-secs",
            str(int(self.timeout_seconds)),
        ]
        if input_paths:
            manifest_path = run_dir / "input-manifest.json"
            manifest_path.write_text(
                json.dumps({"input_paths": [str(p) for p in input_paths]}), encoding="utf-8"
            )
            argv += ["--input-manifest", str(manifest_path)]
        if self.model:
            argv += ["--model", self.model]

        def blocked(*, status: str, error_kind: str, stdout: str, stderr: str) -> AgentResult:
            stdout_path = run_dir / "stdout.log"
            stderr_path = run_dir / "stderr.log"
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            return AgentResult(
                provider=f"o7-invoke-{self.engine}",
                command_version=None,
                model=self.model,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                exit_code=None,
                status=status,  # type: ignore[arg-type]
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                structured_output_path=None,
                schema_valid=False,
                prompt_hash=prompt_hash,
                input_hashes=input_hashes,
                error_kind=error_kind,
            )

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds + 10,
            )
        except FileNotFoundError:
            return blocked(
                status="BLOCKED_NOT_INSTALLED",
                error_kind="o7_not_installed",
                stdout="",
                stderr=f"{self.o7_binary} not found on PATH\n",
            )
        except subprocess.TimeoutExpired as exc:
            return blocked(
                status="BLOCKED_TIMEOUT",
                error_kind="timeout",
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
            )

        # `o7 invoke` exits 1 on every non-PASS status, not just a crash --
        # its own meta.json (not the exit code) is the source of truth. Only
        # a handful of pre-artifact validation failures (bad --engine, schema
        # file missing, unknown --capability-profile) exit before writing
        # anything at all; those are caller bugs in this class's own argv
        # construction, not a normal agent-call outcome, so they are
        # classified distinctly rather than guessed at.
        meta_path = run_dir / "o7-out" / "meta.json"
        if not meta_path.is_file():
            return blocked(
                status="FAIL_INVALID_OUTPUT",
                error_kind="o7_invoke_no_meta_json",
                stdout=proc.stdout,
                stderr=proc.stderr,
            )

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return AgentResult(
            provider=meta["provider"],
            command_version=meta.get("command_version"),
            model=meta.get("model"),
            started_at=_parse_epoch_tag(meta["started_at"]),
            finished_at=_parse_epoch_tag(meta["finished_at"]),
            exit_code=meta.get("exit_code"),
            status=meta["status"],
            stdout_path=meta["stdout_path"],
            stderr_path=meta["stderr_path"],
            structured_output_path=meta.get("structured_output_path"),
            schema_valid=meta["schema_valid"],
            prompt_hash=meta["prompt_hash"],
            input_hashes=meta.get("input_hashes", []),
            error_kind=meta.get("error_kind"),
        )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
