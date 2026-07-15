# Historical external evidence replay (Phase 2A)

Status: complete (correction pass applied) · Verdict: **INSUFFICIENT_EXTERNAL_RECORDS** (secondary observation: **EXTRACTION_NEEDS_TUNING did not trigger** — see §7/§11)

This is **not a live trial**. It replays evidence *already committed* in the
sibling `OwnAudit`/`Own.NET` repositories through Demand Radar, to test
pipeline mechanics and extraction quality — not to discover a new
opportunity, and not to validate a product idea. See §10 for why this
corpus structurally cannot do the latter.

**Correction note:** an arbitration pass over the first version of this
report found three semantic errors and one unverified claim — a repo
miscount (24 vs. the correct 23, caused by an aggregate rerun entry being
counted as its own repository), an inflated "43 technical findings" framing
that hid the mix of confirmed/false-positive/unresolved/metadata-only
entries actually in the census, an overclaim about the Claude extraction
pass behaving "unprompted," and an ingest-path claim that had only actually
exercised the empty-input case. All four are fixed below; the final verdict
(`INSUFFICIENT_EXTERNAL_RECORDS`) is unchanged — the corrections affect
supporting arithmetic and wording, not the conclusion.

## 0. What was asked, in one sentence

Convert whatever real external GitHub evidence already exists in
`OwnAudit/leakmine` and `Own.NET/corpus` into Demand Radar's evidence
contract, run what the pipeline can honestly run on it, and report exact
counts — padding nothing if the corpus falls short.

## 1. Corpus paths and commit SHAs

Read at:

| Repo | HEAD at read time |
|---|---|
| `PhysShell/OwnAudit` | `98b512d327b1358f5fe884c9537e6508d4dbf020` |
| `PhysShell/Own.NET` | `2baa52865a34f67c75fe1d9bb91e40af10eefec6` |
| `PhysShell/demand-radar` (before this correction commit) | `5958414` |

