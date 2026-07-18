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
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from demand_radar.agents.base import (
    READ_ONLY_DATA_PROFILE,
    AgentRunner,
    hash_input_paths,
    sha256_text,
)
from demand_radar.models import AgentResult

# `o7 --version` prints `o7 0.1.0` (007/src/main.rs). Pre-1.0 semver has no
# stability contract across minor versions -- a 0.2.0 `o7` is free to change
# `invoke`'s argv shape, meta.json fields, or the capability-profile mapping
# this whole runner depends on, same as a major bump would be for a >=1.0
# tool. So this pins the 0.1.x line specifically rather than accepting any
# 0.y: bump this constant only after re-reading the new version's CHANGELOG
# and re-verifying this runner's assumptions against it.
SUPPORTED_O7_VERSION_LINE = (0, 1)


@dataclass
class _O7VersionProbeOutcome:
    """Cached result of the one `o7 --version` probe an O7InvokeRunner
    instance ever makes -- see O7InvokeRunner._check_o7_version."""

    ok: bool
    error_kind: str | None = None
    raw_output: str = ""
    found_version: str | None = None


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
        # Populated by _check_o7_version() on the first run() call and
        # reused for the lifetime of this instance thereafter -- see that
        # method's docstring for why a probe failure is cached too, not
        # just a successful one.
        self._version_probe: _O7VersionProbeOutcome | None = None

    def verified_profiles(self) -> frozenset[str]:
        """Claude: `o7 invoke`'s claude path passes `--tools ""`, which
        removes the tool surface structurally (not just by policy) --
        live-verified in this environment (docs/trust-boundaries.md,
        `demand-radar smoke-agents`'s `claude: PASS` run). Any other engine
        (currently just "codex"): `o7 invoke`'s codex path relies on
        `--sandbox read-only` (documented by 007 to deny writes, not
        disable network) plus an *unverified* `-c features.shell_tool=false`
        -- neither flag has ever been exercised against a real `codex`
        binary, because codex is not installed anywhere either this MVP or
        007's `invoke.rs` was built. Whether `features.shell_tool=false`
        actually removes the tool (Claude's structural guarantee) or merely
        restricts what it can do inside the sandbox (a policy constraint,
        not a removal) has never been observed. This returns frozenset() for
        codex until a live install + adversarial smoke test (a real
        prompt-injection payload attempting the command-execution/
        exfiltration path this profile is supposed to close) confirms the
        flag does what it claims -- not just that it is present in the
        argv. See docs/trust-boundaries.md's "Codex is refused..." section
        for the full history of this refusal.
        """
        if self.engine == "claude":
            return frozenset({READ_ONLY_DATA_PROFILE})
        return frozenset()

    def _probe_o7_version_uncached(self) -> _O7VersionProbeOutcome:
        """The actual `o7 --version` subprocess call -- only ever invoked
        once per instance, by _check_o7_version()."""
        try:
            proc = subprocess.run(
                [self.o7_binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            return _O7VersionProbeOutcome(ok=False, error_kind="o7_not_installed")
        except subprocess.TimeoutExpired:
            # Not one of the three outcomes spec'd for this probe (not
            # installed / unparsable / wrong version), but it is a variant
            # of "unparsable" in effect: no version string was ever
            # obtained. Folded into o7_version_probe_failed rather than
            # inventing a fourth error_kind for a probe-level timeout.
            return _O7VersionProbeOutcome(
                ok=False,
                error_kind="o7_version_probe_failed",
                raw_output="<o7 --version timed out after 10s>",
            )

        raw = (proc.stdout or "").strip()
        match = re.fullmatch(r"o7 (\d+)\.(\d+)\.(\d+)", raw)
        if match is None:
            return _O7VersionProbeOutcome(
                ok=False,
                error_kind="o7_version_probe_failed",
                raw_output=raw or (proc.stderr or "").strip(),
            )
        major, minor, patch = (int(group) for group in match.groups())
        if (major, minor) != SUPPORTED_O7_VERSION_LINE:
            return _O7VersionProbeOutcome(
                ok=False,
                error_kind="o7_version_unsupported",
                found_version=f"{major}.{minor}.{patch}",
            )
        return _O7VersionProbeOutcome(ok=True)

    def _check_o7_version(self, blocked: Callable[..., AgentResult]) -> AgentResult | None:
        """Runs the version handshake on the first run() call and caches
        whatever it finds -- success or failure alike -- on self for this
        instance's whole lifetime. A more elaborate policy would retry a
        transient probe failure on a later run() call while keeping a
        confirmed-bad version pinned; this deliberately does neither and
        probes exactly once per instance instead. Simpler, and just as
        honest: a wrong/missing `o7` binary is not going to fix itself
        between two calls made moments apart in the same pipeline run, so
        there is nothing a second probe would learn.

        Returns None if `o7` is a supported version (run() should proceed);
        otherwise returns the already-classified AgentResult for this call.
        """
        if self._version_probe is None:
            self._version_probe = self._probe_o7_version_uncached()
        outcome = self._version_probe
        if outcome.ok:
            return None

        if outcome.error_kind == "o7_not_installed":
            return blocked(
                status="BLOCKED_NOT_INSTALLED",
                error_kind="o7_not_installed",
                stdout="",
                stderr=f"{self.o7_binary} not found on PATH\n",
            )
        if outcome.error_kind == "o7_version_probe_failed":
            return blocked(
                status="BLOCKED_NOT_INSTALLED",
                error_kind="o7_version_probe_failed",
                stdout="",
                stderr=(
                    f"could not parse `{self.o7_binary} --version` output as "
                    f"'o7 X.Y.Z': {outcome.raw_output!r}\n"
                ),
            )
        # o7_version_unsupported: reusing BLOCKED_NOT_INSTALLED here (rather
        # than adding a new AgentRunStatus) is deliberate -- a wrong-generation
        # `o7` binary is "not (usably) installed" from this runner's point of
        # view, same taxonomy bucket as a missing binary; error_kind is what
        # actually carries the distinction for anyone inspecting meta.json.
        supported = ".".join(str(part) for part in SUPPORTED_O7_VERSION_LINE) + ".x"
        found = outcome.found_version or "unknown"
        return blocked(
            status="BLOCKED_NOT_INSTALLED",
            error_kind="o7_version_unsupported",
            stdout="",
            stderr=(
                f"o7 version {found} is not supported by this runner "
                f"(supports {supported}); see SUPPORTED_O7_VERSION_LINE\n"
            ),
        )

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

        version_block = self._check_o7_version(blocked)
        if version_block is not None:
            return version_block

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
