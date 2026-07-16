# Phase 2C-SMOKE: synthetic review pipeline test

Status: **ACCEPTED / CLOSED / FROZEN** — arbiter final verdict, frozen at
commit `c70ec1e7d5e1606db8462db92b4e11bed947d049` (§15). **Canonical Phase
2C status is unchanged and unaffected by this freeze: AWAITING_HUMAN_REVIEW.**

**Corrected twice, 2026-07-16, same day as the original run below: the
import write phase was not actually atomic.** §13 fixed the SQLite side
(batch transaction, no per-row commit). A second arbiter review of §13's
own fix then found the two file-publish steps still sat outside the
transaction boundary — §14 fixes that. Every fact and transcript in
§1-§12 describes the smoke run exactly as it happened and is unchanged;
§9's and §12's use of the word "atomic" is qualified by §13 and §14
together, which are the full, precise account of what was and wasn't
guaranteed at each point.

This is a purely technical, mechanical test: review packet → synthetic
fixture generation → hash/schema validation → atomic review import →
deterministic finalize → regenerated report/verification. **It is not an
independent review, not market validation, and not a continuation of the
Phase 2C research verdict.** No opportunity was actually reviewed by
anyone. Every fixture verdict carries a fatal, machine-generated objection
whose statement says exactly that.

## 1. What this scope touched, and what it explicitly did not

- Canonical head: `bcfdc00dec531a46f6251fabd852d9fad7d81fe7` (Phase 2C
  implementation/pre-review checkpoint — `ACCEPTED / CLOSED / FROZEN`).
- Canonical run: `phase-2c-combined-001`, canonical store/run_dir/review
  packet at `runs/phase-2c/`. **Never opened for writing** — every
  operation below ran against a separate copy at `runs/phase-2c-smoke/`.
- Smoke run: `phase-2c-review-smoke-001`, its own store/run_dir/review
  packet under `runs/phase-2c-smoke/` (gitignored runtime data, not
  committed — see `.gitignore`'s `runs/` entry, same convention every
  other trial in this repo follows).

## 2. Canonical files: hash-identical before and after

Recorded before touching anything, and again after the full copy → generate
→ import → finalize sequence completed:

| file | before | after |
|---|---|---|
| `runs/phase-2c/store.db` | `4e350716f5…6ceb7e` | `4e350716f5…6ceb7e` |
| `.../phase-2c-combined-001/state.json` | `e2eb1c535c…1dbe49` | `e2eb1c535c…1dbe49` |
| `.../outputs/opportunities.json` | `9418f21cd1…33b8c7b65c` | `9418f21cd1…33b8c7b65c` |
| `.../outputs/report.md` | `4c720bda6b…089f9d573c` | `4c720bda6b…089f9d573c` |
| `.../verification.json` | `f32f330562…c26f9405a` | `f32f330562…c26f9405a` |
| `runs/phase-2c/review-packet/packet-manifest.json` | `62b0116744…c0d8a4c8` | `62b0116744…c0d8a4c8` |

**All six files are byte-identical, `sha256sum -c` verified.** This is the
core safety proof for the whole scope — see `scripts/setup_review_smoke_copy.py`
for exactly what it copies and rewrites (a separate file is always produced
via `shutil.copy2`/`copytree` before any rewrite; the canonical paths are
never opened for writing).

## 3. Smoke copy: what was rekeyed, what was not

```
uv run python scripts/setup_review_smoke_copy.py
```

Output:

```
runs: 1 row(s) rekeyed
opportunity_cards: 19 row(s) rekeyed
problem_clusters: 19 row(s) rekeyed
classifications: 193 row(s) rekeyed
critic_verdicts: 0 row(s) rekeyed
```