Paths read:
- `OwnAudit/leakmine/*.py` (the mining pipeline's code)
- `OwnAudit/docs/leakfix-mine.md`, `docs/leakfix-langs-appendix.md`
- `OwnAudit/.github/workflows/leakmine-mine.yml`
- `Own.NET/corpus/{real-world,wpf,di}/*/notes.md` (43 case directories)
- `Own.NET/docs/notes/oracle-sweep-2026-07-10.md`
- `Own.NET/docs/notes/oracle-sweep-rerun-2026-07-11.md`
- `Own.NET/docs/notes/real-world-mining.md`
- `Own.NET/docs/proposals/P-007-arraypool-span.md`, `P-012-bug-corpus-mining.md`
- GitHub Actions run history for `PhysShell/Own.NET`: `oracle.yml` (53 runs),
  `mine.yml` (1 run), `mine-on-push.yml` (13 runs), `mine-run.yml` (10 runs)
- GitHub Actions run history for `PhysShell/OwnAudit`: `leakmine-mine.yml` (0 runs)

Exporter: [`scripts/export_existing_corpus.py`](../../scripts/export_existing_corpus.py).
Extraction experiment: [`scripts/experiment_technical_prevalence_extraction.py`](../../scripts/experiment_technical_prevalence_extraction.py).

## 2. Tables / JSON actually used

There is no `dataset.json` / `corpus.db` / `summary.md` anywhere in either
repo. `leakmine/` is a mining **pipeline** (`collect.py`, `mine.py`,
`confirm.py`, `sweep.py`, `szz.py`, `bigquery.py`, SQLite schema in
`schema.py`), not a dataset — its output directory (`leakmine-out/`) is
gitignored in `OwnAudit/.gitignore` and does not exist on disk, and the one
CI workflow that runs it (`leakmine-mine.yml`) has **0 completed runs** (GitHub
Actions API, checked directly, not inferred). So nothing from `leakmine`
itself fed this replay — it's recorded as a confirmed zero-record source,
not silently skipped.

What was actually used instead, all from `Own.NET`:
1. Programmatic scan of `corpus/{real-world,wpf,di}/*/notes.md` for a
   `github.com/OWNER/REPO/(pull|issues)/NUMBER` reference (mechanical regex,
   not hand-picked). Category: `cited_origin_unverified`.
2. Hand-transcribed, source-cited tables from 3 markdown write-ups — every
   entry in the exporter's `TIER_A` list carries the exact source doc it came
   from; these are prose+table docs with no stable machine-readable form, so
   this script does not attempt to regex-parse them, and the transcription
   is faithful to the tables as read (spot-checkable against the cited doc).
   Category: `confirmed_tp` / `false_positive` / `review_pending` /
   `aggregate_validation`, derived per entry from its own verdict text — the
   3 docs report a mix of all four, not a uniform set of confirmed findings.
3. GitHub Actions run **titles/metadata only** (no job logs, no artifact
   content) for a breadth census of repos mined/scanned outside the 3
   written-up docs (`TIER_B` in the exporter). Category: `ci_run_metadata` —
   proves a workflow ran against that repo, nothing about what it found.
4. One real CI artifact was *found* but not *read*: `mine.yml` run
   `27634247136` produced `mine-report` (2354 bytes, sha256 `7f8308…36e77b`,
   not expired). Its download resolves to
   `productionresultssa19.blob.core.windows.net`, outside this session's
   network egress allowlist — confirmed by a real attempt (`curl`, 403 at the
   proxy), not assumed, and not retried per the standing network-policy
   constraint. Recorded rather than dropped.

## 3. Excluded records and why

**40 of 43** `corpus/{real-world,wpf,di}` case directories were excluded from
the corpus observation set entirely (full list in
`data/live/historical-replay-corpus-observations.json`'s `excluded` array).
Reason, uniformly: `notes.md` describes a pattern "representative of real
code" (per `corpus/real-world/README.md`: *"a hand reduction... not a
verbatim diff of one PR"*) with **no traceable `github.com` issue/PR URL** —
the Phase 2A provenance rule requires one, so a plausible-sounding but
unverifiable pattern does not qualify, no matter how realistic it reads.
Two ScreenToGif/ShareX cases cite a real file path
(`ScreenToGif/Windows/Other/VideoSource.xaml.cs:50-83`) without a commit SHA
or PR/issue number — these were routed to `TIER_A` instead (their commit **is**
pinned and independently documented in `real-world-mining.md`/the oracle-sweep
notes), not excluded; the 40 excluded are cases with no traceable origin at
all beyond "this is what the bug class looks like."

3 corpus case directories DID cite a real external URL; 2 of them
(`arraypool-double-return`, `arraypool-aliased-receiver`) cite the **identical**
`dotnet/runtime#33767` — canonical identity is repo+issue number, so these
collapse to **one** external record, not two (the exporter logs this collapse
explicitly rather than silently deduping).

## 4. Raw / imported / deduplicated counts

Exact output of `uv run python3 scripts/export_existing_corpus.py`:

```
problem_evidence records (RawEvidenceRecord, ready to ingest): 0
corpus census observations total: 43
  tier A (hand-triaged from written-up sweeps): 28
  tier B (named in CI history, not re-verified here): 13
  tier C (cited-origin, not re-fetched): 2
by category (this is the number to cite, not the tier total):
  confirmed_tp: 8
  false_positive: 15
  review_pending: 4
  aggregate_validation: 1
  ci_run_metadata: 13
  cited_origin_unverified: 2
distinct real external repos referenced: 23
excluded candidates (no verifiable external provenance): 40
```

**Why "43" is a census total, not a findings count** (correction from the
first version of this report): the 43 corpus observations are NOT 43
technical findings — 15 are `false_positive` (Own.NET's own reviewer
determined these were NOT real bugs), 4 are `review_pending` (flagged but
never resolved either way), 1 is `aggregate_validation` (a rerun
re-confirming earlier rows, not a new observation), 13 are `ci_run_metadata`
(prove only that a workflow executed, no finding-count information at all),
and 2 are `cited_origin_unverified` (a real URL never re-fetched). Only
**8 of 43** are `confirmed_tp` — a human independently verified these against
the real source. Any sentence that says "43 technical findings" or "43
technical_prevalence findings" is imprecise in exactly the way this
correction exists to fix; the accurate unit is "corpus census observations,"
6 of which have a `confirmed_tp`/`false_positive`/etc. meaning, only one of
which (`confirmed_tp`) supports a technical claim on its own.

Two ingestion-path checks, kept distinct on purpose:
- **Empty-output path, on the real corpus:**
  `demand-radar ingest --product own-audit --input data/live/historical-replay-evidence.jsonl`
  against the real (empty) exporter output: `accepted=0 inserted=0
  already_present=0 errors=0` — proves ingest handles a well-formed empty
  JSONL cleanly. This does **not** exercise `RawEvidenceRecord` validation or
  `demand_radar.ingest.jsonl.parse_jsonl_records` against a non-empty line,
  because the real census never produced one.
- **Non-empty round-trip, on a synthetic fixture:**
  `tests/unit/test_export_existing_corpus.py` constructs one clearly-labeled
  synthetic record (never counted above, never written to `data/live/`),
  round-trips it through `build_problem_evidence_jsonl()` →
  `RawEvidenceRecord` → a JSONL line → `parse_jsonl_records()`, and asserts it
  parses back with no errors. This is what actually proves the non-empty
  `dict → RawEvidenceRecord → JSONL → ingest` path is correct; the first
  version of this report claimed this was "verified" from the empty-corpus
  run alone, which was not true.

**Against the stated minimums:** 0 of 50 problem_evidence records; 23 of 15
repos (only exceeds the repo minimum by counting corpus-observation repos,
which do not carry problem_evidence); 0 of 20 unique authors (see §5); 0 of
30 deduplicated problem_evidence records; the 3 preliminary clusters in §6
are the only thing resembling "problem clusters," all explicitly
non-commercial (§8). The volume minimums were written for problem_evidence;
this replay found corpus census observations instead, which is a different
kind of thing, not a smaller amount of the same thing.

## 5. Repositories and unique authors

**23 distinct real repositories** referenced (full list in the manifest;
highlights): `ShareX/ShareX`, `MahApps/MahApps.Metro`,
`MaterialDesignInXAML/MaterialDesignInXamlToolkit`, `icsharpcode/AvalonEdit`,
`ClosedXML/ClosedXML`, `DapperLib/Dapper`, `JoshClose/CsvHelper`,
`NickeManarin/ScreenToGif` (8 tier A, verified); `NethermindEth/nethermind`,
`dotnet/runtime` (2 tier C, cited-origin); `StackExchange/StackExchange.Redis`,
`protobuf-net/protobuf-net`, `NLog/NLog`, `serilog/serilog`,
`JamesNK/Newtonsoft.Json`, `RestSharp/RestSharp`, `App-vNext/Polly`,
`npgsql/npgsql`, `SixLabors/ImageSharp`, `neuecc/MessagePack-CSharp`,
`mgravell/Pipelines.Sockets.Unofficial`, `WalletWasabi/WalletWasabi`,
`Flow-Launcher/Flow.Launcher` (13 tier B, named in CI history only).
8 + 2 + 13 = 23.

**Correction: not 24.** The first version of this report computed
`distinct_repos = {item.repo for item in census.technical_prevalence}`
directly, which counted the oracle-rerun's own aggregate entry —
whose `repo` field reads `"MahApps/MahApps.Metro, MaterialDesignInXamlToolkit,
AvalonEdit, ShareX"` as one comma-joined descriptive string — as a 24th,
distinct "repository." It re-confirms 4 repos already counted individually
above; it is not a 5th UI-toolkit repo or a 24th anything. The corrected
exporter derives a `category` field per observation and excludes any
`aggregate_validation`-category entry from the repo census while still
keeping it in `corpus_observations` (it's a real, useful re-confirmation —
just not a new repository).

**Unique authors: 0, honestly.** This is not a rounding-down of a small
number — it's a structural fact about what this corpus *is*. Every tier-A/B
item is a **static-analysis finding Own.NET produced against someone else's
code**; no person authored a complaint, an issue, or a PR that this replay
imported. The 2 tier-C items cite a real PR/issue URL but this replay never
fetched the live page (no network path to it; see §2), so even there the
actual GitHub username is unknown to this replay, not merely unrecorded —
inventing a placeholder would misrepresent confidence this replay doesn't
have. `author.stable_hash` in Demand Radar's `EvidenceItem` schema requires a
real identity to hash; since none of this data has one, none of it was
forced through that schema (see §2, §4 — 0 problem_evidence records is the
direct consequence of this, not a separate gap).

## 6. Problem clusters

The production `demand-radar run` graph was not invoked: it clusters
`EvidenceItem`s, and there are 0. Per the Phase 2A instruction for exactly
this situation — no problem_evidence, no independent critic available
(Codex stays forbidden for untrusted content per the standing freeze), don't
invent a new execution mode — a **separate** one-off script
(`scripts/experiment_technical_prevalence_extraction.py`) made one real
Claude call (via `O7InvokeRunner`, `engine=claude` only) over the corpus
observations manifest. The prompt explicitly defined all six categories from
§4, instructed the model to build clusters **only** from `confirmed_tp`
observations (with `review_pending` allowed only as explicitly-labeled,
non-load-bearing color), and forbade any `commercial_status` beyond the two
schema-permitted values. Real output (full JSON in
`runs/historical-replay/technical-prevalence-extraction/o7-out/result.json`):

**Cluster 1 — "IDisposable-owning fields/components with no Dispose
implementation at all"** — `ShareX/ShareX`, `icsharpcode/AvalonEdit`. Both
member findings are `confirmed_tp`.

**Cluster 2 — "Long-lived event/subscription handlers never detached (lapsed
listener leaks)"** — `NickeManarin/ScreenToGif` (both `confirmed_tp`; the
`SystemEvents` finding is also cross-tool corroborated by CodeQL). Similar
`review_pending` findings in ShareX, AvalonEdit, and MaterialDesignInXamlToolkit
are named explicitly as unconfirmed color, not as part of the cluster's
evidence basis.

