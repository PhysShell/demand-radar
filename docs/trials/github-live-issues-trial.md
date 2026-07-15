# GitHub live issues trial (Phase 2B)

Status: complete · **This trial does not reduce to one BLOCKED/PASS label.**
An earlier version of this report collapsed several distinct facts into one
causal story ("BLOCKED because of the critic"); that was wrong on the
mechanism and is corrected below. §10 gives the full reasoning; the short
form is:

1. **Acquisition verdict: PASS.**
2. **Analyst discovery result: USEFUL BUT BIASED.**
3. **Production run verdict: BLOCKED** — immediate cause: the analyst's own
   `BLOCKED_TIMEOUT`, **not** the critic.
4. **Critic status: FAIL_INVALID_OUTPUT** — cause: `FakeRunner`'s `{}`
   default fails schema; a separate fact from #3, and would only have
   produced `FAIL` (not `BLOCKED`) on its own.
5. **Validation status: not `experiment_ready`, for two independent
   reasons** — a missing live critic, *and* `products/own-audit.yaml`'s
   `minimum_source_families: 2` gate, unmet by an all-`github` dataset
   regardless of critic status.

This is a **real live trial**, not a replay: every record below came from
GitHub's Search API through the Phase 2B acquisition bridge
(`.github/workflows/acquire-github-issues.yml`, run
[`29430642497`](https://github.com/PhysShell/demand-radar/actions/runs/29430642497)),
ingested through `demand-radar ingest`, and run through the real production
pipeline (`demand-radar run --analyst claude --critic fake`) — the actual
CLI, not a one-off experiment script. The acquisition and analyst stages
succeeded outright and produced genuinely strong, evidence-linked output.
See §10 for why the run's BLOCKED status is a different kind of "not done"
than Phase 2A's `INSUFFICIENT_EXTERNAL_RECORDS` — and why it is *also* a
different fact from the critic's own status, which an earlier version of
this document conflated.

## 1. Exact queries

10 queries, `research/acquisition-request.yaml`, request commit
`8c11c545280d56e8b4898fd957a21faf68cb96fd`:

| id | query | fetched |
|---|---|---:|
| `memory-leak-csharp` | `"memory leak" language:C# is:issue` | 30 |
| `wpf-memory-leak` | `"WPF memory leak" is:issue` | 30 |
| `event-handler-leak-csharp` | `"event handler leak" language:C# is:issue` | 30 |
| `idisposable-leak-csharp` | `"IDisposable leak" language:C# is:issue` | 30 |
| `systemevents-leak-csharp` | `"SystemEvents" leak language:C# is:issue` | 16 |
| `propertychanged-performance-wpf` | `"PropertyChanged performance" WPF is:issue` | 22 |
| `retained-object-wpf` | `"retained object" WPF is:issue` | 8 |
| `large-wpf-application-performance` | `"large WPF application" performance is:issue` | 21 |
| `dotnet-framework-memory-profiler` | `".NET Framework" memory profiler is:issue` | 30 |
| `dependency-cycle-csharp-architecture` | `"dependency cycle" C# architecture is:issue` | 30 |

## 2. Raw / fetched / excluded / deduplicated counts

From `data/manifest.json` on branch `research-data/own-audit-live-20260715T160205Z-29430642497`
(sha256 of evidence.jsonl: `62702be5...ac7466`, verified against a locally
re-hashed copy before use):

```
fetched total:        247
excluded:              17  (bot_author: 16, physshell_owner: 1)
duplicates (query overlap, same source_id): 2
over max_records cap: 128
accepted (evidence.jsonl):    100
schema_validation_failures:     0
```

Then, through `demand-radar ingest` (production) and `demand-radar run`'s
own similarity-based dedup (different from the acquisition script's
exact-source_id dedup above — this one compares text content):

```
ingested:            100  (accepted=100 inserted=100 errors=0)
independent after production dedup: 96   [as actually run, see correction below]
duplicate groups found: 3  (4 evidence items folded in)
```

The 3 production-dedup groups, by canonical identity and similarity score:

- `FirebirdSQL/NETProvider#195` ← `#196` (0.802), `#197` (0.776) — three
  bulk-migrated Jira subtasks about the same MemoryStream leak family (see §7).
- `Sandip124/BatteryNotifier#58` ← `#68` (0.820) — two similar crash reports
  in the same small hobby project.
- `EWSoftware/VSSpellChecker#30` ← `NuGet/Home#3474` (0.594) — **a confirmed
  false collapse**, not a borderline judgment call: these are unrelated
  problems (a missing Tools-menu item vs. NuGet Package Manager freezing
  Visual Studio). Both issue bodies happen to be auto-generated Visual
  Studio environment dumps (a long list of installed VS components/
  extensions), which share enough boilerplate text to inflate TF-IDF cosine
  similarity to 0.594 — above this run's single `similarity_threshold=0.5`.

**Correction — this is fixed, and independently verified against this exact
real data, not just synthetic fixtures.** `ingest/deduplicate.py` now takes
a separate, stricter `cross_repo_similarity_threshold` (0.75, configured in
`products/own-audit.yaml`) applied only to pairs from different
repositories; same-repo pairs are unaffected. Re-running
`deduplicate_evidence()` directly (offline, no agent calls, no re-ingest)
against this trial's actual 100-record `evidence.jsonl` confirms the fix
is exact:

```
OLD (similarity_threshold=0.5 only):        4 links, 3 groups, 96 independent
NEW (+ cross_repo_similarity_threshold=0.75): 3 links, 2 groups, 97 independent
VSSpellChecker#30 <-> NuGet/Home#3474 linked: True (old) -> False (new)
links removed by the fix: exactly 1 (this pair); links added: 0
```

The FirebirdSQL and BatteryNotifier groups (both genuinely same-repo,
genuinely near-duplicate) are untouched — the fix is scoped precisely to
the false cross-repo collapse it was written to fix, nothing more, nothing
less. **What this correction does *not* do:** the §4-§8 cluster/candidate
analysis below still reflects the *original* 96-independent-item run — it
is not re-clustered or re-classified here, per explicit instruction not to
re-spend the 100-call analyst budget on this correction. One concrete,
known consequence: `NuGet/Home#3474` (a NuGet Package Manager freeze in a
large WPF solution — squarely on-topic for the `large-wpf-application-
performance` query that fetched it) was silently absorbed into an unrelated
canonical record in the original run and never received its own `classify`
call, so it does not appear anywhere in §4-§8. Giving it a fair, independent
classification would take exactly one incremental analyst call against the
now-97-item independent set — worth doing in a future pass, out of scope
for this correction.

## 3. Repositories and authors

**77 unique repositories**, **90 unique issue openers** in the accepted
100-record set (both computed from the committed manifest).

**Correction, found by manual reading, not assumed:** `firebird-automations`
(7 records) and `GoogleCodeExporter` (3 records) are migration/export bot
accounts that the acquisition script's bot filter did not catch — they
don't match the `[bot]` suffix convention or the curated exact-match list.
`ironpythonbot` and `orchardbot` (1 record each) are also bot-like accounts
the filter missed. Reading `FirebirdSQL/NETProvider#189`'s body confirms
this directly: *"Submitted by: Sergey Merkushov (merkushov)"* — the real
reporter's name is in the migrated text, not in GitHub's structured
`user.login` field, which instead shows the import bot. Corrected count:
**86 real human authors**, not 90 — still comfortably over the ≥20 minimum,
but the 90 figure in the committed manifest should not be read as 90
independent people.

**Correction — the acquisition script's bot filter now catches exactly
these 4 accounts.** `classify_item()` gained a `user.type == "Bot"` check
(an independent signal from GitHub's own API, alongside the existing login
list) plus these 4 logins added to the curated exact-match set, with
regression tests for all 4 by name. This dataset (run `29430642497`) is
*not* re-fetched or re-classified to reflect the fix — the 90-reported /
86-corrected figures above describe this trial's real, already-committed
data as originally collected, unchanged. A fresh acquisition run confirming
these 4 accounts are now excluded at the source is recorded separately (see
the correction note referenced from `docs/decisions.log.md`), without
re-spending this trial's 100-call analyst budget.

Notable repos (of 77): `MahApps/MahApps.Metro`, `PrismLibrary/Prism`,
`Krypton-Suite/Standard-Toolkit`, `aspnet/DependencyInjection`,
`AvaloniaUI/Avalonia`, `MvvmCross/MvvmCross`, `autofac/Autofac`,
`SonarSource/sonar-dotnet`, `aws/aws-sdk-net`, `castleproject/Core`,
`cefsharp/CefSharp`, `SignalR/SignalR`.

## 4. Top problem clusters

The production pipeline formed 8 clusters from the 96 independent evidence
items produced by that run (77 items never joined a reported cluster — see
§5). All 8 reached `opportunity` candidate status; all 8 are capped at
`investigate` (§10). As corrected in §2, this 96-item set includes the now-
confirmed false `VSSpellChecker#30`/`NuGet/Home#3474` collapse; the
corrected, dedup-only count is 97 independent items, but clustering was not
re-run against that corrected set (§2 explains why and what specifically is
missing as a result: `NuGet/Home#3474` itself).

1. **Memory profilers fail to identify leak root cause/ownership** —
   11 unique authors, 12 evidence items, confidence 0.74 (the strongest
   cluster by a wide margin). Existing tools (dotMemory, .NET Memory
   Profiler, WinDbg `!gcroot`, vendor support escalations to Telerik/
   JetBrains) show *that* something is retained but not *who owns it or
   where to fix it* — developers manually walk reference chains or build
   minimal repros to isolate the cause. This is a genuinely differentiated
   framing: not "detect leaks" (existing tools already do that) but "map
   retention to ownership," which is closer to what a Roslyn-based audit
   tool could uniquely add.
2. **Custom binding syntax keeps ViewModel alive** (MvvmCross "Tibet"
   binding) — 1 author, evidence from MvvmCross 3.0.14 (2013-era);
   possibly since fixed.
3. **`DependencyPropertyDescriptor.AddValueChanged` retains view tree**
   (SyncTrayzor) — 1 author in this cluster, but the identical named API
   also appears in `MahApps/MahApps.Metro#2064`, which clustered separately
   (into cluster 1 above, as one of its 12 evidence items) — see §5 for why
   this matters.
4. **`PropertyMetadata` delegates not disposed** (RadicalFx/Radical, a
   niche, low-activity legacy framework) — 1 author.
5. **Media change leaks memory until crash** (Sascha-L/WPF-MediaKit) —
   1 author; a *different* issue in the same repo (`#26`, seen during
   manual review, not part of this cluster) is literally titled "Project is
   abandoned - any new home?" — reduces the addressable value of tooling
   aimed at this specific library.
6. **PieSlice event handlers never detached** (CatalystCode/radial-menu) —
   1 author, small/niche control library.
7. **`DelegateCommand` holds strong references** (`Prism#844`) — 1 author in
   this cluster; `Prism#345` (2015, a different, earlier reporter, same root
   cause: `DelegateCommand` uses a plain event instead of `CommandManager`/
   `WeakReference`) landed inside cluster 1 instead as one of its 12
   evidence items, not merged with this one.
8. **Reloading GIF via `SourceUri` leaks memory** (XamlAnimatedGif) —
   1 author, narrow scope.

**Clustering-granularity observation:** clusters 3 and 7 each look
under-counted against independent manual reading — the same specific root
cause (`AddValueChanged`; `DelegateCommand`'s missing weak-reference
pattern) has a second, independent real-world instance that the pipeline
routed into cluster 1 instead of merging with clusters 3/7. This isn't a
correctness bug (evidence isn't lost or fabricated, cluster 1's 12-item
count is real), but a human re-grouping these before further prioritization
would likely produce a tighter, more accurately-corroborated picture than
the 8-cluster surface reading alone.

## 5. Tutorial / support noise

77 of 96 independent evidence items never joined a reported cluster — most
of these are query-level false positives, confirmed by manually reading a
sample rather than assumed from the query text alone:

- `Antergos/Cnchi#319` (dependency-cycle-csharp-architecture) — a Linux
  installer network-retry request; no C# connection.
- `QubesOS/qubes-issues#2183` (dependency-cycle-csharp-architecture) — a
  Debian kernel-boot bug on Qubes OS.
- `Quuxplusone/LLVMBugzillaTest#8913` (dependency-cycle-csharp-architecture)
  — an LLVM Bugzilla-mirror repo; a compiler-backend ILP issue, not C#.
- `conda/conda#1967` (dependency-cycle-csharp-architecture) — Python package
  manager dependency resolution.
- `EricssonResearch/openwebrtc#260`, `PlayScriptRedux/playscript#24`,
  `bazz1tv/SDL2-Xcode-Template#2`, `SynoCommunity/spksrc#671`
  (dotnet-framework-memory-profiler) — WebRTC/iOS, Mono merge tracking, an
  Xcode template crash, and a Synology package request respectively; none
  about .NET memory profiling.
- `Sandip124/BatteryNotifier#58`/`#68` (systemevents-leak-csharp) — generic
  "unhandled exception"/resource-exhaustion crash reports, not actually
  about a `SystemEvents` subscription leak; the query matched on the word
  "SystemEvents" appearing incidentally.

The two weakest queries by false-positive rate are `dependency-cycle-
csharp-architecture` and `dotnet-framework-memory-profiler` — both too
loosely scoped (GitHub's search matches "C#" and "architecture"/"memory"/
"profiler" too permissively as independent terms rather than as a specific
technical phrase). `wpf-memory-leak`, `idisposable-leak-csharp`, and
`memory-leak-csharp` were the highest-signal queries by contrast.

## 6. Technical issue vs. commercial pain

Overwhelmingly **technical**, not commercial, and the analyst's own scoring
says so without being told to: every one of the 8 opportunity candidates
has `commercial_intent` between 0.0 and 0.1 and `commitment` at or near
0.0 — the analyst explicitly flags this in nearly every card's risk list
("no willingness-to-pay signal," "this was a free bug report, not a
purchase inquiry"). Real, substantive technical detail is common (root
causes, repro code, retention-path traces); explicit demand-for-a-product
signal is essentially absent in this sample.

One real exception, not itself a complaint but a supply-side signal worth
naming: `SonarSource/sonar-dotnet#3904` is a commercial static-analysis
vendor (SonarQube/SonarLint) proposing to build a rule for almost exactly
this problem class (`S6112`, explicit event-unsubscription, citing
`SystemEvents` by name as a special case). That's evidence a well-funded
mainstream tooling vendor thinks this space matters — real market
validation of the technical category — but it is not evidence of unmet
demand from OwnAudit's own prospective customers, and should not be
conflated with the commercial_intent score above.

## 7. Source bias

- **Sort-order bias, a real design tradeoff surfaced by this trial, not
  anticipated going in:** the acquisition script fetched `sort=created&
  order=asc` (oldest matching issue first) for determinism *at the time of
  this run*. Combined with a 30-per-query fetch cap, this systematically
  favored OLD issues: most accepted records date from 2014-2017; only the
  two Krypton-Suite reports (2025, 2026) reflect anything resembling
  current/active pain. **Correction — fixed in the script, not re-run for
  this dataset:** the acquisition script now fetches `order=desc` (newest
  first per query); determinism no longer depends on fetch order at all —
  it now comes from deterministic round-robin selection across queries plus
  a final sort by `source_id` for serialization (§2's dedup fix and this
  selection fix were bundled in the same correction; see the correction
  note). This trial's own 100 records were fetched under the old `order=asc`
  behavior and are not re-fetched; the old-issue skew described above still
  accurately describes *this* dataset. A fresh run confirming `order=desc`
  and fair cross-query selection is recorded separately.
- **Migration/bulk-import contamination**: `FirebirdSQL/NETProvider`'s 7
  records and 3 `GoogleCodeExporter` records are historical Jira/Google Code
  migrations, not organic GitHub-native activity (§3).
- **Query-shape bias**: two of ten queries (§5) skew toward false positives;
  the other eight are tightly on-topic.
- **GitHub-only, `is:issue`-only.** No Stack Overflow, no Reddit, no forum
  threads, no PR discussions — a narrower slice of "developer pain" than
  the full real-world signal, consistent with the Phase 2B scope
  constraint (no additional sources).

## 8. Manual review of each candidate

| Candidate | Classification | Why |
|---|---|---|
| 1. Memory profilers fail to identify ownership | **useful** | Strongest evidence in the set: 11 independent authors, named existing substitutes, confidence 0.74, a differentiated framing (ownership mapping, not leak detection) that existing commercial tools don't cover per the evidence itself. |
| 2. Custom binding syntax (MvvmCross Tibet) | **unsupported** | Single 2013-era data point; framework may have changed since; a free workaround (revert to standard binding) already fully resolved it per the evidence. |
| 3. `AddValueChanged` retains view tree | **potentially_testable**, likely under-clustered | Real, specific, technically differentiated pattern; manual reading shows a second independent instance (MahApps.Metro) that clustered elsewhere — re-group before deciding. |
| 4. `PropertyMetadata` delegates undisposed | **unsupported** | Single instance, niche/low-activity framework (Radical), no corroboration. |
| 5. Media change leaks memory | **unsupported** | Single instance in a library whose own maintainer has a separate "project is abandoned" issue open. |
| 6. PieSlice handlers undetached | **unsupported** | Single instance, small/niche control library. |
| 7. `DelegateCommand` holds strong refs | **potentially_testable**, likely under-clustered | Same situation as #3 — a second, earlier (2015) independent Prism issue with the identical root cause landed in cluster 1 instead of here. |
| 8. GIF `SourceUri` reload leak | **unsupported** | Single instance, narrow library scope. |

Only candidate 1 stands on its own as `useful` without needing
re-clustering first; candidates 3 and 7 could plausibly join it (or each
other) on a second pass. None of the 8 is `duplicate` outright or
`unsupported` for lack of *technical* evidence — every card traces to a
real GitHub URL with real technical substance; where cards are weak, it's
on corroboration count and commercial signal, not fabrication.

## 9. Acceptance minimums — checked, not assumed

| Minimum | Required | Actual | Met? |
|---|---:|---:|:-:|
| Fetched issues | ≥50 | 247 | ✓ |
| After dedupe | ≥30 | 96 as-run / 97 with the dedup fix (§2) — either way | ✓ |
| Repositories | ≥15 | 77 | ✓ |
| Unique issue openers | ≥20 | 90 reported / 86 corrected (§3) | ✓ |
| Synthetic records | 0 | 0 | ✓ |
| Accepted records with URL+author+timestamp | 100% | 100% (0 schema_validation_failed) | ✓ |

Every minimum is not just met but exceeded by a wide margin. Thresholds
were not adjusted to reach these numbers — the acquisition ran once, cold,
against the query pack as committed.

## 10. Final verdict

**This trial does not reduce to one BLOCKED/PASS label.** An earlier
version of this section collapsed several distinct facts into a single
causal story — that the critic caused the run-level BLOCKED result, and
that a working critic was the only thing standing between this dataset and
a clean PASS. Both claims were wrong, verified against the actual code
(`verification.py`, `scoring/judge.py`) rather than assumed, and are
corrected below. Five facts, kept deliberately separate:

### Fact 1 — Acquisition verdict: PASS

The Phase 2B acquisition bridge worked exactly as designed: a real
`GITHUB_TOKEN`, real Search API calls on a GitHub-hosted runner, a real
orphan data branch (never an Actions artifact), deterministic exact-identity
dedup, and — after this correction — deterministic round-robin selection
(§2, §7) and a stronger bot filter (§3). Every acceptance minimum in §9 was
met or exceeded on the first cold run. This fact does not depend on
anything below it.

Note: the specific 100 records analyzed in §3-§8 below were selected by
this run, *before* the round-robin selection fix — i.e. among the 128
eligible-but-over-cap records, selection followed the old alphabetic-
sort-then-cap order, which is exactly the sampling bias §2's correction
removed. This trial's own committed dataset is not re-selected or re-run to
correct this (that would mean re-spending the 100-call analyst budget); a
separate, analyst-free acquisition run verifies the selection fix directly
(see the correction note).

### Fact 2 — Analyst discovery result: USEFUL BUT BIASED

`demand-radar run --analyst claude` produced 8 real, evidence-linked
opportunity candidates from real GitHub issues (§4, §8) — not a synthetic
or cherry-picked set. Candidate 1 (memory-profiler ownership mapping) is a
genuinely strong, differentiated signal: 11 independent authors, confidence
0.74, a framing ("map retention to ownership," not "detect leaks") that the
evidence itself shows existing commercial tools don't cover. But the source
is biased — mostly 2014-2017 issues (§7), GitHub-`is:issue`-only, and
overwhelmingly technical rather than commercial (§6: commercial_intent
0.0-0.1 across all 8 candidates). "Useful" here describes a real technical/
problem signal, not a commercially validated opportunity.

### Fact 3 — Production run verdict: BLOCKED / immediate cause: analyst `BLOCKED_TIMEOUT`

`demand-radar run --product own-audit --analyst claude --critic fake`
returned the real production `overall_verdict()` result: **BLOCKED**, exit
code 2. Per `verification.py`, `overall_verdict()` checks `BLOCKED` first:
it returns `BLOCKED` if *either* the analyst's or the critic's derived
status is in `{BLOCKED_AUTH, BLOCKED_USAGE, BLOCKED_TIMEOUT,
BLOCKED_NOT_INSTALLED, NOT_RUN}` — before it ever considers FAIL or PASS.
In this run, the analyst's 96 live `classify` calls included one real
`BLOCKED_TIMEOUT` (alongside 3 `FAIL_INVALID_OUTPUT` and 92 successes). That
single timeout alone is sufficient to make the analyst's derived status
`BLOCKED_TIMEOUT`, which alone is sufficient to make `overall_verdict()`
return `BLOCKED` — independent of anything the critic did. **This is the
correction to the earlier version of this report**, which incorrectly
attributed the BLOCKED result to the critic.

### Fact 4 — Critic status: `FAIL_INVALID_OUTPUT` / cause: `FakeRunner`'s `{}` fails schema

Separately from Fact 3: all 8 `critic_review` calls (`--critic fake`)
returned `FakeRunner`'s hardcoded default output, `{}`, which cannot pass
`critic-verdict.schema.json` for any real, non-empty-required card, by
design (see `agents/fake.py`) — not specific to this run's data; any real
evidence set hits the same wall with `--critic fake`. Had the analyst *not*
also hit `BLOCKED_TIMEOUT`, this critic failure mode alone would have
produced a run-level **FAIL**, not **BLOCKED** — `FAIL` and `BLOCKED` are
different, mutually exclusive branches of `overall_verdict()`. This run's
BLOCKED result is fully explained by Fact 3 alone; the critic's
`FAIL_INVALID_OUTPUT` is real, worth recording, and was never the
proximate cause of BLOCKED.

### Fact 5 — Validation status: not `experiment_ready`, for two independent reasons

Set Fact 3 and Fact 4 aside and imagine a hypothetical rerun with no
analyst timeout and a real, working critic. This dataset would *still* not
clear `judge_opportunity()`'s gates to `experiment_ready`, because
`products/own-audit.yaml` sets `thresholds.minimum_source_families: 2`, and
every accepted record in this dataset has `source_family="github"` — one
family. `judge.py`'s source-diversity gate is its own step in the gate
sequence, independent of the critic-presence check; an all-GitHub dataset
cannot pass it regardless of critic behavior. **Both of the following
claims from an earlier version of this report are withdrawn**: that critic
absence was "the only reason this run doesn't carry a clean PASS," and that
"a real independent critic would likely support a USEFUL verdict" for this
dataset — both were unproven when written, and the source-families gate
shows they were also incorrect. The accurate statement: **the ownership-
path candidate (cluster 1) is a promising technical/problem signal, but it
is not commercially validated and not `experiment_ready`** — for two
independent reasons (no live critic; a single source family), neither of
which a GitHub-only correction can resolve.

### Summary

This is a **different kind of incomplete** than Phase 2A's
`INSUFFICIENT_EXTERNAL_RECORDS`: there, the data itself didn't exist in the
needed shape. Here, real acquisition, real ingest, and real analyst
reasoning all worked well, and cluster 1 is a genuinely strong candidate by
the evidence alone — but the path from here to `experiment_ready` needs
both a live critic *and* a second, independent source family (Fact 5).
Neither is in scope for this correction, per the explicit instruction not
to expand the query pack or add a source family here; both are named as the
logical next scope (not Codex).

**Final verdict field (original Phase 2B enum):
`BLOCKED_BY_CRITIC_REQUIREMENT`.** Read together with Fact 5: this label
names *one of two* independent, unmet requirements for validation — not the
only one. Neither `NEEDS_QUERY_TUNING` nor `SOURCE_TOO_NOISY` nor
`INSUFFICIENT_RECORDS` fits (the source is neither noisy nor under-volume,
per §9's own numbers); `USEFUL` is withheld because validation genuinely has
not completed, for reasons this report's scope cannot resolve alone.

## Commands, run ID, artifacts

```bash
git fetch origin research-data/own-audit-live-20260715T160205Z-29430642497
uv run demand-radar init --db runs/github-live-trial/store.db
uv run demand-radar ingest --product own-audit \
  --input <fetched>/data/evidence.jsonl --db runs/github-live-trial/store.db
PATH=/path/to/007/target/release:$PATH uv run demand-radar run \
  --product own-audit --analyst claude --critic fake \
  --db runs/github-live-trial/store.db \
  --runs-dir runs/github-live-trial/runs --run-id github-live-trial-001
```

- Acquisition workflow run: `29430642497` (completed, success, ~54s).
- Data branch: `research-data/own-audit-live-20260715T160205Z-29430642497`
  (orphan branch, 3 files, not merged into any development branch).
- Ingest: `accepted=100 inserted=100 already_present=0 errors=0`.
- Pipeline run: `run_id=github-live-trial-001 verdict=BLOCKED`, exit code 2.
- Report: `runs/github-live-trial/runs/own-audit/github-live-trial-001/outputs/report.md`
  (not committed — gitignored `runs/`; this document is the committed record).
- This document's commit: see the commit that introduces it, this repo,
  branch `claude/demand-radar-mvp-9a4h1y`.