Only the `run_id` SQL column on those rows changed (`phase-2c-combined-001`
→ `phase-2c-review-smoke-001`) — `opportunity_cards.id`,
`problem_clusters.id`, `evidence_items.id`, and every stored JSON blob are
untouched, because none of `models.py`'s `OpportunityCard` / `ProblemCluster`
/ `Classification` embed `run_id` in their own schema at all (confirmed by
direct inspection — only `ReviewEnvelope.run_id` and `Verification.run_id`
exist as model fields). `evidence_items` and `duplicate_links` have no
`run_id` column to begin with (evidence is product-scoped, not run-scoped)
and were not touched. `task.yaml`, `state.json`, and
`packet-manifest.json`/each `review-template.json` had their `run_id`
field rewritten the same way; `task.yaml` additionally gained:

```yaml
test_fixture: true
test_fixture_kind: synthetic_review_pipeline_smoke
canonical_parent_run: phase-2c-combined-001
```

**Independent confirmation that hashes survived the copy unchanged:**
`packet-manifest.json`'s `opportunity_hashes` and `evidence_manifest_hashes`
dicts in the smoke packet are byte-identical, key-for-key and value-for-value,
to the canonical packet's — e.g. `opp_daa8c508878c` (the one cross-family
opportunity from the combined trial) hashes to
`sha256:9f50af3b20b54da6eab7cb8185c066760877ca1183485a189730d878c22175e6`
in both.

## 4. Fixture generation

```
uv run demand-radar review generate-fixtures \
  --run phase-2c-review-smoke-001 \
  --packet runs/phase-2c-smoke/review-packet \
  --output runs/phase-2c-smoke/completed-fixtures.jsonl
```

Output: `TEST FIXTURE ONLY -- synthetic pipeline-mechanics fixtures, not a
review` · **19 opportunities in the packet, 19 fixtures generated** — one
per opportunity, none skipped, none duplicated. Each fixture:

- `fixture.kind = "test_fixture"`, `fixture.substantive_review_performed =
  false` — both `Literal`-typed in `ReviewFixtureMetadata`, so a fixture
  claiming otherwise cannot even be constructed.
- `verdict.recommended_status = "investigate"`, one `fatal: true` objection
  stating "Synthetic test fixture only; no substantive independent review
  was performed."
- `opportunity_hash` / `evidence_manifest_hash` taken verbatim from
  `packet-manifest.json` — `generate_fixtures()` never opens
  `opportunity.json` or `evidence.jsonl`, and calls no agent.

## 5. Import: rejected without the flag, accepted with it

**Without `--allow-test-fixture` (must fail):**

```
uv run demand-radar review import --run phase-2c-review-smoke-001 \
  --input runs/phase-2c-smoke/completed-fixtures.jsonl \
  --db runs/phase-2c-smoke/store.db --runs-dir runs/phase-2c-smoke/runs
```

```
error: batch contains test fixture envelope(s) -- pass --allow-test-fixture
to import them (never required or accepted for real human reviews)
```

Exit code 1. Zero mutation — no `critic_verdicts` row written, no
`reviews/provenance.jsonl` created.

**With `--allow-test-fixture` (must succeed, run is marked
`test_fixture=true`):**

```
uv run demand-radar review import --run phase-2c-review-smoke-001 \
  --input runs/phase-2c-smoke/completed-fixtures.jsonl \
  --db runs/phase-2c-smoke/store.db --runs-dir runs/phase-2c-smoke/runs \
  --allow-test-fixture
```

```
TEST FIXTURE ONLY -- mechanics validated, no substantive review performed
imported 19 review(s): [... 19 opportunity ids ...]
imported file archived at runs/.../reviews/imports/20260716T024250Z-177238701e70.jsonl
imported file sha256: sha256:177238701e703e3d39857b2fd3006c39ac4e6a14776179629e974fa427e67c59
```

Exit code 0 · **19/19 accepted, 0 rejected.** Independently re-hashed the
archived file on disk: `177238701e703e...` — matches the CLI's own report
exactly. `reviews/provenance.jsonl` has 19 lines, every one
`"source_kind": "test_fixture"`, each carrying the fixture's own
`generator`/`purpose`/`substantive_review_performed` metadata alongside the
hashes — the mechanism that keeps a fixture from ever being mistaken for a
human review's provenance record after the fact.