**Cluster 3 — "Undisposed ADO.NET/IO resources left open in test and
benchmark code"** — `DapperLib/Dapper`, `JoshClose/CsvHelper`. Both
`confirmed_tp`.

Every cluster's `commercial_status` is `would_require_independent_problem_evidence`.

**Correction: not "unprompted."** The first version of this report said the
model excluded weaker-tier evidence "on its own initiative — nobody had to
tell it to." That is not accurate: the prompt explicitly named all six
categories, said to build clusters only from `confirmed_tp`, and the output
schema's `commercial_status` enum has exactly two non-commercial values with
no stronger option to select even if the model wanted to. The correct
framing: **under an explicit anti-overclaim prompt and a restrictive schema,
Claude produced three technically coherent preliminary clusters and stayed
within the permitted non-commercial semantics.** That is a real, positive
result about constrained extraction — the schema and prompt did real work
here, and the model followed them correctly instead of finding a way around
them — but it is a different claim from unprompted epistemic restraint, and
the report should not have implied the model volunteered a discipline it was
in fact given as an explicit constraint.

## 7. Strongest technical evidence

The ScreenToGif `SystemEvents.DisplaySettingsChanged` finding (cluster 2) is
the single strongest item in the corpus: it is (a) `confirmed_tp`, (b)
reduced to a locked regression fixture
(`corpus/real-world/screentogif-systemevents-leak/`), and (c) independently
corroborated by a second, unrelated tool (CodeQL) in the same cross-tool
oracle run — the only finding in this entire corpus with that level of
triangulation. Cluster 3's Dapper finding is next: a leak in a *benchmark*
project of one of the most widely-used .NET micro-ORMs, `confirmed_tp`.

