"""The generic `AgentRunner` contract -- docs/runner-contract.md, "Conformance":
"Pass tests/contract/test_runner_contract.py -- the generic suite every
runner adapter (present or future) must satisfy." Every property tested
below is asserted about the *contract*, not about any one implementation's
internals -- a future `--runner direct`/`--runner api` adapter is expected
to be dropped into `_build_subject` below and pass unchanged, the same way
`FakeRunner` and `O7InvokeRunner` do today. Implementation-specific
behavior (retry-count edge cases for `O7InvokeRunner`'s own argv
construction, `FakeRunner`'s per-scenario stdout content, and so on)
belongs in tests/unit/test_o7_invoke_runner.py / test_fake_runner.py /
test_agents_base.py, not here -- this file must not duplicate those.

Every subject constructed here is fully offline: `FakeRunner` never spawns
a subprocess at all, and every `O7InvokeRunner` subject is built with a
binary path that either does not exist on disk (a genuine, un-mocked
`FileNotFoundError` from a real `subprocess.run` call -- no mocking
required for "not installed") or is monkeypatched to a fixed, fake
`--version` reply for a binary name distinct enough that a real `o7`
happening to be on the test host's PATH is never at risk of being invoked.
The one exception is `test_live_o7_claude_subject_satisfies_the_contract`,
gated behind `O7_CONTRACT_LIVE=1` and never run by scripts/check.sh or CI.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import jsonschema
import pytest

from demand_radar.agents import o7_invoke
from demand_radar.agents.base import (
    READ_ONLY_DATA_PROFILE,
    AgentRunner,
    call_agent_with_retry,
)
from demand_radar.agents.fake import FakeRunner, FakeScenario
from demand_radar.agents.o7_invoke import O7InvokeRunner
from demand_radar.models import AgentResult, AgentRunStatus

TAXONOMY_STATUSES = frozenset(get_args(AgentRunStatus))
assert len(TAXONOMY_STATUSES) == 8, "AgentRunStatus grew/shrank -- update this suite's assumptions"

# --------------------------------------------------------------------------
# Shared fixtures every subject's run() call is exercised against. One
# schema/prompt/input triple, reused identically across every subject: the
# properties under test (hash recomputation, schema validation, run_dir
# containment) don't depend on subject-specific content, and sharing one
# fixture set keeps every property test symmetrical across subjects instead
# of accidentally testing a different thing for each one.
# --------------------------------------------------------------------------

SCHEMA_FILENAME = "schema.json"
INPUT_FILENAME = "input.txt"
RUN_DIRNAME = "run"

CONTRACT_TASK_ID = "contract-task"
CONTRACT_PROMPT = "contract-suite prompt text -- hashed here, never sent to a real model.\n"
CONTRACT_INPUT_CONTENT = "contract-suite input fixture -- hashed only, not read into the prompt.\n"
CONTRACT_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["acknowledged"],
    "properties": {"acknowledged": {"type": "boolean"}},
}
CONTRACT_SUCCESS_OUTPUT: dict[str, object] = {"acknowledged": True}

# A path that cannot exist -- real subprocess.run() raises a genuine,
# un-mocked FileNotFoundError against this, exactly what a caller sees when
# `o7` truly is not installed. No monkeypatching needed for the
# not-installed subjects; that's what "fully offline" means here.
NONEXISTENT_O7_BINARY = "/nonexistent/o7-contract-test"

# A binary name distinct enough it will never collide with a real `o7` that
# might happen to be on the test host's PATH -- only *this exact* argv[0] is
# intercepted by _patch_unsupported_o7_version below; everything else falls
# through to the real subprocess.run.
FAKE_UNSUPPORTED_O7_BINARY = "o7-contract-test-unsupported-version"


def _write_schema(tmp_path: Path) -> Path:
    path = tmp_path / SCHEMA_FILENAME
    path.write_text(json.dumps(CONTRACT_SCHEMA), encoding="utf-8")
    return path


def _write_input_file(tmp_path: Path) -> Path:
    path = tmp_path / INPUT_FILENAME
    path.write_text(CONTRACT_INPUT_CONTENT, encoding="utf-8")
    return path


def _independent_sha256_text(text: str) -> str:
    """Deliberately re-implemented here rather than imported from
    agents/base.py::sha256_text -- the entire point of the `prompt_hash`
    contract property (docs/runner-contract.md) is that a caller with no
    special knowledge of a runner's internals can recompute the identical
    hash from the prompt string alone. Importing the function under test
    would make this check a tautology instead of an independent check.
    """
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _independent_sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _assert_path_contained(path_str: str, run_dir: Path) -> None:
    resolved = Path(path_str).resolve()
    resolved_run_dir = run_dir.resolve()
    assert resolved == resolved_run_dir or resolved_run_dir in resolved.parents, (
        f"{path_str} resolves outside its own run_dir {run_dir} -- a runner must write only "
        f"inside the run_dir it was given and never outside it (docs/runner-contract.md, "
        f"'run_dir')"
    )


def _patch_unsupported_o7_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes `<FAKE_UNSUPPORTED_O7_BINARY> --version` return `"o7 0.9.9"` --
    a real, parseable version string, just on the wrong minor line
    (`SUPPORTED_O7_VERSION_LINE` is `(0, 1)`) -- without a real `o7` binary
    anywhere in the picture. Patches the real `subprocess.run` (via
    `o7_invoke.subprocess`, which *is* the same module object `subprocess`
    itself is -- matching how tests/unit/test_o7_invoke_runner.py patches
    it) but only intercepts calls whose argv[0] is this exact fake binary
    name; any other call falls through to the genuine `subprocess.run`, so
    this cannot accidentally swallow some other subject's real
    FileNotFoundError-against-a-nonexistent-path behavior.
    """
    real_run = subprocess.run

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == FAKE_UNSUPPORTED_O7_BINARY and argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="o7 0.9.9\n", stderr="")
        return real_run(argv, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(o7_invoke.subprocess, "run", fake_run)


# --------------------------------------------------------------------------
# ContractSubject: one (offline-constructible runner, expected outcome)
# pairing every property test below is parametrized over.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractSubject:
    """`make_runner` is a zero-arg factory, not a pre-built runner instance:
    a couple of the tests below (task_id retry stability, the
    version-probe-is-cached-per-instance determinism check) specifically
    need to control whether they get a *fresh* runner per call or reuse
    *one* instance across calls, and a factory lets each test choose
    instead of every subject silently sharing one runner's mutable state
    (e.g. `O7InvokeRunner._version_probe`) across unrelated test functions.
    """

    id: str
    make_runner: Callable[[], AgentRunner]
    # The exact status set reachable by calling run() once, offline, on
    # this subject -- every subject here is constructed to be
    # single-outcome and deterministic, so this is a singleton set in
    # practice, but a set (not a scalar) so a future subject legitimately
    # reachable via more than one offline outcome doesn't need a shape
    # change here.
    expected_statuses: frozenset[str]
    # verified_profiles() is a static property of engine/backend choice,
    # not of run() succeeding or failing -- every subject below has a
    # definite expected value regardless of its expected_statuses.
    expected_verified_profiles: frozenset[str]


def _build_subject(kind: str, monkeypatch: pytest.MonkeyPatch) -> ContractSubject:
    if kind == "fake_success":
        return ContractSubject(
            id=kind,
            make_runner=lambda: FakeRunner(
                scenarios={
                    CONTRACT_TASK_ID: FakeScenario(kind="success", output=CONTRACT_SUCCESS_OUTPUT)
                }
            ),
            expected_statuses=frozenset({"PASS"}),
            expected_verified_profiles=frozenset({READ_ONLY_DATA_PROFILE}),
        )
    if kind == "fake_timeout":
        return ContractSubject(
            id=kind,
            make_runner=lambda: FakeRunner(
                scenarios={CONTRACT_TASK_ID: FakeScenario(kind="timeout")}
            ),
            expected_statuses=frozenset({"BLOCKED_TIMEOUT"}),
            expected_verified_profiles=frozenset({READ_ONLY_DATA_PROFILE}),
        )
    if kind == "fake_auth_failure":
        return ContractSubject(
            id=kind,
            make_runner=lambda: FakeRunner(
                scenarios={CONTRACT_TASK_ID: FakeScenario(kind="auth_failure")}
            ),
            expected_statuses=frozenset({"BLOCKED_AUTH"}),
            expected_verified_profiles=frozenset({READ_ONLY_DATA_PROFILE}),
        )
    if kind == "fake_usage_limit":
        return ContractSubject(
            id=kind,
            make_runner=lambda: FakeRunner(
                scenarios={CONTRACT_TASK_ID: FakeScenario(kind="usage_limit")}
            ),
            expected_statuses=frozenset({"BLOCKED_USAGE"}),
            expected_verified_profiles=frozenset({READ_ONLY_DATA_PROFILE}),
        )
    if kind == "fake_malformed_json":
        return ContractSubject(
            id=kind,
            make_runner=lambda: FakeRunner(
                scenarios={CONTRACT_TASK_ID: FakeScenario(kind="malformed_json")}
            ),
            expected_statuses=frozenset({"FAIL_INVALID_OUTPUT"}),
            expected_verified_profiles=frozenset({READ_ONLY_DATA_PROFILE}),
        )
    if kind == "fake_schema_violation":
        return ContractSubject(
            id=kind,
            make_runner=lambda: FakeRunner(
                scenarios={CONTRACT_TASK_ID: FakeScenario(kind="schema_violation")}
            ),
            expected_statuses=frozenset({"FAIL_SCHEMA"}),
            expected_verified_profiles=frozenset({READ_ONLY_DATA_PROFILE}),
        )
    if kind == "o7_not_installed_claude":
        return ContractSubject(
            id=kind,
            make_runner=lambda: O7InvokeRunner(engine="claude", o7_binary=NONEXISTENT_O7_BINARY),
            expected_statuses=frozenset({"BLOCKED_NOT_INSTALLED"}),
            expected_verified_profiles=frozenset({READ_ONLY_DATA_PROFILE}),
        )
    if kind == "o7_not_installed_codex":
        # engine="codex" precisely so the verified_profiles property test
        # below exercises the "must NOT contain read-only-data" side of the
        # contract (docs/runner-contract.md's capability-profiles section)
        # via the same generic machinery as every other subject, rather
        # than as a one-off special case.
        return ContractSubject(
            id=kind,
            make_runner=lambda: O7InvokeRunner(engine="codex", o7_binary=NONEXISTENT_O7_BINARY),
            expected_statuses=frozenset({"BLOCKED_NOT_INSTALLED"}),
            expected_verified_profiles=frozenset(),
        )
    if kind == "o7_version_unsupported":
        _patch_unsupported_o7_version(monkeypatch)
        return ContractSubject(
            id=kind,
            make_runner=lambda: O7InvokeRunner(
                engine="claude", o7_binary=FAKE_UNSUPPORTED_O7_BINARY
            ),
            expected_statuses=frozenset({"BLOCKED_NOT_INSTALLED"}),
            expected_verified_profiles=frozenset({READ_ONLY_DATA_PROFILE}),
        )
    raise ValueError(f"unknown contract subject kind: {kind!r}")  # pragma: no cover


SUBJECT_KINDS = (
    "fake_success",
    "fake_timeout",
    "fake_auth_failure",
    "fake_usage_limit",
    "fake_malformed_json",
    "fake_schema_violation",
    "o7_not_installed_claude",
    "o7_not_installed_codex",
    "o7_version_unsupported",
)


@pytest.fixture(params=SUBJECT_KINDS)
def subject(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> ContractSubject:
    """Parametrizes every test below over SUBJECT_KINDS. `monkeypatch` is
    threaded through from this fixture (not accessed directly by each test
    function) so the o7_version_unsupported subject's patch is applied for
    exactly the duration of the one test invocation currently using it,
    same lifetime any other monkeypatch use would get.
    """
    kind = request.param
    assert isinstance(kind, str)
    return _build_subject(kind, monkeypatch)


def _call(subject: ContractSubject, tmp_path: Path) -> AgentResult:
    schema_path = _write_schema(tmp_path)
    input_path = _write_input_file(tmp_path)
    runner = subject.make_runner()
    return runner.run(
        task_id=CONTRACT_TASK_ID,
        prompt=CONTRACT_PROMPT,
        input_paths=[input_path],
        output_schema=schema_path,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / RUN_DIRNAME,
    )


# --------------------------------------------------------------------------
# Contract properties -- one test function per property, parametrized over
# every subject via the `subject` fixture above.
# --------------------------------------------------------------------------


def test_run_returns_an_agent_result_instance(subject: ContractSubject, tmp_path: Path) -> None:
    result = _call(subject, tmp_path)
    assert isinstance(result, AgentResult)


def test_status_is_one_of_the_eight_taxonomy_values(
    subject: ContractSubject, tmp_path: Path
) -> None:
    result = _call(subject, tmp_path)
    assert result.status in TAXONOMY_STATUSES
    assert result.status in subject.expected_statuses, (
        f"{subject.id}: got status {result.status!r}, expected one of "
        f"{sorted(subject.expected_statuses)} -- every subject in this suite is constructed to "
        f"be deterministic offline, so a mismatch here means the subject construction (or the "
        f"runner under test) drifted, not that this assertion is flaky"
    )


def test_prompt_hash_is_independently_recomputable(
    subject: ContractSubject, tmp_path: Path
) -> None:
    result = _call(subject, tmp_path)
    assert result.prompt_hash == _independent_sha256_text(CONTRACT_PROMPT)


def test_input_hashes_are_independently_recomputable(
    subject: ContractSubject, tmp_path: Path
) -> None:
    result = _call(subject, tmp_path)
    assert result.input_hashes == [_independent_sha256_file(tmp_path / INPUT_FILENAME)]


def test_schema_valid_true_only_if_structured_output_actually_validates(
    subject: ContractSubject, tmp_path: Path
) -> None:
    """schema_valid=False is the correct outcome for most subjects here
    (everything but fake_success) -- nothing to independently verify in
    that case, so this test's only real assertion fires for fake_success.
    The point is structural: schema_valid=True must never be taken on the
    runner's own word (docs/runner-contract.md's structured_output_path
    row) -- this reruns jsonschema.validate() against actual bytes on disk
    itself, not against anything the runner computed.
    """
    result = _call(subject, tmp_path)
    if not result.schema_valid:
        pytest.skip(f"{subject.id}: schema_valid is False for this subject -- nothing to verify")
    assert result.structured_output_path is not None
    structured_path = Path(result.structured_output_path)
    assert structured_path.is_file()
    payload = json.loads(structured_path.read_text(encoding="utf-8"))
    jsonschema.validate(payload, CONTRACT_SCHEMA)


def test_stdout_and_stderr_paths_are_contained_within_run_dir(
    subject: ContractSubject, tmp_path: Path
) -> None:
    """Containment (docs/runner-contract.md's `run_dir` row: a runner "must
    write only inside it, and must never write outside it") holds
    unconditionally for both paths, for every subject, with no exceptions.
    Existence is asserted unconditionally too: this suite's first run caught
    FakeRunner's success/malformed_json/schema_violation branches reporting
    a stderr_path they never wrote (fixed in fake.py by creating the file
    empty up front, before any scenario branch runs).
    """
    result = _call(subject, tmp_path)
    run_dir = tmp_path / RUN_DIRNAME
    _assert_path_contained(result.stdout_path, run_dir)
    _assert_path_contained(result.stderr_path, run_dir)

    assert Path(result.stdout_path).is_file(), f"{subject.id}: stdout_path does not exist on disk"
    assert Path(result.stderr_path).is_file(), f"{subject.id}: stderr_path does not exist on disk"


def test_verified_profiles_returns_the_expected_frozenset(subject: ContractSubject) -> None:
    runner = subject.make_runner()
    profiles = runner.verified_profiles()
    assert isinstance(profiles, frozenset)
    assert all(isinstance(p, str) for p in profiles)
    assert profiles == subject.expected_verified_profiles, (
        f"{subject.id}: verified_profiles() == {profiles!r}, expected "
        f"{subject.expected_verified_profiles!r}"
    )


# --------------------------------------------------------------------------
# task_id retry stability (docs/runner-contract.md's `task_id` row):
# call_agent_with_retry passes the SAME task_id on every attempt of one
# logical call, so a FakeRunner scenario keyed by task_id (fake.py:
# `self.scenarios.get(task_id, self.default)`) must fire identically no
# matter which attempt number is currently in flight.
# --------------------------------------------------------------------------


class _RecordingRunner:
    """Wraps an AgentRunner and records the task_id/run_dir of every run()
    call it forwards -- lets the test below observe what
    call_agent_with_retry actually passed on each attempt, which the
    returned AgentResult alone cannot reveal.
    """

    def __init__(self, inner: AgentRunner) -> None:
        self._inner = inner
        self.seen_task_ids: list[str] = []

    def run(
        self,
        *,
        task_id: str,
        prompt: str,
        input_paths: list[Path],
        output_schema: Path,
        capability_profile: str,
        run_dir: Path,
    ) -> AgentResult:
        self.seen_task_ids.append(task_id)
        return self._inner.run(
            task_id=task_id,
            prompt=prompt,
            input_paths=input_paths,
            output_schema=output_schema,
            capability_profile=capability_profile,
            run_dir=run_dir,
        )

    def verified_profiles(self) -> frozenset[str]:
        return self._inner.verified_profiles()


def test_call_agent_with_retry_keeps_task_id_stable_across_attempts(tmp_path: Path) -> None:
    """BLOCKED_TIMEOUT is the one transient status (agents/base.py::
    _TRANSIENT_STATUSES), so scripting it here is what forces
    call_agent_with_retry to actually retry (max_retries=2, so 3 attempts
    total) instead of returning after attempt 1. If FakeRunner (or a
    future runner) ever derived behavior from attempt count rather than
    from task_id + inputs, this is the test that would catch it: the
    scenario is keyed by CONTRACT_TASK_ID only, so it firing identically
    on attempts 1, 2, and 3 is only possible if the same task_id is really
    being passed every time.
    """
    schema_path = _write_schema(tmp_path)
    input_path = _write_input_file(tmp_path)
    fake = FakeRunner(scenarios={CONTRACT_TASK_ID: FakeScenario(kind="timeout")})
    recorder = _RecordingRunner(fake)
    run_dir_root = tmp_path / "retry-run"

    result = call_agent_with_retry(
        recorder,
        task_id=CONTRACT_TASK_ID,
        prompt=CONTRACT_PROMPT,
        input_paths=[input_path],
        output_schema=schema_path,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=run_dir_root,
        max_retries=2,
    )

    assert result.status == "BLOCKED_TIMEOUT"  # still transient after retries are exhausted
    assert recorder.seen_task_ids == [CONTRACT_TASK_ID] * 3  # attempt 1 + 2 retries
    for attempt in (1, 2, 3):
        attempt_dir = run_dir_root / f"attempt-{attempt}"
        assert attempt_dir.is_dir(), f"attempt-{attempt} subdirectory was never created"


# --------------------------------------------------------------------------
# Determinism of failure classification: calling run() twice on the SAME
# O7InvokeRunner instance must produce the same status and error_kind both
# times. The interesting mechanism is the version probe -- O7InvokeRunner
# probes `o7 --version` only once per instance and caches the outcome
# (o7_invoke.py::_check_o7_version), so the second call must short-circuit
# on the cached _version_probe rather than re-probing.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", ["o7_not_installed_claude", "o7_not_installed_codex", "o7_version_unsupported"]
)
def test_o7_failure_classification_is_deterministic_across_repeated_calls(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _build_subject(kind, monkeypatch).make_runner()
    schema_path = _write_schema(tmp_path)
    input_path = _write_input_file(tmp_path)

    def call(n: int) -> AgentResult:
        return runner.run(
            task_id=CONTRACT_TASK_ID,
            prompt=CONTRACT_PROMPT,
            input_paths=[input_path],
            output_schema=schema_path,
            capability_profile=READ_ONLY_DATA_PROFILE,
            run_dir=tmp_path / f"run-{n}",  # fresh run_dir per call, same runner instance
        )

    first = call(1)
    second = call(2)

    assert first.status == second.status == "BLOCKED_NOT_INSTALLED"
    assert first.error_kind == second.error_kind


# --------------------------------------------------------------------------
# Optional live subject (e): O7InvokeRunner(engine="claude") against a real,
# installed `o7` binary and an authenticated `claude` CLI subscription.
# Gated behind O7_CONTRACT_LIVE=1 -- never set by scripts/check.sh or CI, so
# this never runs unattended and never becomes a source of flaky offline
# failures. scripts/o7_conformance_gate.py is the *other* live check (o7
# invoke direct vs. wrapped agreement); this one instead confirms the live
# reference runner still satisfies the same general contract shape the
# offline subjects above prove generically.
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("O7_CONTRACT_LIVE") != "1",
    reason="live O7InvokeRunner(engine='claude') subject -- requires a real o7 binary on PATH "
    "and an authenticated claude CLI subscription. Opt in explicitly with O7_CONTRACT_LIVE=1; "
    "never set in CI or scripts/check.sh -- see docs/runner-contract.md.",
)
def test_live_o7_claude_subject_satisfies_the_contract(tmp_path: Path) -> None:
    runner = O7InvokeRunner(engine="claude")
    schema_path = _write_schema(tmp_path)
    input_path = _write_input_file(tmp_path)

    result = runner.run(
        task_id=CONTRACT_TASK_ID,
        prompt=CONTRACT_PROMPT,
        input_paths=[input_path],
        output_schema=schema_path,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp_path / RUN_DIRNAME,
    )

    assert isinstance(result, AgentResult)
    assert result.status in TAXONOMY_STATUSES
    assert result.prompt_hash == _independent_sha256_text(CONTRACT_PROMPT)
    assert result.input_hashes == [_independent_sha256_file(input_path)]
    assert runner.verified_profiles() == frozenset({READ_ONLY_DATA_PROFILE})
