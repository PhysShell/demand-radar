# Decisions log

Append-only. Newest entries at the bottom. Each entry: date, decision, why.

## 2026-07-15 — Step 1: 007 repository inspection

Read before writing any code, per task instructions: `007/README.md`,
`007/TODO.md`, `007/docs/workflow-scripting.md`, `007/docs/agent-language.md`,
`007/docs/task-aware-context-generator.md`, `007/docs/security-layers.md`,
plus `007/judge/*` (the one working agent-invocation contract in 007 today)
and `Own.NET/sandboy/*` + `Own.NET/docs/notes/sandboy-isolation-adr.md`.

### What 007 actually is today

- A thin Rust orchestration binary (`o7 run`, `o7 judge`) that drives `claude`/
  `codex` CLIs over **Own.NET** and **OwnAudit** from the outside, using
  subscription auth (no API keys). Private repo by design — "subscription-
  auth/agent-routing code must not land in a public tree."
- `o7 run`: git-worktree isolate → agent full-auto (`bypassPermissions`) →
  gate (`bash -lc` steps from `.007/gate.toml`) → harvest artifacts
  (`task.md`, `meta.json`, `agent.stdout`, `diff.patch`, `gate/*.log`).
  Exit code 0/1 for CI gating. **Not yet exercised on a real coding task**
  (per TODO.md) — this is a scaffolded MVP, not a proven system.
- `o7 judge`: read-only FP-triage, closed-world (`--tools ""` +
  `--strict-mcp-config` for the claude backend — no built-in tool, no ambient
  MCP, no read/network/exfil path). This is the one **verified working**
  agent-invocation pattern in 007 and is the direct model for Demand Radar's
  own agent runners (Zone 2 in `docs/trust-boundaries.md`).
- Both `judge`'s claude and codex backends exist; codex's `--sandbox
  read-only` denies writes but **not network** (codex has no one-flag
  network-off equivalent) — claude's `--tools ""` is the stronger closed-world
  guarantee. We inherit this asymmetry: prefer claude for untrusted-content
  extraction/critique, treat codex as write-denied-but-not-network-denied.

### Load-bearing idea to carry over

**The script proposes, the host enforces** (`docs/workflow-scripting.md`).
007's workflow-scripting ADR explicitly scoped a v1 as a flat, linear
`workflow.toml` with no DAG, no multi-provider IR, no skills packaging —
and explicitly named "building a DAG engine for a need that hasn't appeared
yet" as speculative generality. Demand Radar's LangGraph pipeline is a
straight line (see `docs/architecture.md`) for the same reason: no
conditional branching has been justified by an actual run yet.

**Structured agent contract, not free prose** (`docs/agent-language.md`).
007's (proposed, not-yet-built) `O7Plan`/`TaskSpec` layer establishes the
pattern this MVP borrows directly: "every claim must have an EvidenceRef; a
claim without evidence is rejected," and "the model may propose, the system
decides" (deterministic judge, not the LLM, sets the verdict). Demand Radar's
`classification`/`opportunity-card` schemas and deterministic judge
(`scoring/judge.py`) are this same idea applied to evidence items instead of
code diffs.

**Security is layered, not one mechanism** (`docs/security-layers.md`).
007 documents its own present-day gap honestly: worktree `cwd` is cleanup
convenience, not confinement; the real missing layer is a syscall sandbox
(Landlock+seccomp), for which the sibling **sandboy** (in Own.NET) is the
built-but-unwired enforcement tool. Demand Radar has a narrower problem than
007 (no arbitrary `bash -lc` gate steps, no agent-edits-code loop) but the
same discipline applies: the agent zones (Zone 2) get **zero** filesystem/
shell/network tool surface at all (`--tools ""` / `-s read-only` +
`-c features.shell_tool=false`), not a deny-list layered on top of a general
capability set.

### Constraints this MVP must honor (from the task brief, cross-checked against 007)

- Do **not** add a Python/LangGraph runtime inside the `o7` Rust binary; do
  not change `o7 run` / `o7 judge`. Confirmed: 007 is Cargo/Rust-only, zero
  Python. Demand Radar is a fully separate sibling repository/process; the
  only relationship is "inspired by," recorded as a proposal
  (`docs/o7-bridge-proposal.md`), not an implementation.
- Do not implement `o7 workflow`, a DAG engine, Claude/Codex consensus,
  `O7Plan` runtime, a universal provider framework, or a memory layer inside
  007 — all explicitly out of scope per `TODO.md`'s own deferred backlog and
  per the task brief. Demand Radar reads these proposals as prior art for its
  *own* pipeline; it does not build them for 007.
