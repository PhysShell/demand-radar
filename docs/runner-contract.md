# The runner contract

This is the normative document for `AgentRunner` — the interface any agent
adapter must satisfy to be usable by `demand-radar run`. It exists because
the pipeline's actual runtime dependency is this contract, not any one
binary that happens to implement it. `o7` (the sibling `007` repo's
`o7 invoke` primitive, wrapped by `agents/o7_invoke.py::O7InvokeRunner`) is
the **default and reference implementation** — the one this project has
actually built and exercised against live CLIs — not the only legal one.
`--runner o7` is the default value of `demand-radar run`'s `--runner`
option precisely because it is a default, not a hardcoded assumption: a
`--runner direct` or `--runner api` adapter is a legitimate future
implementation of the same Protocol, registered the same way `o7` is.

This separates two axes that earlier revisions of this project conflated:

- **Engine** (`--analyst`/`--critic`: `fake` | `claude` | `codex`) — *which
  model* generates or critiques an opportunity card.
- **Runner** (`--runner`: `o7`, and in the future `direct`, `api`, ...) —
  *how the engine is actually invoked*: which subprocess/transport/binary
  turns a `(prompt, schema, capability_profile)` triple into an
  `AgentResult`.

A runner is chosen once per `demand-radar run` invocation and used for both
the analyst and critic engine selections; an engine name is meaningless
without a runner that knows how to run it. `cli.py::RUNNER_FACTORIES` is the
registry mapping a `--runner` name to a callable that constructs an
`AgentRunner` — adding a new runner means adding an entry there, not
branching inside `cli.py::run`.

## The Protocol

`agents/base.py::AgentRunner` (a `typing.Protocol`, `@runtime_checkable`):

```python
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
```

Parameter semantics:

- **`task_id`** — a caller-assigned identifier for one logical call (e.g.
  `classify:ev_0042` or `critic_review:opp_0007`). It is **stable across
  retries**: `call_agent_with_retry` (`agents/base.py`) passes the same
  `task_id` on every attempt of the same logical call, so a fixture or fake
  scenario keyed by `task_id` (`FakeRunner`) matches regardless of how many
  attempts it took. A runner must never derive identity, caching, or
  dedup behavior from anything *other* than `task_id` plus its inputs —
  in particular, never from wall-clock time or attempt count, both of
  which change between retries of the "same" call.
- **`run_dir`** — the directory this specific attempt owns. Ownership is
  split, not shared: the caller (`call_agent_with_retry`) is responsible
  for choosing a fresh `run_dir` per attempt (`run_dir / f"attempt-{n}"`),
  so a failed attempt's artifacts are never overwritten by a retry; the
  runner is responsible for everything *under* that directory once it
  receives it — the runner may create the directory if it does not exist,
  must write only inside it, and must never write outside it (that
  guarantee is part of what `read-only-data` means for the agent process
  itself, and part of what a caller relies on when composing multiple
  runners' output into one run tree). `persist_call_artifacts` (a separate,
  caller-side helper, not part of the runner's own obligation) additionally
  writes `prompt.txt` and `meta.json` alongside whatever the runner itself
  wrote — a runner does not need to duplicate those two files itself, but
  must not conflict with a caller writing them into the same `run_dir`.
- **`output_schema`** — a filesystem path to a JSON Schema document, **JSON
  Schema Draft 2020-12** (the draft this project's own
  `schemas/*.schema.json` files declare via `"$schema"`, and the draft
  `jsonschema.validator_for` resolves them as). A runner must constrain the
  engine's output to this schema by whatever mechanism its own transport
  supports (a native structured-output flag, a prompt-appended instruction
  plus independent re-validation, or both) — see "Capability profiles"
  below for why re-validation, not merely instruction, is required whenever
  the native mechanism isn't independently verified.
- **`capability_profile`** — one of a small, named set of capability-profile
  strings (currently exactly one: `read-only-data`, see below). It is a
  label the runner maps to its own concrete enforcement mechanism, never a
  request the caller can use to grant more access than the label implies.
  An unrecognized profile name must be refused outright, before any
  subprocess spawns, rather than silently falling back to some default
  posture (`o7 invoke`'s own behavior, inherited by `O7InvokeRunner`; any
  new runner must match it).
- **`prompt`** — the full prompt text, passed by value. Runners must not
  place prompt text on a spawned process's argv (world-readable via
  `/proc/*/cmdline` and shell history, and subject to an OS argv length
  limit that an evidence-laden prompt could hit) — pass it via stdin or a
  file the runner itself writes into `run_dir`, matching `O7InvokeRunner`'s
  and `o7 invoke`'s own choice (`docs/trust-boundaries.md`, "Subprocess").
