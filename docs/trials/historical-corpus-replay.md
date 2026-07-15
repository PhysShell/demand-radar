# Historical external evidence replay (Phase 2A)

Status: complete · Verdict: **INSUFFICIENT_EXTERNAL_RECORDS** (secondary observation: **EXTRACTION_NEEDS_TUNING did not trigger** — see §7/§11)

This is **not a live trial**. It replays evidence *already committed* in the
sibling `OwnAudit`/`Own.NET` repositories through Demand Radar, to test
pipeline mechanics and extraction quality — not to discover a new
opportunity, and not to validate a product idea. See §10 for why this
corpus structurally cannot do the latter.

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
| `PhysShell/demand-radar` (this repo, before this trial's commit) | `5ee14dc8554b1510319a89e246a3a340d0f4de33` |

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
   not hand-picked).
2. Hand-transcribed, source-cited tables from 3 markdown write-ups — every
   entry in the exporter's `TIER_A` list carries the exact source doc it came
   from; these are prose+table docs with no stable machine-readable form, so
   this script does not attempt to regex-parse them, and the transcription
   is faithful to the tables as read (spot-checkable against the cited doc).
3. GitHub Actions run **titles/metadata only** (no job logs, no artifact
   content) for a breadth census of repos mined/scanned outside the 3
   written-up docs (`TIER_B` in the exporter).
4. One real CI artifact was *found* but not *read*: `mine.yml` run
   `27634247136` produced `mine-report` (2354 bytes, sha256 `7f8308…36e77b`,
   not expired). Its download resolves to
   `productionresultssa19.blob.core.windows.net`, outside this session's
   network egress allowlist — confirmed by a real attempt (`curl`, 403 at the
   proxy), not assumed, and not retried per the standing network-policy
   constraint. Recorded rather than dropped.

## 3. Excluded records and why