## 6. Hash validation result

Every opportunity/evidence-manifest hash independently recomputed from the
packet files on disk and compared against `packet-manifest.json`:

```
checked 19 opportunity hashes + 19 evidence manifest hashes, mismatches=0
```

`packet-manifest.json` sha256:
`sha256:a8393257d7c0feb72265461c04e349be9c3eea7ebef9fb0fa1b95bfb15a8996c`.

## 7. Finalize

```
uv run demand-radar finalize --run phase-2c-review-smoke-001 \
  --db runs/phase-2c-smoke/store.db --runs-dir runs/phase-2c-smoke/runs
```

```
run_id=phase-2c-review-smoke-001 verdict=BLOCKED
```

Exit code 2.

### 7.1 Statuses before finalize (preliminary, `--critic human` output)

`preliminary/outputs/opportunities.json` (archived by `finalize_run` before
being overwritten): **19/19 `investigate`**. `preliminary/verification.json`:
`verdict=BLOCKED`, `critic_status=NOT_RUN`, `analyst_status=BLOCKED_TIMEOUT`.

### 7.2 Statuses after finalize

```json
{
  "schemas_valid": "PASS",
  "evidence_refs_valid": "PASS",
  "duplicate_inflation_absent": "PASS",
  "deterministic_rules_passed": "PASS",
  "report_generated": "PASS",
  "analyst_status": "BLOCKED_TIMEOUT",
  "critic_status": "PASS"
}
```

**19/19 opportunities: `rejected`** — every card's fatal fixture objection
hits `judge_opportunity`'s rule 3 ("a fatal critic objection is an outright
block, regardless of volume") before the volume/threshold gates are even
reached. `experiment_ready` count: **0**. `externally_validated` count:
**0** (also structurally unreachable from this pipeline regardless — see
`tests/unit/test_judge.py::test_externally_validated_is_not_a_reachable_status`).

### 7.3 `review-channel-status.json` — the separate TEST_FIXTURE_REVIEW marker

```json
{
  "pipeline_mechanics": "PASS",
  "review_channel_status": "TEST_FIXTURE_REVIEW",
  "substantive_review": "NOT_PERFORMED"
}
```

Written by `finalize_run` only because every provenance record for this run
is `source_kind: "test_fixture"` **and** every opportunity has a verdict
(completeness, not just purity — an earlier version of this check only
verified "not mixed with human reviews," which a test caught: a *partial*
fixture import also satisfied that weaker condition and incorrectly wrote
the marker; fixed to require completeness before writing anything — see
`tests/integration/test_review_fixture_smoke.py::test_partial_fixture_set_stays_blocked`).
`TEST_FIXTURE_REVIEW` is **not** part of `AgentRunStatus` or
`verification.schema.json` — it lives only in this separate,
Phase-2C-SMOKE-specific file.

### 7.4 The run's overall verdict: `BLOCKED` — and why, precisely

`critic_status` reads `PASS` (the fixture channel is complete), yet the
run's own `verdict` is still `BLOCKED`, because `analyst_status` reads
`BLOCKED_TIMEOUT` — inherited verbatim from the canonical run's own
`state.json` (the 3 classify timeouts + 1 invalid-output result already
disclosed in `docs/trials/phase-2c-combined-trial.md` §4.3). This is the
exact shape the phase spec's preferred outcome describes
(`verdict: BLOCKED`, `review_channel_status: TEST_FIXTURE_REVIEW`), and it
holds for two independent, honestly-reported reasons at once rather than
one: a synthetic review channel that structurally cannot claim substance,
*and* a real, already-disclosed analyst gap it does not paper over.

## 8. Proof of zero agent calls

Three independent lines of evidence, not just one:

1. **Structural**: `finalize_run()` constructs `NeverCalledRunner()` for
   both `analyst_runner` and `critic_runner` regardless of caller — if
   `deterministic_judge`/`render_report`/`verify_run` ever referenced
   either, this would raise `AssertionError` immediately. Unchanged from
   Phase 2C2, not something Phase 2C-SMOKE modified.
2. **Test**: `tests/integration/test_review_fixture_smoke.py::test_finalize_calls_no_agent_for_fixture_reviewed_run`
   runs this exact fixture → import → finalize sequence end to end and
   would fail loudly on any agent call.
3. **Live filesystem evidence from this actual run**: every file under
   `agents/` in the smoke run_dir predates the smoke procedure by over an
   hour (last write at `2026-07-16T02:29:33Z`, inherited unmodified from
   the copy of the canonical run's real analyst calls); every file the
   smoke procedure itself wrote (`reviews/`, `outputs/`, `verification.json`)
   is timestamped `2026-07-16T02:42:50Z` or later. The directory count
   under `agents/` (3 subdirectories) is identical between the canonical
   and smoke run_dirs — nothing new was added.

## 9. Tests

20 new tests (`tests/unit/test_review_fixtures.py`, 15;
`tests/integration/test_review_fixture_smoke.py`, 5), covering exactly the
required list: fixture generation (one per opportunity, deterministic,
correct hashes), rejection without `--allow-test-fixture`, rejection in a
run with no `task.yaml` and in one shaped like a real pipeline run but
without the `test_fixture` marker, rejection of mixing fixture/human
reviews, stale opportunity/evidence hash rejection, unknown-opportunity
rejection, malformed nested `CriticVerdict` rejection, atomic batch import
**(validation-phase: a batch is rejected wholesale if any one envelope
fails validation, before any write — see §13 for the separate write-phase
guarantee added afterward)**, provenance marked synthetic, a complete fixture set reaching mechanics
completeness, a partial set staying blocked, finalize calling no agent,
fixture finalize never producing `experiment_ready` or
`externally_validated`. The real human-review workflow's own test suite
(`tests/unit/test_review.py`, `tests/integration/test_review_workflow.py`)
required zero changes and still passes unchanged, proving requirement #20
by construction rather than by a new test asserting it.

Full offline gate: **254 tests passed**, `ruff check` clean, `ruff format
--check` clean, `mypy --strict src` clean.

## 10. Published CI

Code commit `cd38172` (`feat: add synthetic review-fixture pipeline smoke
path`): workflow run
[`29467399513`](https://github.com/PhysShell/demand-radar/actions/runs/29467399513),
status check `quality-gate`, conclusion **success** — all four gate
commands (`ruff check`, `ruff format --check`, `mypy --strict src`,
`pytest`) run as named steps, 254 tests passing at that commit. This
report's own commit is verified separately after it is pushed (see
`docs/decisions.log.md`'s Phase 2C-SMOKE entry and the commit history for
the exact run id, following the same pattern every prior trial report in
this repository uses).

## 11. Scope prohibitions confirmed untouched

Diffed against the Phase 2C closure commit (`bcfdc00`): `007` (no new
commits, no uncommitted changes), `o7 invoke`, the Codex freeze, the Phase
2B/2C acquisition bridges and scripts, `products/own-audit.yaml`
(thresholds), `src/demand_radar/scoring/judge.py` (weights), objection
codes — all show **zero changes**. The canonical review packet and
canonical run artifacts under `runs/phase-2c/` are, per §2, hash-identical
before and after. No real human review was performed. Nothing here claims
Phase 2C `PASS`, an independent review completed, market validation, or
opportunity completeness.

## 12. Result

```
canonical hashes unchanged:                  PASS
fixture generation:                          PASS
fixture schema/hash validation:              PASS
atomic fixture import:                       PASS
deterministic finalize:                      PASS
agent calls during fixture/finalize:         0
fixture opportunities experiment_ready:      0
fixture opportunities externally_validated:  0
published CI quality-gate:                   success
```

`atomic fixture import` above describes this run's own outcome (19/19
committed together, nothing partial) and the validation-phase guarantee
that was true at the time; it did not yet mean the write phase itself was
structurally guaranteed atomic on a mid-batch failure — see §13, corrected
the same day.

