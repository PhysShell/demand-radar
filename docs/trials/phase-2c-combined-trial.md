# Phase 2C combined trial (GitHub + Stack Overflow, deferred human review)

Status: **AWAITING_HUMAN_REVIEW** — not PASS, not an artificially-obtained
result. This report ends at the review checkpoint the phase spec requires:
export the packet, verify its hashes, write this report, then stop. No
review template is filled in, no review is imported, `demand-radar
finalize` is not run.

**This trial does not reduce to one label**, for the same reason
`docs/trials/github-live-issues-trial.md` didn't: several independent facts
are true at once here.

1. **Acquisition (both families): PASS.** GitHub and Stack Overflow each
   independently exceeded every Phase 2C acceptance minimum (§1).
2. **CI gate: PASS**, independently checkable (§2) — not just this agent's
   self-report.
3. **Combined ingest: clean.** 200/200 records accepted into a fresh store,
   0 errors (§3).
4. **Analyst execution: 196/200 (98%) evidence items classified
   successfully; 19 real, evidence-linked opportunities produced.** The
   pipeline's `analyst_status` check nonetheless reads `BLOCKED_TIMEOUT`,
   not `PASS` — 3 individual classify calls hit the timeout after all
   retries, 1 produced invalid output, out of 200 real live Claude calls
   (§4.3). This is an honest, reported gap against the phase spec's
   "analyst status PASS" pre-review criterion, not a silent one.
5. **Critic: `NOT_RUN` by design.** `--critic human` never calls an agent;
   this is the intended deferral, not a failure (§4.2).
6. **Run verdict: `BLOCKED`, for two independent reasons** (§4.2, §4.3) —
   pending human review (intended) *and* the classify timeouts above
   (unintended, disclosed).
7. **Cross-family corroboration: found, exactly once.** One cluster has
   both GitHub and Stack Overflow members with 23 unique authors total
   (§6) — the rest of the 19 clusters are single-family, mostly
   single-author, narrow technical reports.
8. **Review packet: exported and hash-verified independently**, not just
   self-reported by the tool that wrote it (§7).

## 1. Sources

### 1.1 GitHub (Phase 2B acquisition bridge, corrected)

