"""`demand-radar smoke-agents` — spec section 24. Not run in CI; exercises
the real, already-authenticated `claude`/`codex` CLIs (via `o7 invoke` --
see agents/o7_invoke.py and docs/o7-invoke.md) with one minimal schema-bound
prompt each, no repo files, no network research (closed-world by
construction, enforced by 007, not this project). One provider's outcome
never substitutes for another's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from demand_radar.agents.base import READ_ONLY_DATA_PROFILE, persist_call_artifacts
from demand_radar.agents.o7_invoke import O7InvokeRunner

SMOKE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["acknowledged"],
    "properties": {"acknowledged": {"type": "boolean"}},
}

SMOKE_PROMPT = (
    "You have no tools. Respond with exactly one JSON object matching the "
    'provided schema and nothing else: {"acknowledged": true}'
)

# spec section 24's status vocabulary (distinct from AgentResult's own
# BLOCKED_TIMEOUT -- this command speaks the vocabulary the spec names here).
_STATUS_MAP = {
    "PASS": "PASS",
    "BLOCKED_AUTH": "BLOCKED_AUTH",
    "BLOCKED_USAGE": "BLOCKED_USAGE",
    "BLOCKED_NOT_INSTALLED": "BLOCKED_NOT_INSTALLED",
    "BLOCKED_TIMEOUT": "FAIL_TIMEOUT",
    "FAIL_INVALID_OUTPUT": "FAIL_INVALID_OUTPUT",
    "FAIL_SCHEMA": "FAIL_INVALID_OUTPUT",
    "NOT_RUN": "FAIL_INVALID_OUTPUT",
}


@dataclass
class SmokeOutcome:
    status: str
    detail: str = ""


def run_smoke_agents(*, runs_dir: Path, schemas_dir: Path) -> dict[str, SmokeOutcome]:
    del schemas_dir  # smoke uses its own minimal schema, not the pipeline's
    run_dir = runs_dir / "_smoke" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    schema_path = run_dir / "smoke.schema.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(json.dumps(SMOKE_SCHEMA, indent=2), encoding="utf-8")

    results: dict[str, SmokeOutcome] = {}
    for provider_name, runner in (
        ("claude", O7InvokeRunner(engine="claude")),
        ("codex", O7InvokeRunner(engine="codex")),
    ):
        call_dir = run_dir / provider_name
        task_id = f"smoke:{provider_name}"
        result = runner.run(
            task_id=task_id,
            prompt=SMOKE_PROMPT,
            input_paths=[],
            output_schema=schema_path,
            capability_profile=READ_ONLY_DATA_PROFILE,
            run_dir=call_dir,
        )
        persist_call_artifacts(call_dir, task_id, SMOKE_PROMPT, result)
        mapped = _STATUS_MAP.get(result.status, "FAIL_INVALID_OUTPUT")
        results[provider_name] = SmokeOutcome(status=mapped, detail=result.error_kind or "")

    return results