Extraction-quality note: the model's self-check (§11) shows it correctly
distinguished `confirmed_tp` from `review_pending`, `false_positive`,
`ci_run_metadata`, `aggregate_validation`, and `cited_origin_unverified` when
deciding what to cluster on — this is the positive result of this replay,
achieved under an explicit anti-overclaim prompt and schema (§6), not
spontaneously. **EXTRACTION_NEEDS_TUNING does not apply**: nothing here
indicates the extraction step needs tuning — given the right categorical
structure to reason over, it used it correctly, and no cluster's
`commercial_status` overreached.

## 8. Commercial signals actually present

**None.** Checked explicitly, not assumed: zero entries in the corpus are a
person filing an issue asking for a fix, a maintainer commenting on cost or
priority, a user requesting a feature, or any expression of willingness to
pay. Every one of the 43 corpus observations is Own.NET's own analyzer
output, a review note, or a CI run record — never a third party's words.
All three preliminary clusters in §6 are correctly labeled
`would_require_independent_problem_evidence` — the strongest label the
schema allows short of a real complaint, and none claims more.

## 9. Source bias

- **100% supply-side selection.** Every repo in this corpus was chosen by
  Own.NET's own author to exercise/calibrate Own.NET's own detectors — not
  sampled by any signal of user demand. `real-world-mining.md`,
  `oracle-sweep-2026-07-10.md`, and CI run titles ("clean-code control",
  "verify the 3 merged FP-fixes dropped", "confirm the modeless-Form FP
  drop") show this outright: some repos were picked specifically to find
  *few* findings (precision validation), which is the opposite of a
  demand-maximizing sample.
- **Domain-skewed by design, not accident.** The WPF/WinForms desktop-app
  concentration (ShareX, ScreenToGif, MahApps.Metro,
  MaterialDesignInXamlToolkit, AvalonEdit, Flow.Launcher, WalletWasabi)
  matches OwnAudit's actual target domain (`products/own-audit.yaml`) — that
  part of the bias is intentional and appropriate. The backend/infra
  sampling (Dapper, CsvHelper, Npgsql, StackExchange.Redis, protobuf-net,
  NLog, Serilog, Newtonsoft.Json, RestSharp, Polly, ImageSharp,
  MessagePack-CSharp, Pipelines.Sockets.Unofficial) is broader ecosystem
  validation, not WPF-domain signal.
- **Popularity-skewed.** Every named repo is a well-known, easily cloneable
  OSS project — no signal at all about the long-tail/internal-enterprise
  legacy WPF codebases `own-audit.yaml`'s personas actually describe.

## 10. Corpus contamination

Explicit and total: this corpus **is** Own.NET/OwnAudit's own development
history. The oracle/mining CI runs exist specifically to calibrate Own.NET's
rules against real code — meaning any "problem cluster" this replay derives
is, by construction, already known to the product owner, who built the
detector for that exact pattern before this replay ever ran. This replay
therefore cannot and does not claim to have discovered anything new for the
product owner (per the explicit instruction not to). What it *can* and does
claim: Demand Radar's extraction step, pointed at real (if self-referential)
technical evidence and given an explicit categorical structure to reason
with (§6), reasoned about it correctly instead of inflating bug-prevalence
into invented demand.

## 11. Manual review of each candidate

| Candidate | Classification | Why |
|---|---|---|
| Cluster 1 — undisposed IDisposable fields/components | **known_product_problem** | This is precisely OWN001/CA2000-class coverage Own.NET was built to catch; not new to the product owner (§10). Both member findings are `confirmed_tp`, but the general shape is also covered by existing substitutes (Roslyn analyzers, ReSharper, SonarQube, ships-with-.NET CA2000) — not a differentiated angle. |
| Cluster 2 — undetached UI event subscriptions | **known_product_problem**, secondarily **potentially_testable** | Also core to Own.NET's own positioning (its docs explicitly note *no* mainstream oracle — CodeQL/Infer# — has an equivalent query), so not new to the product owner either. The "potentially_testable" secondary tag reflects that the tooling-differentiation angle (not the bug's existence) is the one piece here not already fully explored — worth a human checking whether anyone outside this project has ever asked for it. |
| Cluster 3 — undisposed ADO.NET/IO resources in tests/benchmarks | **known_product_problem** | Same OWN001-class coverage as cluster 1, on different repos (Dapper, CsvHelper); the analyzer's correct handling of `using`-scoped locals in the same sweep is evidence of precision, not of a new opportunity. |
| All three clusters | **technical_prevalence_only** for commercial purposes | None is `useful` (net-new opportunity), `unsupported` (all three rest on `confirmed_tp` evidence for the technical claim specifically), or `duplicate` — they are real, correctly-scoped, non-commercial technical findings, exactly the bucket §8/the Phase 2A spec defines for this. |

