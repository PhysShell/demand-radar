# Phase 2C-SMOKE: synthetic review pipeline test

Status: **PIPELINE_SMOKE_PASS** for this scope. **Canonical Phase 2C status
is unchanged: AWAITING_HUMAN_REVIEW.**

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
rejection, malformed nested `CriticVerdict` rejection, atomic batch import,
provenance marked synthetic, a complete fixture set reaching mechanics
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

**Scope verdict: PIPELINE_SMOKE_PASS.**

```
Review import/finalize mechanics: PASS
Independent human review: NOT PERFORMED
Market validation: NOT PERFORMED
Canonical Phase 2C status: AWAITING_HUMAN_REVIEW
```