**40 of 43** `corpus/{real-world,wpf,di}` case directories were excluded from
the technical_prevalence set entirely (full list in
`data/live/historical-replay-technical-prevalence.json`'s `excluded` array).
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
technical_prevalence items total: 43
  tier A (fully verified, hand-triaged): 28
  tier B (named in CI history, not re-verified here): 13
  tier C (cited in a corpus regression case): 2
distinct real external repos referenced: 24
excluded candidates (no verifiable external provenance): 40
```

`demand-radar ingest --product own-audit --input data/live/historical-replay-evidence.jsonl`
against the resulting (empty) file: `accepted=0 inserted=0 already_present=0
errors=0` — proves the mechanical ingest path is correct on real (if empty)
exporter output; there is nothing further to dedupe in the production sense
because there are 0 EvidenceItems.

**Against the stated minimums:** 0 of 50 problem_evidence records; 24 of 15
repos (only exceeds the repo minimum by counting technical_prevalence repos,
which do not carry problem_evidence); 0 of 20 unique authors (see §5); 0 of
30 deduplicated problem_evidence records; the 2 preliminary clusters in §6
are the only thing resembling "problem clusters," both explicitly
non-commercial (§8). The volume minimums were written for problem_evidence;
this replay found technical_prevalence instead, which is a different kind of
thing, not a smaller amount of the same thing.

## 5. Repositories and unique authors

**24 distinct real repositories** referenced (full list in the manifest;
highlights): `ShareX/ShareX`, `MahApps/MahApps.Metro`,
`MaterialDesignInXAML/MaterialDesignInXamlToolkit`, `icsharpcode/AvalonEdit`,
`ClosedXML/ClosedXML`, `DapperLib/Dapper`, `JoshClose/CsvHelper`,
`NickeManarin/ScreenToGif` (tier A, verified); `NethermindEth/nethermind`,
`dotnet/runtime` (tier C, cited-origin); `StackExchange/StackExchange.Redis`,
`protobuf-net/protobuf-net`, `NLog/NLog`, `serilog/serilog`,
`JamesNK/Newtonsoft.Json`, `RestSharp/RestSharp`, `App-vNext/Polly`,
`npgsql/npgsql`, `SixLabors/ImageSharp`, `neuecc/MessagePack-CSharp`,
`mgravell/Pipelines.Sockets.Unofficial`, `WalletWasabi/WalletWasabi`,
`Flow-Launcher/Flow.Launcher` (tier B, named in CI history only).

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
Claude call (via `O7InvokeRunner`, `engine=claude` only) over the
technical_prevalence manifest, explicitly instructed that analyzer findings
are not user complaints and that "would require independent problem
evidence" is the strongest honest label available. Real output (full JSON in
`runs/historical-replay/technical-prevalence-extraction/o7-out/result.json`):

**Cluster 1 — "IDisposable fields/resources with no Dispose implementation at
all"** — `ShareX/ShareX`, `icsharpcode/AvalonEdit`, `DapperLib/Dapper`.
Recurs as a confirmed (human-reviewed, not just raw-flagged) true positive
across three independently maintained projects.

**Cluster 2 — "Desktop UI event subscriptions never detached"** —
`ShareX/ShareX`, `NickeManarin/ScreenToGif`. Recurs across two independent
maintainers; the ScreenToGif instance is cross-tool corroborated (CodeQL
independently flags the same file, though not the same rule).

Both clusters were built **only** from tier-A (human-verified TP) entries;
the model explicitly excluded tier-B ("not independently re-verified") and
"review"/"review, bulk" verdicts from driving cluster formation on its own
initiative — nobody had to tell it to.

## 7. Strongest technical evidence

Cluster 2's ScreenToGif `SystemEvents.DisplaySettingsChanged` finding is the
single strongest item in the corpus: it is (a) tier-A, human-verified TP,
(b) reduced to a locked regression fixture
(`corpus/real-world/screentogif-systemevents-leak/`), and (c) independently
corroborated by a second, unrelated tool (CodeQL) in the same cross-tool
oracle run — the only finding in this entire corpus with that level of
triangulation. Cluster 1's Dapper finding is the next-strongest: a leak in a
*benchmark* project of one of the most widely-used .NET micro-ORMs, tier-A
verified.

Extraction-quality note: the model's self-check (§11) shows it correctly
distinguished "verified TP" from "review, not confirmed" from "unverified
CI-history mention" when deciding what to cluster — this is the positive
result of this replay. **EXTRACTION_NEEDS_TUNING does not apply**: nothing
here indicates the extraction step needs tuning: it filtered by evidence
tier without being told to, and neither cluster's `commercial_status`
overreached.

## 8. Commercial signals actually present

**None.** Checked explicitly, not assumed: zero entries in the corpus are a
person filing an issue asking for a fix, a maintainer commenting on cost or
priority, a user requesting a feature, or any expression of willingness to
pay. Every technical_prevalence item is Own.NET's own analyzer output plus
PhysShell's own review notes. Both preliminary clusters in §6 are correctly
labeled `would_require_independent_problem_evidence` — the strongest label
the schema allows short of a real complaint, and neither claims more.

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
technical evidence, reasoned about it honestly (§6, §11) instead of
inflating bug-prevalence into invented demand.

## 11. Manual review of each candidate

| Candidate | Classification | Why |
|---|---|---|
| Cluster 1 — undisposed IDisposable fields | **known_product_problem** | This is precisely OWN001/CA2000-class coverage Own.NET was built to catch; not new to the product owner (§10). Technically real and tier-A confirmed, but the general shape is also covered by existing substitutes (Roslyn analyzers, ReSharper, SonarQube, ships-with-.NET CA2000) — not a differentiated angle. |
| Cluster 2 — undetached UI event subscriptions | **known_product_problem**, secondarily **potentially_testable** | Also core to Own.NET's own positioning (its docs explicitly note *no* mainstream oracle — CodeQL/Infer# — has an equivalent query), so not new to the product owner either. The "potentially_testable" secondary tag reflects that the tooling-differentiation angle (not the bug's existence) is the one piece here not already fully explored — worth a human checking whether anyone outside this project has ever asked for it. |
| Both clusters | **technical_prevalence_only** for commercial purposes | Neither is `useful` (net-new opportunity), `unsupported` (both are tier-A evidence-backed), or `duplicate` in the trivial sense — they are real, correctly-scoped, non-commercial technical findings, exactly the bucket §8/the Phase 2A spec defines for this. |

No candidate reached, or was framed as reaching, `useful` in the
demand-discovery sense — none is `unsupported`, since both are backed by
real tier-A evidence for the technical claim specifically (not the
commercial one).

## 12. Final verdict

**INSUFFICIENT_EXTERNAL_RECORDS.**

The committed corpus contains 0 problem_evidence records and cannot supply
them — `leakmine` was never run, and every real external citation the
Own.NET corpus does contain is technical_prevalence by the Phase 2A's own
classification rule, not a substitute for problem evidence at any volume.
This is not `CORPUS_TOO_BIASED` (bias is real, §9, but volume is the binding
constraint, not bias), not `EXTRACTION_NEEDS_TUNING` (the one real extraction
pass available to test — §6/§11 — showed correct behavior, not a defect to
tune), and not `BLOCKED_BY_CRITIC_REQUIREMENT` (the critic gate was never
reached; the blocker is upstream, at data availability).

What this replay *does* establish, positively: (1) the export→ingest
mechanical path is correct on real corpus-derived data, including the empty
case; (2) a real Claude extraction pass over real technical evidence
correctly separated "this bug is real and recurring" from "someone wants a
product for this," unprompted to do so beyond the manifest's own honesty
notes; (3) the corpus census itself (24 real repos, 43 documented technical
findings) is a legitimate, reusable technical_prevalence reference for
Own.NET/OwnAudit's own roadmap prioritization — just not a Demand Radar
input.

## Commands, run ID, verification

```bash
uv run python3 scripts/export_existing_corpus.py
uv run demand-radar init --db runs/historical-replay/store.db
uv run demand-radar ingest --product own-audit \
  --input data/live/historical-replay-evidence.jsonl \
  --db runs/historical-replay/store.db
PATH=/path/to/007/target/release:$PATH \
  uv run python3 scripts/experiment_technical_prevalence_extraction.py
```

- Exporter outputs: `data/live/historical-replay-evidence.jsonl` (0 lines),
  `data/live/historical-replay-technical-prevalence.json` (43 items) — both
  gitignored (`data/`), not committed; this document and the two scripts are
  the committed record.
- Extraction run artifacts: `runs/historical-replay/technical-prevalence-extraction/`
  (`meta.json`, `prompt.txt`, `o7-out/result.json`) — gitignored (`runs/`).
- Ingest verification: `accepted=0 inserted=0 already_present=0 errors=0`.
- Live Claude call: `status=PASS schema_valid=True error_kind=None`.
- This document's commit: see the commit that introduces it, this repo,
  branch `claude/demand-radar-mvp-9a4h1y`.