- **sandboy** (`Own.NET/sandboy/`): a spiked (not yet `cargo build`-verified
  in this environment — the ADR says it was "authored in a network-restricted
  sandbox, not compiled") Landlock+seccomp wrap-the-child confinement,
  `sandboy run --policy step.toml -- <cmd>`. It wraps **subprocess steps**
  (gate commands), not "call an LLM API and read its text response," which is
  Demand Radar's actual shape for Zone 2. Considered and **not adopted for
  this MVP**: Demand Radar's agent runners already run with `--tools ""` /
  `-s read-only`, no shell tool, and (being ordinary CLI subprocess calls
  themselves) could be wrapped by `sandboy run --policy ... -- claude ...`
  for defense-in-depth the same way a `.007/gate.toml` step would be — this is
  recorded as a deferred hardening option in `docs/trust-boundaries.md`
  rather than wired in now, because (a) sandboy is Linux-only/unverified-
  built in this sandbox, (b) the closed-world CLI flags already remove the
  tool surface an OS sandbox would otherwise need to contain, and (c) adding
  an unproven dependency to satisfy a threat (agent escapes its own
  `--tools ""` restriction) that has no observed instance yet is exactly the
  "building for a need that hasn't appeared" pattern `docs/workflow-
  scripting.md` warns against. Revisit if a future Demand Radar agent zone
  needs actual shell/filesystem tool access.

### Codex CLI flags — verification method

`codex` is not installed in this execution environment, so its flags could
not be checked by running `codex --help` directly (the task instructs
verifying current official flags before implementation). Verified instead
against public OpenAI documentation and cross-referenced against 007's own
`judge/README.md` (which already runs `codex` in production for the FP-judge
`--provider codex` path): `codex exec --json --output-schema <file> -s
read-only -a never`, config overrides via `-c features.shell_tool=false`.
This is recorded as a residual risk in `docs/trust-boundaries.md` — flags
should be re-checked against `codex --help` on any machine that has it
installed before first real use.

## 2026-07-15 — Product context read

Read `griff/AGENTS.md` (swancore riff generator, Rust workspace, strict
TDD/no-unsafe constitution) and `OwnAudit/AGENTS.md` + `README.md` (lift-out
orchestration repo for Own.NET's legacy .NET/WPF audit pipeline) to ground
`products/own-audit.yaml` and `products/griff.yaml` in the real shape of
each product rather than inventing personas from nothing. Neither repo is
modified by this task — Demand Radar only reads their public positioning to
write believable product configs.

## 2026-07-15 — LangGraph pipeline stays a straight line

Per the task brief's own pipeline diagram (`load_run` through `verify_run`)
and 007's workflow-scripting precedent above: no `depends_on`, no branch/
merge nodes. The one piece of real control flow is the retry/resume behavior
LangGraph's checkpointer gives for free, which is the actual reason this MVP
uses LangGraph instead of a plain function pipeline (see
`docs/architecture.md` §LangGraph necessity) — not because the DAG shape
needs it.

## 2026-07-15 — Ingestion contract: raw records, not full EvidenceItems

`fixtures/mixed-demand-signals.jsonl` and the real `ingest`/`ingest-rss`
paths all produce `ingest/jsonl.py::RawEvidenceRecord` (source_id/kind/
family, url, author, published_at, text, query_id) rather than a
pre-built `EvidenceItem` — provenance fields ingestion itself must derive
(`id`, `content_hash`, `normalized_text`, `author.stable_hash`,
`collected_at`) are computed once, in `build_evidence_item`, shared by both
the JSONL and RSS paths. This is the "unified ingestion contract" section 7
asks future collectors to plug into: a new collector only has to produce
`RawEvidenceRecord`-shaped dicts, never hand-compute a hash or an id.

## 2026-07-15 — Clustering's agent-assist is naming/merge-only, with a deterministic fallback

Section 14 lists "preliminary clustering assistance" as one of the
analyst's responsibilities. Implemented narrowly: the analyst gets a list
of already-deterministically-grouped `problem_key`s (from exact match) and
representative statements, and may propose a label plus which keys (not
evidence ids) describe the same real problem and should merge. It never
sees or returns an evidence_id, so it cannot fabricate or drop one — the
model operates one level of indirection away from membership.
`propose_clusters` falls back to one cluster per `problem_key` (slug as
label) if this call fails in any way, so clustering never blocks on model
availability. Considered and rejected: asking the analyst to also decide
final cluster membership directly, which would have required trusting a
list of evidence ids from the model and validating it after the fact,
instead of the id ever being absent from the model's context in the first
place.

## 2026-07-15 — Fixture needed a fourth cluster for "2+ rejected, differently reasoned"

Section 22 requires "two or more rejected candidates." The first pass
(strong/weak/viral clusters) produced only one naturally-rejected candidate
(the viral post, single-source). Added a fourth small cluster
(`cs0246-namespace-not-found`, two evidence items about a common beginner
build error, both explicitly stating they want to learn the cause, not buy
a fix) so the fixture's rejected candidates are rejected for two distinct,
independently verified reasons (`single_viral_source_dominant` /
`complaint_not_purchase_intent` vs `educational_not_commercial`) —
see `tests/integration/test_fixture_pipeline.py::test_rejected_candidates_have_distinct_reasons_not_hidden`.

## 2026-07-15 — Real bug found while building resumability: None looks like "unset" after a checkpoint reload

`langgraph`'s `LastValue.from_checkpoint` only restores a channel's value
if the persisted checkpoint value `is not None` — a channel whose value is
exactly `None` (e.g. `state["since"]` on an all-time run) deserializes as
*absent*, not as `None`, after any resume. Found via
`tests/integration/test_resume.py`'s crash-and-resume test (which
constructs a genuinely fresh `Store`/runner pair for the second attempt,
not just continuing in the same Python objects) — a bare `state["since"]`
subscript raised `KeyError` post-resume even though the exact same code
worked in an uninterrupted run. Fixed by using `state.get(...)` for every
`Optional`-typed `DemandState` field (`since`, `analyst_run_id`,
`critic_run_id`, `verdict`), never a bare subscript, in any node that might
run after a resume. This is a langgraph-library behavior, not a bug in this
codebase, but it is exactly the kind of thing "at least one test of
resumption after an artificial failure" (spec section 12.3) is for —
without that test this would have shipped silently broken.

## 2026-07-15 — Run artifact layout: per-call subdirectories, not one flat analyst/critic dir

Section 20's illustrative run-artifact tree shows one `agents/analyst/`
directory with a single `prompt.txt`/`stdout.jsonl`/`result.json` set. This
pipeline calls the analyst once per evidence item (`classify`) and once per
cluster (`generate_opportunities`), and the critic once per opportunity —
a flat directory would mean each call overwrites the previous one's
artifacts. Used `agents/analyst/classify/<evidence_id>/attempt-N/...` etc.
instead, preserving full per-call provenance. Documented in
`docs/architecture.md` as a deliberate, minor deviation ("structure may be
adjusted as long as ingestion/agent-execution/scoring/storage/reporting
boundaries stay explicit" — section 8), not a silent omission.

## 2026-07-15 — CLI refuses same-provider analyst+critic outside of `fake`

Section 29 explicitly forbids "quietly substitute Codex with Claude if
Claude is unavailable" as an MVP shortcut, and section 24 names
`single_provider_unreviewed` as a status that must never pass the final
gate. Implemented as an upfront CLI check (`cli.py::run`): `--analyst X
--critic X` for any `X != "fake"` is refused before the pipeline even
starts (exit code 2, explicit `single_provider_unreviewed` message), rather
than letting a same-provider run complete and trying to retroactively mark
its verdict as lesser. `fake`+`fake` remains allowed since it's how offline
tests exercise the full pipeline without either real CLI.

## 2026-07-15 — Real bug found by the real smoke test: `claude --json-schema` rejects our own `$schema` key

Running `demand-radar smoke-agents` for real (`claude` is installed and
authenticated in this environment) initially returned `claude:
FAIL_INVALID_OUTPUT -- nonzero_exit`, not `PASS`. `runs/_smoke/*/claude/
stderr.log` had the exact cause: `Error: --json-schema is not a valid JSON
Schema: no schema with key or ref
"https://json-schema.org/draft/2020-12/schema"`. `claude_cli.py` was passing
whatever schema file it was given to `--json-schema` byte-for-byte, and
every schema this project writes (`schemas/*.schema.json`,
`agents/prompts/opportunity.py::OPPORTUNITY_CANDIDATE_SCHEMA`) declares a
top-level `"$schema": "https://json-schema.org/draft/2020-12/schema"` key,
per normal JSON Schema authoring convention. Isolated with a direct `claude
-p --json-schema ...` invocation outside this codebase: a schema with
`$schema` present fails every time with the exact error above; the same
schema with only `$schema` removed succeeds; a schema with `$id` present
(no `$schema`) also succeeds — so the live `claude` binary (v2.1.210)
specifically chokes on `$schema`, not on meta-keys in general. This would
have broken every real-Claude call in the actual pipeline
(`classify`/`generate_opportunities`), not just the smoke test — the smoke
test caught it only because it is the one path in this codebase that
actually shells out to the real binary; every other test uses `FakeRunner`
and would not have exercised this at all.

Fixed by adding `agents/base.py::strip_dollar_schema` (parse, drop
`$schema` if present, re-serialize) and calling it in `claude_cli.py`
before the `--json-schema` argv entry is built. Applied the same helper to
`codex_cli.py`'s `--output-schema <file>` (writing a stripped copy into
`run_dir` rather than pointing at the original schema file), since both
runners read the same schema files/dicts and could plausibly share the
same failure mode — this half is **not** independently verified against a
real `codex` binary (still not installed in this environment; same residual
risk already logged above under "Codex CLI flags — verification method").
Re-ran `demand-radar smoke-agents` after the fix: `claude: PASS`,
`codex: BLOCKED_NOT_INSTALLED` (honest, expected — codex genuinely isn't
installed here). Schema files themselves keep their `$schema` key
(needed for editor/tooling validation and for `schemas/*.json`'s own
Draft 2020-12 validity check in `scripts/check.sh`); the strip happens only
on the wire to each CLI's schema flag, not in the files on disk.

## 2026-07-15 — Migrated to `o7 invoke`; `claude_cli.py`/`codex_cli.py` deleted

Follow-up scope, explicitly authorized to also touch 007 (previously
off-limits): consolidate the closed-world Claude/Codex CLI-invocation logic
that existed **twice** — once here (`claude_cli.py`/`codex_cli.py`,
partially unverified for Codex) and once, more carefully, in 007's
`judge.rs` (`--output-last-message`, proven) — into one place. 007 gained a
narrow new primitive, `o7 invoke` (`007/src/invoke.rs`), generalizing
`judge.rs`'s closed-world call pattern to an arbitrary caller-supplied
prompt + schema. `agents/o7_invoke.py::O7InvokeRunner` is now this repo's
only non-fake `AgentRunner`; `claude_cli.py`/`codex_cli.py` are deleted, not
kept as dead code. Full record, what differs from the original
`docs/o7-bridge-proposal.md` sketch, and live verification results:
`docs/o7-invoke.md`.

Two real things fell out of doing this migration carefully rather than
quickly:
1. **`claude --json-schema` rejects `$schema`** (the bug logged above) —
   confirmed to also affect 007's own new `invoke.rs` if left unfixed;
   fixed once, in Rust, so every caller of `o7 invoke` (not just this repo)
   gets it. This repo's own `strip_dollar_schema` copy (`agents/base.py`)
   became dead code the moment `claude_cli.py` was deleted, so it was
   deleted in the same pass, not left for a future cleanup to rediscover.
2. **Provider API keys were never stripped for Codex-via-`judge.rs`, and
   never stripped for Claude at all, anywhere.** This repo's own
   `codex_cli.py::_clean_env` stripped `OPENAI_API_KEY`/`CODEX_API_KEY`
   for Codex only; 007's `judge.rs` strips neither, for either engine.
   Writing the trust-boundary comparison for `docs/o7-invoke.md` surfaced
   this gap directly (comparing "what does each side actually strip"
   line by line) — fixed in `invoke.rs::strip_provider_api_keys`, applied
   to both engines, since `o7 invoke` is meant to be the one shared
   primitive both this repo and any future caller relies on.

Verified, not assumed: cross-repo conformance gate
(`scripts/o7_conformance_gate.py`) run for real in this environment —
`claude` direct-`o7-invoke` vs `O7InvokeRunner`-wrapped calls agree on
`status`/`schema_valid`/`error_kind`/structured output
(`PASS`/`PASS`/`True`/`True`/matching `{"acknowledged": true}`); same for
`codex` (`BLOCKED_NOT_INSTALLED`/`BLOCKED_NOT_INSTALLED` on both sides,
matching `error_kind`). All 124 tests still pass (117 pre-migration + 7 new
`test_o7_invoke_runner.py` tests, replacing the 7 deleted
`test_cli_runner_schema_wire.py` tests that tested the now-deleted
runners' own schema-stripping).
