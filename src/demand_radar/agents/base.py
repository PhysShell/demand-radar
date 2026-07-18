"""The agent runner contract — spec section 13. Every runner (fake, Claude
CLI, Codex CLI) implements this same Protocol so graph nodes never branch on
which provider is behind it. capability_profile is a named, pre-agreed
restriction bundle (this MVP defines exactly one: "read-only-data" — no
shell, no filesystem write outside run_dir, minimal/no network) — it is a
label the concrete runner maps to real CLI flags, not something a caller
can use to grant more access.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from demand_radar.models import AgentResult

READ_ONLY_DATA_PROFILE = "read-only-data"

# spec section 12.2: "maximum retries: 2; retry только для transient errors;
# malformed structured output не является transient error." BLOCKED_TIMEOUT
# is the one transient status here; everything else (auth/usage/invalid
# output/schema) is either systemic (caller should stop, not retry) or a
# content problem no retry fixes.
_TRANSIENT_STATUSES = {"BLOCKED_TIMEOUT"}


@runtime_checkable
class AgentRunner(Protocol):
    def run(
        self,
        *,
        task_id: str,
        prompt: str,
        input_paths: Sequence[Path],
        output_schema: Path,
        capability_profile: str,
        run_dir: Path,
    ) -> AgentResult: ...

    def verified_profiles(self) -> frozenset[str]:
        """The set of capability-profile names this runner can PROVABLY
        enforce -- live-verified against a real install/behavior, not
        merely vendor-documented or asserted in a flag's name. A caller
        (`cli.py::run`) must refuse to route untrusted content (evidence
        text) through a runner whose verified set does not contain the
        requested `capability_profile`; see docs/trust-boundaries.md and
        docs/runner-contract.md. Documentation of what a flag is *supposed*
        to do is not verification -- only an observed, adversarial smoke
        test against a live binary earns a profile a place in this set.
        """
        ...


class NeverCalledRunner:
    """An `AgentRunner` whose `.run()` always raises. Used wherever a role
    must structurally never invoke an agent -- `--critic human` (the human
    review deferral mode: critic_review skips the loop entirely, but this
    still backs `RunContext.critic_runner` so an accidental future call
    fails loudly instead of silently producing a bogus verdict) and
    `demand-radar finalize` (deterministic_judge/render_report/verify_run
    never call an agent; this is the "crash on any call" double proving
    that in both production and tests, not just tests).
    """

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
        raise AssertionError(
            f"NeverCalledRunner.run() invoked for task_id={task_id!r} -- this role must "
            f"never call an agent"
        )

    def verified_profiles(self) -> frozenset[str]:
        # Vacuously safe: run() above always raises, so this runner can
        # never actually execute anything against any capability profile --
        # there is nothing here that could ever leak untrusted content
        # anywhere. Reporting the full profile is honest, not optimistic:
        # "provably enforces read-only-data" is trivially true of code that
        # provably never runs at all.
        return frozenset({READ_ONLY_DATA_PROFILE})


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def hash_input_paths(input_paths: Sequence[Path]) -> list[str]:
    return [sha256_file(p) for p in input_paths]


def call_agent_with_retry(
    runner: AgentRunner,
    *,
    task_id: str,
    prompt: str,
    input_paths: Sequence[Path],
    output_schema: Path,
    capability_profile: str,
    run_dir: Path,
    max_retries: int = 2,
) -> AgentResult:
    """Same task_id on every attempt (so a fixture/fake scenario keyed by
    task_id matches regardless of retry count); each attempt gets its own
    subdirectory so artifacts from a failed attempt are never overwritten.
    """
    attempt = 1
    while True:
        result = runner.run(
            task_id=task_id,
            prompt=prompt,
            input_paths=input_paths,
            output_schema=output_schema,
            capability_profile=capability_profile,
            run_dir=run_dir / f"attempt-{attempt}",
        )
        if result.status not in _TRANSIENT_STATUSES or attempt > max_retries:
            return result
        attempt += 1


def persist_call_artifacts(run_dir: Path, task_id: str, prompt: str, result: AgentResult) -> None:
    """meta.json + prompt.txt alongside the runner's own stdout/stderr/result
    files -- one full record per agent call, spec section 20's agents/ tree."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (run_dir / "meta.json").write_text(
        json.dumps(
            {"task_id": task_id, **result.model_dump(mode="json")}, indent=2, sort_keys=True
        ),
        encoding="utf-8",
    )