**Scope verdict: PIPELINE_SMOKE_PASS.**

```
Review import/finalize mechanics: PASS
Independent human review: NOT PERFORMED
Market validation: NOT PERFORMED
Canonical Phase 2C status: AWAITING_HUMAN_REVIEW
```

## 13. Correction — write-phase atomicity was not guaranteed at the time of this run

Found by arbiter code review, not by a test or by inspection on my own
part: `import_reviews()` validated a full batch atomically (§5's "zero
mutation" rejection is real and unaffected by this), but the **write**
phase that follows a successful validation was not atomic. Precisely:
`Store.upsert_critic_verdict()` committed once per row, and
`import_reviews()` wrote the archived-input file and appended to
`reviews/provenance.jsonl` interleaved with those per-row commits, before
the batch's last row was even reached. A failure partway through — on
opportunity 10 of 19, say — would have left prior verdicts permanently
committed, `provenance.jsonl` partially written, and the archived-import
artifact fully present, despite the import as a whole never completing.
This run's own 19/19 batch happened not to hit this, since nothing failed
mid-write — so every fact in §1-§12 stands — but the code did not
*guarantee* that outcome the way the report's use of "atomic" implied, and
the gap applies identically to real human-review imports, since
`import_reviews()` is one shared code path for both.

**Fix**, in `src/demand_radar/storage/sqlite.py` and
`src/demand_radar/review.py`:

- New `Store.upsert_critic_verdicts_batch()`: every verdict in a batch is
  staged (`_stage_critic_verdict`, the same `INSERT ... ON CONFLICT` as
  before, minus the commit) inside one SQLite transaction, committed once
  at the end; any exception rolls back and re-raises before anything is
  durable. The existing single-row `upsert_critic_verdict()` (the
  real-time agent-critic path, one verdict per call) is untouched and
  still commits per call — confirmed by the full pre-existing suite
  passing unchanged.
- `import_reviews()`'s write phase now stages both the archived-input file
  and the full combined `provenance.jsonl` content (existing content plus
  the new batch's lines — provenance is append, not overwrite, so the
  staged content is the complete post-import file, not just the delta) to
  `.staging`-suffixed paths, calls
  `upsert_critic_verdicts_batch()`, and only `Path.rename()`s both staged
  files into place after that call returns successfully. Any exception
  (SQL or otherwise) deletes both staged files and raises a new
  `ReviewWritePhaseError` — zero verdicts committed, `provenance.jsonl`
  exactly as it was before the call (untouched if this was the first
  import, unextended if a prior import had already succeeded), no archived
  import artifact.
- Transaction-boundary logic stays inside `Store` rather than exposing
  `commit()`/`rollback()` for `review.py` to call directly — a deliberate
  encapsulation choice made while implementing this, not the first draft.
  **Superseded by §14**: a second arbiter review round found this
  particular choice couldn't survive the next correction — `commit()`/
  `rollback()` ended up needing to be public after all, for a reason this
  paragraph's own reasoning didn't anticipate.

**Verification**: `tests/unit/test_review_import_atomicity.py` (3 new
tests, self-contained, mirroring this repository's existing
one-file-per-concern test convention) inject a failure via a
call-counting monkeypatch on `Store._stage_critic_verdict` — raising on an
exact call number, 1-indexed, across the test — rather than simulating a
real disk/OS failure:

- `test_human_import_write_phase_failure_rolls_back_completely` and
  `test_fixture_import_write_phase_failure_rolls_back_completely`: a
  3-opportunity batch (human and fixture respectively) fails on its 2nd
  write; both assert all 3 opportunities have zero committed verdict,
  `provenance.jsonl` does not exist, and `reviews/imports/` is empty or
  absent.
- `test_second_batch_write_phase_failure_leaves_first_batchs_provenance_untouched`,
  a stronger property than "provenance doesn't exist": a first batch
  (`opp_a`) imports successfully, a second batch (`opp_b`, `opp_c`) then
  fails on its very first write; asserts `opp_a`'s verdict, its
  `provenance.jsonl` line, and its archived-import file are all still
  present and byte-identical to before the second batch was attempted,
  while `opp_b`/`opp_c` have zero verdicts.

Full offline gate re-run after the fix: **257 tests passed** (254 prior +
3 new), `ruff check` clean, `ruff format --check` clean, `mypy --strict
src` clean. The canonical run (`runs/phase-2c/`) was not touched by this
correction — it is a code/test/doc-only change, verified the same way as
every prior correction in this engagement: full offline gate, then
published CI on the commit (see `docs/decisions.log.md`'s matching entry
for the exact run id).

**This section's own "atomic" claim turned out to be incomplete too** —
see §14 for the second, more precise correction the same arbiter review
thread required.

## 14. Second correction — the two `rename()` calls were still outside the transaction boundary

§13's fix made the SQLite side of the write phase genuinely atomic (one
transaction, one commit, rollback on any exception during the insert
loop) but left a second, distinct gap the arbiter's next review caught by
reading the code precisely: both `staged_import_path.rename(...)` and
`staged_provenance_path.rename(...)` ran **after**
`upsert_critic_verdicts_batch()`'s internal commit had already succeeded,
and **outside** the surrounding `try`/`except`. §13's own claim —
"Any exception (SQL or otherwise) deletes both staged files... zero
verdicts committed" — was true for exceptions during validation or during
the SQL insert loop, but not for a failure in either `rename()` call
itself: at that point the verdicts were already durably committed, so a
failure publishing either file would have left one of:

- first `rename` fails: verdicts committed, no import artifact, stale
  provenance, orphaned staging file.
- second `rename` fails: verdicts committed, import artifact published,
  stale provenance, orphaned staged provenance file.

The 3 tests §13 added never exercised this window — they all injected
their failure into `Store._stage_critic_verdict`, strictly before the
commit and both renames, so they could not have caught this even in
principle.

**Fix**, same two files: the commit itself now happens *last*, after both
files are already published, so the operation that's hardest to undo
happens only once nothing durable is left un-coordinated:

1. `Store.upsert_critic_verdicts_batch()` is gone. In its place,
   `Store.stage_critic_verdicts()` inserts every verdict in the current
   transaction without committing, and `Store.commit()`/`Store.rollback()`
   are now public. This does re-expose the transaction boundary that §13
   deliberately kept inside `Store` — necessary this time, not an
   oversight: the commit decision now depends on filesystem operations
   `import_reviews()` performs, in `review.py`, between the insert and the
   commit, so no method living entirely inside `Store` can own the whole
   operation the way `upsert_critic_verdicts_batch()` tried to.
2. `import_reviews()`'s write phase now runs, in order: stage both files
   → insert verdicts (no commit) → back up the existing `provenance.jsonl`
   (`shutil.copy2`, only if one exists yet) → `os.replace` the import
   artifact into place → `os.replace` the staged provenance into place →
   `store.commit()`. Any exception at any step: `store.rollback()`, delete
   the import artifact if it was published, restore `provenance.jsonl`
   from its backup (or delete it, if this was the run's first-ever
   import) if it was replaced, delete every staging/backup file, raise
   `ReviewWritePhaseError`.
3. Stated precisely, per the arbiter's own framing: this is exception-safe
   *within this process*, not a journaled two-phase commit across SQLite
   and the filesystem — a `kill -9` or power loss between the two
   `os.replace` calls is out of scope, the same practical boundary this
   project's other atomicity claims already use.

