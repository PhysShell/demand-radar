"""FakeRunner — mandatory per spec section 13.3. Every offline/CI test path
uses this; nothing in the test suite may depend on real Claude/Codex
authentication or spend real subscription usage.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import jsonschema

from demand_radar.agents.base import READ_ONLY_DATA_PROFILE, hash_input_paths, sha256_text
from demand_radar.models import AgentResult

ScenarioKind = Literal[
    "success", "timeout", "auth_failure", "usage_limit", "malformed_json", "schema_violation"
]


@dataclass
class FakeScenario:
    kind: ScenarioKind = "success"
    output: dict[str, object] | None = None


class FakeRunner:
    """Deterministic stand-in for O7InvokeRunner.

    `scenarios` maps task_id -> FakeScenario for tests that need specific,
    per-call behavior (e.g. "the critic call for opp_007 times out"); any
    task_id not listed falls back to `default` (success with an empty object
    unless given an explicit output).
    """

    def __init__(
        self,
        provider_name: str = "fake",
        scenarios: dict[str, FakeScenario] | None = None,
        default: FakeScenario | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.scenarios = scenarios or {}
        self.default = default if default is not None else FakeScenario(kind="success", output={})

    def verified_profiles(self) -> frozenset[str]:
        # Never spawns a subprocess and never reads outside run_dir -- every
        # scenario below only writes fixed/scripted bytes into run_dir and
        # returns a canned AgentResult, so the read-only-data guarantee is
        # true of this class by construction, not by verification against a
        # real backend (there is no real backend here to verify against).
        return frozenset({READ_ONLY_DATA_PROFILE})

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
        run_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(UTC)
        stdout_path = run_dir / "stdout.jsonl"
        stderr_path = run_dir / "stderr.log"
        prompt_hash = sha256_text(prompt)
        input_hashes = hash_input_paths(input_paths)
        scenario = self.scenarios.get(task_id, self.default)

        def finish(
            *,
            status: str,
            structured_output_path: Path | None,
            schema_valid: bool,
            error_kind: str | None,
            exit_code: int | None,
        ) -> AgentResult:
            return AgentResult(
                provider=self.provider_name,
                command_version="fake/0.1.0",
                model="fake-model",
                started_at=started_at,
                finished_at=datetime.now(UTC),
                exit_code=exit_code,
                status=status,  # type: ignore[arg-type]
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                structured_output_path=str(structured_output_path)
                if structured_output_path
                else None,
                schema_valid=schema_valid,
                prompt_hash=prompt_hash,
                input_hashes=input_hashes,
                error_kind=error_kind,
            )

        if scenario.kind == "timeout":
            stderr_path.write_text("simulated timeout: agent process killed after deadline\n")
            stdout_path.write_text("")
            return finish(
                status="BLOCKED_TIMEOUT",
                structured_output_path=None,
                schema_valid=False,
                error_kind="timeout",
                exit_code=None,
            )

        if scenario.kind == "auth_failure":
            stderr_path.write_text("simulated auth failure: not logged in\n")
            stdout_path.write_text("")
            return finish(
                status="BLOCKED_AUTH",
                structured_output_path=None,
                schema_valid=False,
                error_kind="auth",
                exit_code=1,
            )

        if scenario.kind == "usage_limit":
            stderr_path.write_text("simulated usage-limit failure: subscription quota exhausted\n")
            stdout_path.write_text("")
            return finish(
                status="BLOCKED_USAGE",
                structured_output_path=None,
                schema_valid=False,
                error_kind="usage_limit",
                exit_code=1,
            )

        if scenario.kind == "malformed_json":
            broken = '{"not": "valid json"'  # deliberately truncated
            stdout_path.write_text(broken)
            return finish(
                status="FAIL_INVALID_OUTPUT",
                structured_output_path=None,
                schema_valid=False,
                error_kind="invalid_json",
                exit_code=0,
            )

        if scenario.kind == "schema_violation":
            bad_output = (
                scenario.output if scenario.output is not None else {"unexpected_field": True}
            )
            stdout_path.write_text(json.dumps(bad_output))
            structured_output_path = run_dir / "result.json"
            structured_output_path.write_text(json.dumps(bad_output, indent=2))
            return finish(
                status="FAIL_SCHEMA",
                structured_output_path=structured_output_path,
                schema_valid=False,
                error_kind="schema_violation",
                exit_code=0,
            )

        # "success"
        output = scenario.output if scenario.output is not None else {}
        stdout_path.write_text(json.dumps(output))
        structured_output_path = run_dir / "result.json"
        structured_output_path.write_text(json.dumps(output, indent=2))

        schema_valid = True
        try:
            schema = json.loads(output_schema.read_text(encoding="utf-8"))
            jsonschema.validate(output, schema)
        except (jsonschema.ValidationError, jsonschema.SchemaError, OSError, json.JSONDecodeError):
            schema_valid = False

        status = "PASS" if schema_valid else "FAIL_SCHEMA"
        return finish(
            status=status,
            structured_output_path=structured_output_path,
            schema_valid=schema_valid,
            error_kind=None if schema_valid else "schema_violation",
            exit_code=0,
        )
