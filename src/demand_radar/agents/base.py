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