**Verification**: `tests/unit/test_review_import_atomicity.py` grew by 4
tests, covering exactly the three windows the arbiter named plus the
"prior successful batch survives" property applied to the new window:

- `test_import_artifact_publish_failure_rolls_back_completely` — the
  first `os.replace` (import artifact) fails.
- `test_provenance_publish_failure_after_artifact_published_rolls_back_completely`
  — the second `os.replace` (provenance) fails after the first already
  succeeded; proves the published artifact gets un-published.
- `test_second_batch_provenance_publish_failure_restores_first_batch_byte_identical`
  — same window, against a run with a prior successful import already on
  disk; proves the backup-and-restore path specifically (not just
  delete-the-new-file).
- `test_sqlite_commit_failure_after_both_files_published_rolls_back_completely`
  — the exact case the arbiter named explicitly: `store.commit()` itself
  fails after both files are already published.

Each asserts the same five properties the arbiter specified: zero new
verdicts, prior verdicts (if any) untouched, `provenance.jsonl`
byte-identical to before the failed batch, the import artifact absent (or,
for a second-batch test, the first batch's artifact still present and
nothing extra), and no `.staging`/`.bak` file left anywhere under the run
directory.

**Sanity-checked, not just written and run once**: before trusting these
4 tests, the fix's `store.commit()` call was deliberately moved one line
earlier (immediately after the first `os.replace`, before the second) to
reintroduce the exact bug shape the arbiter found — 2 of the 4 new tests
failed immediately against that mutated code, with `pytest` showing a
committed verdict where the test expected `None`. The correct ordering was
then restored and reverified. This is not part of the permanent test
suite (there is no "mutation test" checked in) — it was a one-time check
that these specific tests actually fail when the bug they target is
present, not just that they pass against already-correct code.

Full offline gate re-run after this second fix: **261 tests passed** (257
prior + 4 new), `ruff check` clean, `ruff format --check` clean, `mypy
--strict src` clean. Canonical run (`runs/phase-2c/`) untouched — again a
code/test/doc-only change.

This report does not declare its own freeze or acceptance status — that
verdict belongs to the arbiter's review, consistent with every other scope
in this engagement. See §15 for that verdict, once given.

## 15. Arbiter final verdict — ACCEPTED / CLOSED / FROZEN

Recorded verbatim (translated), not self-declared: the arbiter's review of
commit `c70ec1e` found the §14 correction closes the write-phase gap
completely, with every property checked directly against the code rather
than taken on trust —

```
Fixture pipeline mechanics:              ACCEPTED
Validation-phase atomicity:              ACCEPTED
SQLite batch transaction:                ACCEPTED
Filesystem publication compensation:     ACCEPTED
Commit-after-publication ordering:       ACCEPTED
Failure-injection coverage:              ACCEPTED
Published CI:                            VERIFIED (run 29469380798, success)

Phase 2C-SMOKE: ACCEPTED / CLOSED / FROZEN
Frozen scope head: c70ec1e7d5e1606db8462db92b4e11bed947d049
```

No further correction commits are required for Phase 2C-SMOKE. This is
the scope's terminal state.

**What freezing this scope does not do**: it does not advance, validate,
or otherwise touch the canonical Phase 2C research verdict, which this
scope was never in a position to affect in the first place (§1, §11) —
the arbiter's own closing line states this explicitly: canonical Phase 2C
research status remains **AWAITING_HUMAN_REVIEW**, exactly where
`docs/trials/phase-2c-combined-trial.md` left it. What froze here is
narrower and purely mechanical: proof that the review export → import →
finalize pipeline handles a completed review batch correctly and
recoverably, including under injected mid-write failure — not that any
opportunity in `runs/phase-2c/` has been reviewed, validated, or moved
closer to `experiment_ready`. A synthetic fixture batch stayed a synthetic
fixture batch throughout; verifying the paperwork process rejects bad
paperwork the same way whether it's fake or real does not make the fake
paperwork real.
