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

## 2026-07-15 — Correction: "no shell" was overclaimed for Codex; Codex refused for real Zone 2 use

External review of the migration above caught a real inconsistency, not a
cosmetic one: `docs/trust-boundaries.md` described Zone 2 ("It has no
shell, so 'run this command' in evidence text has nowhere to go") as if it
held equally for both engines. It does not. Claude's `--tools ""` removes
the tool surface structurally and is verified live in this environment.
Codex's posture in `o7 invoke` rests on `--sandbox read-only` (which 007's
own docs already say denies writes without disabling network) plus a
`-c features.shell_tool=false` flag that — this migration had actually
*dropped* relative to Demand Radar's own now-deleted `codex_cli.py`,
reasoning that `judge.rs`'s proven flag set (which never carried this flag
either) was the safer baseline to match. That reasoning traded a real,
if-unverified, extra restriction for a "more proven" one that never
attempted the restriction at all — the wrong trade once the actual
consequence (a documented security claim the code didn't back) was named
plainly instead of just being an internal implementation choice.

Two fixes, not one:
1. Restored `-c features.shell_tool=false` to `invoke.rs::call_codex`
   (007) — still unverified against a live install, but at least attempted
   again, and an invalid config key fails loudly rather than silently
   running less restricted.
2. `cli.py::run` now refuses `--analyst codex`/`--critic codex` outright
   (`codex_unverified_for_untrusted_content`, exit 2) — Zone 2 processes
   untrusted evidence text, and neither of Codex's restriction flags has
   ever been exercised against a real `codex` binary. `demand-radar
   smoke-agents` is unaffected (fixed non-adversarial prompt, no evidence
   content, constructs `O7InvokeRunner` directly rather than going through
   `run`'s provider guard) — confirmed unaffected by re-running it after
   this change (`claude: PASS`, `codex: BLOCKED_NOT_INSTALLED`).

`docs/trust-boundaries.md`, `README.md`, and `docs/o7-invoke.md` all
corrected to state the per-engine distinction plainly rather than a
blended claim; `007/docs/o7-invoke.md` (new — the primitive's own design
note, distinct from this repo's migration-focused doc of the same name)
documents the same thing from 007's side, plus what a real fix looks like:
install codex, re-verify the flags against `codex --help`, and run a live
adversarial smoke test (a prompt-injection payload attempting the exact
command-execution/exfiltration path Claude's `--tools ""` forecloses)
before lifting the CLI refusal — not just confirming the flag is present
in the argv. Added `tests/integration/test_cli.py::test_run_refuses_codex_for_untrusted_zone_2`.

Also strengthened `scripts/o7_conformance_gate.py`, flagged separately as
too weak: it previously passed `input_paths=[]`, so `--input-manifest`/
`input_hashes` were never exercised on either call path, and it only
compared 4 fields. Now passes a real, non-empty input fixture and checks
`input_hashes`, `provider`, `model`, and `exit_code` too, on top of the
existing `status`/`schema_valid`/`error_kind`/structured-output/prompt_hash
checks. Re-run for real after all fixes: still `CONFORMANCE GATE: PASS`
for both engines.

## 2026-07-15 — Correction: alphabetic sampling bias, incomplete bot filter, false cross-repo dedup in the GitHub acquisition bridge

External review of `docs/trials/github-live-issues-trial.md` (the Phase 2B
live trial, workflow run `29430642497`, commit `20bec3e`) found three real
defects in `scripts/acquire_github_issues.py` and the production dedup
config, plus a causal misstatement in the report itself. All four are fixed
here; `007`, `o7 invoke`, and the Codex policy are untouched, and the query
pack / source families are unchanged, per the correction's own scope.

1. **Deterministic sampling bug — alphabetic, not fair.** `run_acquisition()`
   sorted all eligible records by `source_id` (alphabetic by `owner/repo`)
   *before* slicing to `max_records` — a tournament of repo owners for an
   early ASCII slot, not a market sample: whichever queries happened to
   surface early-alphabet owner names would crowd out every other query's
   results once the eligible set exceeded the cap. Fixed by reordering to
   fetch → classify/filter (including schema validation) → exact `source_id`
   dedup across queries (`deduplicate_by_query`; first-seen query, in the
   request file's own order, owns a duplicate) → deterministic round-robin
   selection across queries up to `max_records` (`select_round_robin`, new)
   → only then sort the *selected* set by `source_id`, purely for
   serialization. New manifest fields recording the corrected mechanism:
   `selection_strategy`, `eligible_total_before_cap`,
   `eligible_per_query_before_cap`, `accepted_per_query`,
   `excluded_by_cap_per_query`. New regression coverage
   (`test_run_acquisition_selection_is_not_alphabetically_biased` plus unit
   tests for `select_round_robin` itself) reproduces the exact shape of the
   bug — a query whose repos sort last must still get a fair share — and
   would fail against the old code.
2. **Bot filter missed migration/import accounts.** `firebird-automations`,
   `GoogleCodeExporter`, `ironpythonbot`, and `orchardbot` — real accounts
   present in the live trial dataset — matched neither the `[bot]` suffix
   convention nor the curated login list. Fixed by adding `user.type ==
   "Bot"` as an independent signal (GitHub's own API field, alongside the
   login list, not a replacement for it) and adding these 4 logins to the
   curated set by name, with regression tests for exactly those 4. No
   attempt is made to extract a "real" author from free-text issue body
   content (e.g. migrated Jira/Google Code text naming the original
   reporter) — out of scope, per instruction; these accounts are excluded as
   non-independent authors by account identity only.
3. **Confirmed false dedup collapse, fixed with a scoped threshold, not a
   blind global raise.** `EWSoftware/VSSpellChecker#30` and `NuGet/Home#3474`
   — two unrelated issues in unrelated repos, both auto-generated Visual
   Studio environment dumps that happen to share enough boilerplate text —
   scored 0.594 under the single `similarity_threshold=0.5`, above
   threshold, and collapsed into one duplicate group. `ingest/deduplicate.py`
   gained a separate `cross_repo_similarity_threshold` (0.75,
   `products/own-audit.yaml`), applied only when a pair's derived repo keys
   differ (or are unknown); same-repo pairs are untouched. Verified two
   ways: (a) synthetic fixtures calibrated to the same 0.5-0.75 band
   (`tests/unit/test_deduplicate.py`, 3 new tests, 10/10 passing); (b)
   re-running `deduplicate_evidence()` offline against the trial's actual
   100-record `evidence.jsonl` — old: 4 links/3 groups/96 independent, new:
   3 links/2 groups/97 independent, exactly the one false pair removed and
   nothing else changed. `similarity_threshold` itself (same-repo pairs) is
   untouched — it was never the part that was wrong.
4. **Report causal misstatement, corrected.** The live-trial report
   attributed the run's `BLOCKED` verdict to the critic ("all 8
   `critic_review` calls failed `FAIL_SCHEMA`") and claimed critic absence
   was "the only reason this run doesn't carry a clean PASS." Both were
   wrong, checked against the actual code rather than assumed:
   `verification.py::overall_verdict()` returns `BLOCKED` on the analyst's
   own status first, and this run's analyst hit a real `BLOCKED_TIMEOUT` on
   one of its 96 live classify calls — that alone explains the BLOCKED
   result, independent of the critic's separate `FAIL_INVALID_OUTPUT` status
   (which alone would have produced `FAIL`, not `BLOCKED`). Separately,
   `products/own-audit.yaml`'s `minimum_source_families: 2` gate is unmet by
   this all-`github` dataset regardless of critic status — a second,
   independent blocker to `experiment_ready` that a working critic alone
   would not clear. `docs/trials/github-live-issues-trial.md` §10 now states
   five facts separately (acquisition PASS; analyst result USEFUL BUT
   BIASED; run verdict BLOCKED/cause analyst timeout; critic status
   FAIL_INVALID_OUTPUT/cause FakeRunner; validation blocked by two
   independent gates) instead of one collapsed causal story.

Verification: full offline gate (`ruff check`, `ruff format --check`,
`mypy --strict` on `src/`, full `pytest` suite) green; acquisition-script
unit tests grew from 17 to 26 (new coverage for the 4 named bot accounts,
`user.type=="Bot"`, and round-robin selection fairness/determinism); dedup
regression tests grew from 7 to 10. One new acquisition workflow run
triggered by this same push (a comment-only addition to
`research/acquisition-request.yaml` — no change to product, queries, or
limits) verifies the sampling and bot-filter fixes against real GitHub data,
without re-running the 100-call Claude classification pipeline from the
original trial. See the addendum below for that run's actual result.

### Addendum — verification run result

Workflow run `29439478053` completed (`success`, ~43s), data branch
`research-data/own-audit-live-20260715T181121Z-29439478053`:

```
fetched total:        247
excluded:               37  (bot_author: 36, physshell_owner: 1)
duplicates (exact cross-query, source_id): 5
eligible before cap:   205
accepted (evidence.jsonl):    100
selection_strategy: round_robin_by_query_order_then_source_id_sort
```

**Round-robin selection confirmed fair, on real data.** `accepted_per_query`
is 11 for 8 of the 10 queries and 6 for the remaining 2
(`propertychanged-performance-wpf`, `retained-object-wpf`) — exactly those
2 queries' entire eligible count (6 each, `excluded_by_cap_per_query: 0` for
both), meaning every eligible record from the two smaller queries was kept,
while the 8 larger queries shared the remaining 88 slots evenly (11 each) —
exactly what round-robin-until-exhausted produces. The arithmetic is
self-consistent end to end: 247 fetched − 37 excluded − 5 duplicates = 205
eligible; 205 eligible − 105 excluded-by-cap = 100 accepted, matching both
`over_max_records_cap_count: 105` and `sum(accepted_per_query) == 100`
exactly. No query was crowded out by another query's repo-name ASCII
ordering, unlike the original run.

**Bot filter confirmed on real data.** The new `unique_authors` list (90
names) contains none of `firebird-automations`, `GoogleCodeExporter`,
`ironpythonbot`, or `orchardbot` (checked case-insensitively). `bot_author`
exclusions rose to 36 (vs. 16 in the original run) — a different underlying
GitHub dataset at a different point in time, so not a like-for-like
before/after count on the same records, but it confirms the mechanism
(`user.type=="Bot"` plus the 4 named logins) does real, additional work on
live data, not only in the synthetic unit tests.

Not re-verified here, by design, per the correction's own scope: the
dedup-threshold fix (already verified directly against the *original*
trial's real data — see `docs/trials/github-live-issues-trial.md` §2, not
repeated against this new dataset) and the `order=desc` fetch change (a
one-line, unambiguous diff; this run's successful completion confirms the
API accepts the parameter — its effect on issue recency is not separately
re-analyzed, since doing so would need the analyst to run against this new
dataset, out of scope here). This new data branch is a verification
artifact only: it is not ingested, not analyzed, and is not the trial's
dataset of record — `docs/trials/github-live-issues-trial.md` continues to
describe run `29430642497` exactly as originally collected.