- **`input_paths`** — auxiliary input files (may be empty) whose content
  the runner may make available to the engine call and must hash into
  `AgentResult.input_hashes`, in the same order.

## `AgentResult`: field-by-field normative semantics

`AgentResult` (`models.py`, a `StrictModel` — `extra="forbid"`) is the
entire contract surface a runner hands back. Every field below is
normative: a runner that cannot honestly populate a field must not
populate it with a plausible-looking placeholder.

| Field | Semantics |
| --- | --- |
| `provider` | Free-form string identifying the runner's own concrete backend (e.g. `"o7-invoke:claude"`). Not standardized across runners — callers must not pattern-match on it. |
| `command_version` | The version string of whatever binary the runner actually invoked, if the runner can determine it (e.g. `claude`'s own `--version` output, or `o7`'s own reported version). `None` if genuinely unknown — never a guessed or cached value from a previous run. |
| `model` | The model alias/name actually used, if applicable and known. `None` for engines with no model concept. |
| `started_at` / `finished_at` | Wall-clock bounds of the call as the runner observed them, not as the caller requested them — must reflect the actual subprocess/transport lifetime. |
| `exit_code` | The underlying process's exit code, if the runner's transport is process-based. `None` for a transport with no such concept (e.g. a future pure-API runner). |
| `status` | One of the 8 `AgentRunStatus` values — see the taxonomy below. This is the field callers branch on; it must be internally consistent with `schema_valid` and `exit_code` (a `PASS` with `schema_valid=false` is a contract violation, not a valid combination). |
| `stdout_path` / `stderr_path` | Paths (inside `run_dir`) to the raw stdout/stderr the runner's transport produced, even on failure — a debugging record, never omitted because the call failed. |
| `structured_output_path` | Path to the parsed structured output file, if any was produced. **`schema_valid=true` REQUIRES this field to be non-`None`, the file to exist, and its content to independently validate against `output_schema`** — a runner must not report `schema_valid=true` on the strength of the *engine's own* claim to have validated; the runner (or, for `O7InvokeRunner`'s case, the layer it trusts — see "Auth-agnosticism" below for what trust means here) must have actually run a validator against actual bytes on disk. |
| `schema_valid` | Boolean; see above. `false` whenever `structured_output_path` is `None`, missing, or fails validation. |
| `prompt_hash` | `sha256:<hex>` of the exact prompt **text** passed in, computed the same way `agents/base.py::sha256_text` does (UTF-8 encode, then SHA-256). Must be **independently recomputable**: a caller (or the conformance gate) hashing the same prompt string must get the identical value, regardless of which runner produced it. This is the property `scripts/o7_conformance_gate.py` checks by computing it a third time itself. |
| `input_hashes` | List of `sha256:<hex>`, one per element of `input_paths`, in the same order, computed the same way (`agents/base.py::sha256_file`) — also independently recomputable from the files on disk. |
| `error_kind` | Free-form machine-readable-ish string naming *why* a non-`PASS` status occurred (e.g. `o7_version_unsupported`, `not_installed`, `nonzero_exit`). Not an enum — new values are expected as new failure modes are discovered — but must be present whenever `status != "PASS"` and the runner has any specific cause to report; `None` only when the runner genuinely has nothing more specific than the `status` value itself. |

## Status taxonomy

`AgentRunStatus` (`models.py`) is exactly these 8 values. This table is
normative for every runner, not just `O7InvokeRunner`:

| Status | Meaning | Transient? | Caller behavior |
| --- | --- | --- | --- |
| `PASS` | Call succeeded; output produced and schema-valid. | — | Proceed with the structured output. |
| `BLOCKED_AUTH` | The engine's own authentication is missing/expired/rejected (e.g. an unauthenticated CLI subscription). | No | **Systemic** — the classify-loop/critic-loop this call was part of aborts rather than continuing to burn calls against a broken credential; surfaces as the run's overall `BLOCKED` verdict. |
| `BLOCKED_USAGE` | A usage/rate/quota limit was hit. | No | Systemic — same abort behavior as `BLOCKED_AUTH`; retrying immediately would just hit the same limit again. |
| `BLOCKED_TIMEOUT` | The call did not complete within its allotted time. | **Yes — the only transient status** | Retried, same `task_id`, up to `max_retries` (2) more attempts, each in its own `run_dir / attempt-N` subdirectory (`agents/base.py::call_agent_with_retry`, `_TRANSIENT_STATUSES = {"BLOCKED_TIMEOUT"}`). If still `BLOCKED_TIMEOUT` after retries are exhausted, treated as a terminal result for that call. |
| `BLOCKED_NOT_INSTALLED` | The underlying binary/engine is not present or not a supported version in this environment. | No | Systemic — same abort behavior; nothing about retrying changes whether the binary exists. |
| `FAIL_INVALID_OUTPUT` | The call completed but produced output that isn't even parseable as the expected shape (e.g. non-JSON, nonzero exit with no structured output). | **No — never retried** | Treated as a content/contract failure for that specific call, not a systemic condition; the loop records it and moves on (or fails that item), it does not abort the whole run the way `BLOCKED_*` does. |
| `FAIL_SCHEMA` | Output parsed but failed JSON Schema validation against `output_schema`. | No | Same as `FAIL_INVALID_OUTPUT` — a content failure, never retried; retrying an engine that produced a schema-violating answer without changing the prompt would just reproduce the same violation. |
| `NOT_RUN` | The call was never attempted (e.g. a role that is structurally skipped, such as `--critic human`). | — | Not a failure; downstream code must treat this as "no result to interpret," not as a `BLOCKED`/`FAIL` outcome. |

The `BLOCKED_*` family is grouped by cause (auth, usage, install, timeout)
rather than being a single generic `BLOCKED`, precisely so a caller (or a
human reading `verification.json`) can tell "this will never succeed
without operator intervention" (`BLOCKED_AUTH`/`BLOCKED_USAGE`/
`BLOCKED_NOT_INSTALLED`) apart from "this specific attempt was slow"
(`BLOCKED_TIMEOUT`) apart from "the content itself was wrong"
(`FAIL_INVALID_OUTPUT`/`FAIL_SCHEMA`) — three different remediations, not
one.

## Capability profiles and `verified_profiles()`

`READ_ONLY_DATA_PROFILE = "read-only-data"` (`agents/base.py`) is currently
the only defined capability profile. It means, concretely: **no shell
tool, no filesystem write outside `run_dir`, minimal/no network beyond the
engine's own model-API traffic, and output constrained to the given JSON
Schema.** It exists because Zone 2 of this pipeline (`classify`,
`generate_opportunities`, `critic_review` — see `docs/trust-boundaries.md`)
feeds untrusted, adversarial-by-assumption evidence text into a model call,
and the *only* thing standing between an embedded prompt-injection payload
and a real side effect is the absence of any tool through which that side
effect could occur.

`AgentRunner` additionally exposes:

```python
def verified_profiles(self) -> frozenset[str]: ...
```

the set of capability-profile names this runner can **provably** enforce —
"provably" meaning *live-exercised against a real install and observed to
hold*, not "documented by the vendor to hold." The CLI's refusal rule is
generic and simple: **a requested `capability_profile` must be a member of
`runner.verified_profiles()`, or the call is refused before any subprocess
spawns.** This replaces the earlier, hardcoded `if "codex" in (analyst,
critic): refuse` check in `cli.py::run` with the same outcome for today's
runner but a rule that generalizes to any future runner/engine pair without
new special-casing.

`O7InvokeRunner`'s `verified_profiles()` is engine-dependent, and the
distinction is the entire reason this method exists rather than being a
fixed constant:

- **`engine="claude"` → `{"read-only-data"}`.** Claude's `--tools ""` has
  been directly observed to remove the tool surface *structurally*, not by
  policy — confirmed against a live `claude` install in this environment
  (`docs/o7-invoke.md`, "Live verification").
- **`engine="codex"` → `frozenset()` (empty).** Not because codex is known
  to be unsafe, but because it has never been checked: `o7 invoke`'s codex
  path relies on `--sandbox read-only` plus `-c features.shell_tool=false`,
  and neither flag has ever been exercised against a real, installed
  `codex` binary in this project's build environment. This is the same
  distinction `docs/trust-boundaries.md` draws in detail — "verified" means
  a live adversarial smoke test observed the tool surface actually absent,
  not that a flag matching public documentation is present in the argv.
  Cross-referencing vendor documentation is how the flags were *chosen*;
  it is not how they get *verified*.

`FakeRunner` and `NeverCalledRunner` both return `{"read-only-data"}` —
correct for both: `FakeRunner` never spawns a subprocess or gives a model
any tool at all, and `NeverCalledRunner.run()` always raises before doing
anything, so no profile it could claim to support is ever actually
exercised in a way that could violate it.