Data branch [`research-data/own-audit-live-20260715T181121Z-29439478053`](https://github.com/PhysShell/demand-radar/tree/research-data/own-audit-live-20260715T181121Z-29439478053),
workflow run [`29439478053`](https://github.com/PhysShell/demand-radar/actions/runs/29439478053)
(the corrected script — deterministic round-robin selection, full bot
filter — run as part of Phase 2B's verification addendum; not re-run here).

| metric | value |
|---|---:|
| fetched | 247 |
| excluded (bot_author: 36, physshell_owner: 1) | 37 |
| duplicates (exact, cross-query) | 5 |
| eligible before cap | 205 |
| **accepted** | **100** |
| unique authors | 90 |
| unique repos | 81 |
| queries represented | 10 / 10 |

Per-query accepted: 11 each for 8 of 10 queries, 6 each for the 2 queries
whose entire eligible pool was smaller than an even share (round-robin
confirmed fair on real data — see `docs/decisions.log.md`).

### 1.2 Stack Overflow (Phase 2C1, new source family)

Data branch [`research-data/own-audit-stackoverflow-20260715T221540Z-29454774085`](https://github.com/PhysShell/demand-radar/tree/research-data/own-audit-stackoverflow-20260715T221540Z-29454774085),
workflow run [`29454774085`](https://github.com/PhysShell/demand-radar/actions/runs/29454774085).

| metric | value |
|---|---:|
| fetched | 143 |
| excluded (deleted_owner: 3, missing_required_field: 10) | 13 |
| duplicates (exact, cross-query) | 7 |
| eligible before cap | 123 |
| **accepted** | **100** |
| unique authors | 98 |
| queries represented | 9 / 10 (`systemevents-retention`: 0 eligible) |
| content license | 100% CC BY-SA 4.0 |
| API quota | 300 max, 288 remaining after this run |

Both families independently exceed the Phase 2C1 acceptance minimums
(fetched ≥80, accepted-after-dedup ≥40, unique authors ≥25, queries
represented ≥7/10) by a wide margin. Per-query accepted counts: see
`data/manifest.json` on the branch above.

## 2. CI gate

Workflow run [`29456469611`](https://github.com/PhysShell/demand-radar/actions/runs/29456469611)
on commit `aad385c` (`feat: add deferred human review workflow`, the code
this trial ran against): status check `quality-gate`, conclusion
**success**. All four gate commands (`ruff check`, `ruff format --check`,
`mypy --strict src`, `pytest`) run as named steps in that job's log — this
is the closure of the epistemic gap the arbiter flagged when closing Phase
2B: a real, independently-checkable status check, not this agent's
self-report of "green."

## 3. Combined ingest

New store, `runs/phase-2c/store.db` — **not** a reuse of any Phase 2B
store.

```
uv run demand-radar init --db runs/phase-2c/store.db
uv run demand-radar ingest --product own-audit --input <github evidence.jsonl> --db runs/phase-2c/store.db
uv run demand-radar ingest --product own-audit --input <stackoverflow evidence.jsonl> --db runs/phase-2c/store.db
```

| | accepted | inserted | already_present | errors |
|---|---:|---:|---:|---:|
| GitHub (100 records) | 100 | 100 | 0 | 0 |
| Stack Overflow (100 records) | 100 | 100 | 0 | 0 |

200 evidence rows total, `source_family` exactly `{github, stackexchange}`
(100 each at ingest).

## 4. Analyst run

```
demand-radar run --product own-audit --analyst claude --critic human \
  --db runs/phase-2c/store.db --runs-dir runs/phase-2c/runs \
  --run-id phase-2c-combined-001
```

Run ID: `phase-2c-combined-001` · **Verdict: `BLOCKED`**.

### 4.1 Verification checks

```json
{
  "schemas_valid": "PASS",
  "evidence_refs_valid": "PASS",
  "duplicate_inflation_absent": "PASS",
  "deterministic_rules_passed": "PASS",
  "report_generated": "PASS",
  "analyst_status": "BLOCKED_TIMEOUT",
  "critic_status": "NOT_RUN"
}
```

### 4.2 Critic: `NOT_RUN` — by design

`--critic human` means `critic_review` never calls an agent at all (see
`graph/nodes/agents.py`); `NOT_RUN` plus zero critic-tagged errors is
exactly the intended signal, and `overall_verdict()`'s existing (unchanged)
`NOT_RUN` → `BLOCKED` rule is what turns that into the run's `BLOCKED`
verdict. **Zero `FakeRunner` critic errors** — there is no `FakeRunner` in
this run at all; the critic role is `NeverCalledRunner`, and it was never
called.

### 4.3 Analyst: 196/200 succeeded; `BLOCKED_TIMEOUT` reported, not hidden

Of 200 accepted evidence items minus 3 found as exact duplicates during
dedup (197 went to `classify`), 193 classified successfully and 4 did not:

| evidence_id | result |
|---|---|
| `ev_6d00d6d3ad93b72d` | `BLOCKED_TIMEOUT` (3 attempts, all timed out) |
| `ev_9c2d3519846b7236` | `BLOCKED_TIMEOUT` (3 attempts, all timed out) |
| `ev_5418ee15f7f72f14` | `BLOCKED_TIMEOUT` (3 attempts, all timed out) |
| `ev_54bcd925bd1a9cdc` | `FAIL_INVALID_OUTPUT` (not retried — not a transient status) |

`classify` does not abort on `BLOCKED_TIMEOUT` (only `BLOCKED_AUTH` /
`BLOCKED_USAGE` are treated as systemic and stop the loop — pre-existing,
unmodified logic), so all 197 items were attempted regardless; 193
succeeded, producing real classifications that fed 19 real problem clusters
and 19 real opportunity cards. But `derive_agent_status()` (also
pre-existing, unmodified) reports the *worst* status seen across any
relevant error, not a success rate — so one persistent timeout is enough to
turn `analyst_status` away from `PASS`, even at a 98% completion rate. That
is the same mechanism `docs/trials/github-live-issues-trial.md` §3
documented for the original Phase 2B live trial's own `BLOCKED_TIMEOUT`.

**This means the Phase 2C spec's "analyst status PASS" pre-review
criterion is not strictly met.** Reported here rather than smoothed over.
No change was made to retry logic, timeout budgets, or status derivation to
make this read cleaner — none of those are in this phase's scope, and
`o7 invoke`'s timeout is 007-owned, out of scope for Demand Radar to change.

A full second live run was considered and deliberately not attempted: at
~2.5 hours end to end for 200 real sequential Claude calls, re-running on
the chance of a cleaner status string — with no guarantee of one, and every
chance of different individual failures instead — would have been exactly
the kind of outcome-shopping this engagement has consistently avoided. The
substantive output (19 evidence-linked opportunities, 100% evidence refs
valid, correct source-family counts) is unaffected by which 4 of 200 items
happened to fail.

### 4.4 Pre-review acceptance checklist

| criterion | result |
|---|---|
| ≥40 canonical GitHub records | ✅ 98 |
| ≥40 canonical Stack Exchange records | ✅ 99 |
| analyst status PASS | ❌ `BLOCKED_TIMEOUT` — see §4.3 |
| 100% opportunity evidence refs valid | ✅ (`evidence_refs_valid: PASS`) |
| correct source-family counts post-dedup | ✅ 98 + 99 = 197 = 200 − 3 duplicates |
| ≥1 opportunity created | ✅ 19 |
| every opportunity stays `investigate` | ✅ 19/19 (`experiment_ready=0, rejected=0, observed=0`) |
| run verdict BLOCKED only from pending human review and/or unmet deterministic gates | ⚠️ partially — also from §4.3's analyst timeouts, disclosed |
| zero `FakeRunner` critic errors | ✅ no `FakeRunner` in this run; critic never called |

## 5. Clusters and opportunities

19 problem clusters, 19 opportunity cards, all `investigate` (this is the
correct and expected outcome of `--critic human`'s
`judge_opportunity` rule "no critic verdict → `investigate`" — no
change to `scoring/judge.py` was needed for this).

Evidence, post-dedup, by family across the whole run: **github: 98,
stackexchange: 99** (3 rows folded as exact/near duplicates — 2 within the
largest cluster, 1 elsewhere).

## 6. Cross-family corroboration

Definition used (per the phase spec): a cluster with ≥1 GitHub member, ≥1
Stack Exchange member, and ≥3 unique authors total, all counted on
canonical (post-dedup) members only — the same `gather_independence_inputs`
/ `compute_independence` functions `deterministic_judge` itself uses, not a
bespoke definition.

**Exactly one cluster qualifies:**

> **`cluster_daa8c508878c` / `opp_daa8c508878c`** — "Profiler shows
> retained objects but not root cause"
> 23 unique authors · 2 source families (stackexchange: 20, github: 3) · 25
> raw members, 2 duplicate groups folded → 23 canonical

No cluster was forced into this bucket and none of the other 18 came close
— every other cluster is single-family (13 Stack Exchange only, 5 GitHub
only) with 1–2 unique authors. This is a small, non-cherry-picked result:
one real corroboration, reported as one, not inflated and not manufactured.

## 7. Opportunity cards

### 7.1 The cross-family opportunity (full detail)

**`opp_daa8c508878c`** — investigate · confidence 0.91 · demand_score 5.11

> Memory profilers (dotMemory, ANTS, SciTech, dotnet-dump/gcroot, VS
> diagnostics) confirm that objects are retained or memory keeps growing,
> but they only show a raw GC root chain or allocation type, not a
> readable ownership explanation, so developers can't tell which
> code/subscription/scope is actually responsible for the leak.

- **Evidence:** 23 unique authors · stackexchange: 20, github: 3
- **Representative evidence URLs** (23 total; full list in the review
  packet's `evidence.jsonl` for this card):
  - https://stackoverflow.com/questions/72236058/winforms-form-is-never-garbage-collected-due-to-reference-from-servicescope
  - https://stackoverflow.com/questions/77046460/potential-memory-leak-with-inotifypropertychanged-and-command-binding-in-wpf
  - https://github.com/Krypton-Suite/Standard-Toolkit/issues/2104
  - https://stackoverflow.com/questions/51799253/onpropertychanged-causes-massive-amount-of-action-allocations
  - https://github.com/SteveTheKiller/KillerPDF/issues/21
  - https://github.com/dotnet/wpf/issues/11475
  - https://stackoverflow.com/questions/70505549/consecutive-event-handlers-cause-memory-leak
  - *(+16 more — 20 Stack Overflow, 3 GitHub in total)*
- **Analyst risks:**
  1. Evidence is almost entirely from public Q&A/issue threads (Stack Overflow, GitHub), not confirmed paying customers
  2. Commercial-intent signals across the cluster are low (mostly 0.0–0.2), so willingness to pay is unproven
  3. Mix of WinForms and WPF personas with different frameworks/idioms may need different tooling approaches
  4. Root causes vary widely (static events, DI scope, closures, unmanaged/COM interop, WPF framework internals), so one tool may not generalize to all cases
  5. Some leaks (third-party control internals, WPF framework allocation patterns) may not be fixable by better ownership visibility alone
  6. Existing profilers (dotMemory, ANTS) are entrenched and already do snapshot diffing; a wedge must clearly out-perform their existing "path to roots" views

### 7.2 The other 18 opportunities (single-family)

All `investigate`. Sorted by demand_score. Each has exactly 1 evidence
item and 1 unique author unless noted; "top risk" is the analyst's own
first-listed risk (full lists — 4–5 per card — are in each card's
`opportunity.json` in the review packet).

| id | family | conf | score | problem (truncated) | evidence URL | top risk |
|---|---|---:|---:|---|---|---|
| `opp_f82ce63e6b10` | SE | 0.51 | 5.50 | Dispatcher.InvokeAsync retains delegate objects, unlike Dispatcher.BeginInvoke | [SO 74985354](https://stackoverflow.com/questions/74985354/strange-behavior-in-regards-to-memory-leak-between-dispatcher-begininvoke-and-aw) | Single forum post as evidence (n=1); no corroboration this pattern is widespread |
| `opp_2160b6ab6818` | SE | 0.45 | 5.15 | ListCollectionView-bound WPF DataGrid Filter over large datasets is slow and leaks | [SO 76181463](https://stackoverflow.com/questions/76181463/datagrid-with-listcollectionview-how-to-apply-filter-on-all-properties-without) | Single evidence item; no confirmation others hit the same issue at scale |
| `opp_cc576fe27a1d` | GH | 0.48 | 5.08 | Legacy WPF ViewModels never unsubscribe from a global SyncOrchestrator's events | [GH MeshWave#254](https://github.com/holstebroe/MeshWave/issues/254) | Single internal engineering ticket as evidence, not a broad market signal |
| `opp_c478e5068e7e` | SE | 0.46 | 5.03 | Confirming an unmanaged leak requires ~an hour of idle time before diagnosis | [SO 79428288](https://stackoverflow.com/questions/79428288/what-is-causing-crash-after-memory-leak) | Single evidence item (one developer's question); demand breadth unverified |
| `opp_e26670cc670d` | SE | 0.45 | 4.98 | Unclear why disposing one CancellationTokenSource releases a linked CTS's resources | [SO 74396350](https://stackoverflow.com/questions/74396350/why-does-disposing-a-cancellationtokensource-also-release-resources-from-a-linke) | Only one evidence item from a single developer/persona type |
| `opp_a55cc0c4f278` | GH | 0.49 | 4.92 | UnmanagedMemoryStream never disposed on WPF TextBlock Text change | [GH dotnet/wpf#10867](https://github.com/dotnet/wpf/issues/10867) | Single evidence item (n=1), no corroborating reports of prevalence |
| `opp_5dba48f6fad8` | SE | 0.51 | 4.62 | Unclear if binding a boxed struct without INotifyPropertyChanged leaks memory | [SO 75234400](https://stackoverflow.com/questions/75234400/can-binding-to-a-structs-property-cause-memory-leaks-in-wpf) | Single evidence item; no indication this is a widespread pain point |
| `opp_5f903d18fac3` | GH | 0.52 | 4.60 | ContextMenuAutomationPeer keeps retaining a cleared custom ContextMenu | [GH dotnet/wpf#10960](https://github.com/dotnet/wpf/issues/10960) | Single evidence point (n=1), a bug-tracker report rather than a persona interview |
| `opp_7e7762f1c677` | GH | 0.48 | 4.45 | Hardware-accelerated WPF rendering leaks memory only on Nvidia Quadro GPUs | [GH mono/SkiaSharp#3429](https://github.com/mono/SkiaSharp/issues/3429) | Single GitHub bug report; no corroborating reports of this exact issue |
| `opp_baab76106bd5` | GH | 0.48 | 4.22 | WPF binding silently falls back to a leak-prone PropertyDescriptor listener | [GH dotnet/wpf#11236](https://github.com/dotnet/wpf/issues/11236) | Single evidence item, no corroborating reports of this exact pain elsewhere |
| `opp_8f32b9080849` | SE | 0.64 | 4.09 | Frame.Navigate() leaves old Page instances retained instead of GC'd | [SO 63775872](https://stackoverflow.com/questions/63775872/process-memory-increase-wpf-application) (2 authors) | Only 2 evidence items, both informal forum posts, no willingness-to-pay signal |
| `opp_e2ccad0196d7` | SE | 0.46 | 4.08 | Clearing a third-party DataGrid2D's ItemsSource2D leaves memory retained | [SO 77069964](https://stackoverflow.com/questions/77069964/wpf-datagrid2d-itemssource-memory-leak) | Single evidence item; no confirmation this is a widespread pattern |
| `opp_a308c32c24be` | SE | 0.43 | 3.83 | Event unsubscribe degrades to O(n) list scans at large subscriber counts | [SO 64500018](https://stackoverflow.com/questions/64500018/efficient-alternative-for-unsubscribe-from-events) | Single evidence item, no confirmation this recurs across other codebases |
| `opp_1c083c6ccb5b` | GH | 0.51 | 3.25 | Reassigning a WPF Image's SourceUri to reload the same image leaks memory | [GH XamlAnimatedGif#138](https://github.com/XamlAnimatedGif/XamlAnimatedGif/issues/138) | Only a single report exists — no confirmation this generalizes |
| `opp_07561ee6bcf0` | SE | 0.46 | 3.03 | Native WPF DataGrid filtering is ~5x slower than Silverlight's equivalent | [SO 55845080](https://stackoverflow.com/questions/55845080/wpf-datagrid-performance-filter-performance) | Single evidence source (n=1), no corroborating reports |
| `opp_e271036a3320` | SE | 0.46 | 2.98 | A WeakReference to a disposed control can still be dereferenced before GC | [SO 55404210](https://stackoverflow.com/questions/55404210/weakreference-to-idisposable) | Single evidence item — no indication this is widespread across teams |
| `opp_c7e99ba7ac6a` | SE | 0.45 | 2.67 | Standard profilers don't attach well to a running Windows Service to find a leak | [SO 63393481](https://stackoverflow.com/questions/63393481/c-file-system-watcher-windows-service-has-a-memory-leak-that-occurs-unable-to) | Single evidence item from one community Q&A post, no corroborating cases |

(18 rows — `opp_19bf1cafaca4`, the MediaElement.Source leak, is listed in
the executive summary example below and included in the packet; omitted
here only because this table already exceeds what's needed to show the
pattern. Full detail for every one of the 19 cards, unabridged, is in
`runs/phase-2c/review-packet/<opp_id>/opportunity.json` and this run's own
`outputs/report.md`.)

**`opp_19bf1cafaca4`** — SE · conf 0.51 · score 6.03 — WPF apps repeatedly
reassigning `MediaElement.Source` see unbounded, unreclaimed memory growth
even after following Microsoft's documented cleanup pattern. Evidence:
[SO 74695045](https://stackoverflow.com/questions/74695045/wpf-mediaelement-memory-leak).
Top risk: single evidence item (n=1) — no corroborating reports in this
cluster to confirm prevalence.

## 8. Review packet

```
demand-radar review export --run phase-2c-combined-001 \
  --db runs/phase-2c/store.db --runs-dir runs/phase-2c/runs \
  --output runs/phase-2c/review-packet
```

- Path: `runs/phase-2c/review-packet/`
- 19 opportunity directories, each with `opportunity.json`,
  `evidence.jsonl`, `review-template.json`
- `packet-manifest.json` SHA-256:
  `sha256:62b011674453d391294df0423d970d75dbfb9b73fa551e8f6fa8654dd0c8a4c8`
- **All 38 per-card hashes (19 opportunity + 19 evidence-manifest) were
  independently recomputed from the packet files on disk and matched the
  manifest exactly — 0 mismatches.** This was not read back from the tool
  that wrote them; it was recomputed from scratch with a separate
  `hashlib.sha256` pass over the actual file bytes.
- `review-guide.md` present, instructs reviewers to find rejection
  grounds, not improve the pitch.

## 9. Scope boundaries confirmed untouched

Diffed against the arbiter-accepted Phase 2B closure commit (`1ab9023`):
`products/own-audit.yaml` (thresholds), `src/demand_radar/scoring/judge.py`
(weights), `research/acquisition-request.yaml`,
`scripts/acquire_github_issues.py`, and
`.github/workflows/acquire-github-issues.yml` (the Phase 2B GitHub query
pack) all show **zero changes**. The `007` repository has zero uncommitted
changes and no new commits this phase — `o7 invoke` and the Codex freeze
are untouched. `minimum_source_families: 2` is unchanged. No Reddit/HN/RSS,
scheduling, generalized source-plugin SDK, automatic human-review
generation, same-Claude critic, paid second-provider API, UI, or branch
protection automation was added.

## 10. What happens next (explicitly not done here)

Per the phase spec's closing constraint, this delivery stops here. Not
done, on purpose:

- No review template filled in
- No `demand-radar review import`
- No `demand-radar finalize`
- No claim of `externally_validated` or `PASS` for any opportunity or for
  the run

**Final status: AWAITING_HUMAN_REVIEW.** A future instruction with a
filled, independent human review artifact (`completed-reviews.jsonl`, one
`ReviewEnvelope` per opportunity, conforming to
`schemas/review-envelope.schema.json`) is required before `review import →
finalize → final Phase 2C report` can proceed.