No candidate reached, or was framed as reaching, `useful` in the
demand-discovery sense — none is `unsupported`, since all three are backed by
real `confirmed_tp` evidence for the technical claim specifically (not the
commercial one).

## 12. Final verdict

**INSUFFICIENT_EXTERNAL_RECORDS.** (Unchanged by the correction pass.)

The committed corpus contains 0 problem_evidence records and cannot supply
them — `leakmine` was never run, and every real external citation the
Own.NET corpus does contain is a corpus census observation by the Phase 2A's
own classification rule (§4), not a substitute for problem evidence at any
volume. This is not `CORPUS_TOO_BIASED` (bias is real, §9, but volume is the
binding constraint, not bias), not `EXTRACTION_NEEDS_TUNING` (the one real
extraction pass available to test — §6/§7/§11 — showed correct behavior
under an explicit anti-overclaim prompt, not a defect to tune), and not
`BLOCKED_BY_CRITIC_REQUIREMENT` (the critic gate was never reached; the
blocker is upstream, at data availability).

What this replay *does* establish, positively: (1) the empty-output
export→ingest path is correct on the real corpus, and the non-empty
`dict → RawEvidenceRecord → JSONL → ingest` path is correct on a synthetic
fixture (§4) — the two are now verified separately rather than one being
claimed on the strength of the other; (2) under an explicit anti-overclaim
prompt and a restrictive schema, a real Claude extraction pass over real
technical evidence correctly separated "this bug is real and recurring"
(8 `confirmed_tp` observations) from "someone wants a product for this"
(zero observations of any kind support this); (3) the corpus census itself
(23 real repos, 43 census observations of which 8 are independently
confirmed true positives) is a legitimate, reusable technical-prevalence
reference for Own.NET/OwnAudit's own roadmap prioritization — just not a
Demand Radar input.