The load-bearing rule for anyone adding a runner or an engine: **untrusted
evidence text must never be sent through a runner/engine combination whose
`verified_profiles()` does not contain the profile the call site requested
(`read-only-data`, for every current Zone 2 caller).** Do not widen
`verified_profiles()` for an engine until it has actually been exercised
live, the same way `codex` has not been.

## Auth-agnosticism

The reference `o7` runner authenticates via already-logged-in `claude`/
`codex` CLI subscriptions and never reads or requires an API key
(`docs/trust-boundaries.md`, "Auth storage"; `007/src/invoke.rs::
strip_provider_api_keys` actively strips `ANTHROPIC_API_KEY`/
`CLAUDE_API_KEY`/`OPENAI_API_KEY`/`CODEX_API_KEY` from the subprocess
environment before every call). This is an **operational fact about the
reference implementation's actual authentication method**, not a
project-wide principle the contract enforces. The `AgentRunner` Protocol
says nothing about how a runner authenticates — `task_id`, `prompt`,
`input_paths`, `output_schema`, `capability_profile`, and `run_dir` are the
entire interface, and none of them constrains credential handling.

Concretely: a future API-key-based runner adapter (`--runner api`, say,
calling a provider's API directly with a key from the environment or a
secrets store) is a **legitimate implementation of this contract**, exactly
as legitimate as `o7`, provided it correctly implements `run()`,
`verified_profiles()`, and passes `tests/contract/test_runner_contract.py`.
It would need its own answer to "how do I know my declared
`verified_profiles()` are actually enforced" — which is a real design
question for whoever builds it, not a question this document answers on
their behalf — but nothing about needing an API key disqualifies it.

What *does* remain a real, specific security property, scoped to the `o7`
runner rather than the contract as a whole: 007's API-key stripping
prevents a key present in the parent environment for an unrelated reason
from silently substituting for the subscription-authenticated login the
`o7` runner is actually supposed to be exercising. That is a property of
*that runner's* trust model (subscription auth is the thing being
verified; an ambient key must not quietly become the actual auth path
without anyone noticing), not a statement that API-key auth is illegitimate
elsewhere in the contract's design space.

## Conformance

To add a new runner:

1. Implement the `AgentRunner` Protocol (`run()` and `verified_profiles()`)
   against whatever transport the new runner uses.
2. Register it in `cli.py::RUNNER_FACTORIES` under a new `--runner` name.
3. Pass `tests/contract/test_runner_contract.py` — the generic suite every
   runner adapter (present or future) must satisfy: status/field
   consistency (e.g. `schema_valid=true` implies a real, validating
   `structured_output_path`), `prompt_hash`/`input_hashes`
   independent-recomputability, `task_id` stability across retries,
   `run_dir` containment, and refusal behavior for an unrecognized
   `capability_profile`. This suite is what makes "any future runner
   adapter must pass it" checkable rather than aspirational — it runs
   against `FakeRunner` (and any other runner cheap enough to exercise
   offline) as part of the normal offline gate.

`scripts/o7_conformance_gate.py` is a **separate, additional** check that
exists only for the reference `o7` implementation: it runs the same
prompt/schema/input through `o7 invoke` directly (subprocess) and through
`O7InvokeRunner.run(...)` and asserts the two call paths agree on status,
`schema_valid`, `error_kind`, and structured output content — catching
translation drift between this repo's Python and 007's Rust specifically.
It requires a built `o7` binary on `PATH` and is not part of the offline
gate (`scripts/check.sh`); a new non-`o7` runner does not need an
equivalent script unless it, too, wraps some other external primitive it
needs to cross-check itself against.

## Versioning: the `o7` runner's handshake as a worked example

A runner is free to own its own compatibility check against whatever it
wraps — the contract does not standardize this, because a future runner's
compatibility surface (a REST API version, a different CLI's own flag) has
no reason to look like `o7`'s. `O7InvokeRunner` probes `o7 --version`
lazily on its first `run()` call (not at construction time, so simply
instantiating a runner never has a side effect), parses output of the shape
`o7 0.1.0`, and requires the `0.1.x` line. A mismatch — an `o7` binary too
old or too new to trust the `meta.json` shape this runner parses
structurally (`docs/trust-boundaries.md`'s "translation layer, not
enforcement" trust boundary) — returns `status="BLOCKED_NOT_INSTALLED"`
with `error_kind="o7_version_unsupported"` rather than attempting the call
against a binary whose `meta.json` shape hasn't been confirmed compatible.
`BLOCKED_NOT_INSTALLED` is the correct status for this case, not a new
one: from the caller's perspective, "wrong version installed" and "not
installed at all" both mean the same thing — this runner cannot be used
here — and both are systemic, non-retryable conditions.