## Commands, run ID, verification

```bash
uv run python3 scripts/export_existing_corpus.py
uv run demand-radar init --db runs/historical-replay/store.db
uv run demand-radar ingest --product own-audit \
  --input data/live/historical-replay-evidence.jsonl \
  --db runs/historical-replay/store.db
PATH=/path/to/007/target/release:$PATH \
  uv run python3 scripts/experiment_technical_prevalence_extraction.py
uv run pytest tests/unit/test_export_existing_corpus.py -v
```

- Exporter outputs: `data/live/historical-replay-evidence.jsonl` (0 lines),
  `data/live/historical-replay-corpus-observations.json` (43 observations,
  category breakdown in §4) — both gitignored (`data/`), not committed; this
  document and the two scripts (plus the new unit test) are the committed
  record.
- Extraction run artifacts: `runs/historical-replay/technical-prevalence-extraction/`
  (`meta.json`, `prompt.txt`, `o7-out/result.json`) — gitignored (`runs/`).
- Empty-corpus ingest verification: `accepted=0 inserted=0 already_present=0 errors=0`.
- Synthetic non-empty round-trip: `tests/unit/test_export_existing_corpus.py`,
  2 tests, both passing (`uv run pytest tests/ -q` — part of `scripts/check.sh`).
- Live Claude call (post-correction manifest): `status=PASS schema_valid=True error_kind=None`.
- This document's commit: see the commit that introduces this correction pass,
  this repo, branch `claude/demand-radar-mvp-9a4h1y`.
