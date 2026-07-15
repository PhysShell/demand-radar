#!/usr/bin/env python3
"""One-off, read-only exporter -- Phase 2A "Historical External Evidence
Replay" (see docs/trials/historical-corpus-replay.md). NOT a crawler, NOT a
live collector, NOT wired into the production ingest CLI.

Converts corpus already committed in the sibling OwnAudit/Own.NET checkouts
(default: ../OwnAudit, ../Own.NET; override with OWNAUDIT_ROOT / OWNNET_ROOT)
into two local, gitignored outputs under data/live/:

  historical-replay-evidence.jsonl            RawEvidenceRecord contract
                                               (demand_radar.ingest.jsonl),
                                               problem_evidence only.

  historical-replay-technical-prevalence.json Supplemental manifest, NOT the
                                               production schema. Everything
                                               this script actually found.

Why the first file is expected to come out empty
--------------------------------------------------
Per the Phase 2A honest-semantics rule: Own.NET analyzer findings and
corpus/real-world regression cases are ALWAYS technical_prevalence, never
problem_evidence -- they prove a technical pattern occurs in real code, not
that a person voiced a problem or would pay for a fix. Every source this
script reads (OwnAudit/leakmine's own output -- which does not exist, see
below -- and Own.NET's corpus/ + docs/notes/ mining record) is exactly that
shape. So `build_problem_evidence_jsonl()` below has a real, wired code path
(it round-trips through RawEvidenceRecord and would emit real lines if it
ever received a record with a genuine external author + timestamp), but the
census this script performs supplies it with zero inputs. That is a finding,
not a bug in this script -- see the report for the corpus census that
produced it.

Sources read
------------
1. OwnAudit/leakmine/ -- a real mining PIPELINE (collect/mine/confirm/sweep/
   szz/bigquery.py), not a dataset. `leakmine-out/` (its output dir) is
   gitignored in OwnAudit and does not exist in this checkout. The one CI
   workflow that runs it (leakmine-mine.yml) has zero completed runs
   (confirmed via the GitHub Actions API at replay time). Recorded as a
   zero-record source, not silently skipped.
2. Own.NET/corpus/{real-world,wpf,di}/*/notes.md -- parsed programmatically
   for a github.com/OWNER/REPO/(pull|issues)/NUMBER reference. Mechanical,
   not hand-transcribed.
3. A hand-curated, source-cited transcription of Own.NET's own mining
   write-ups (docs/notes/oracle-sweep-2026-07-10.md, oracle-sweep-rerun-
   2026-07-11.md, real-world-mining.md) -- these are markdown prose+tables
   with no stable machine-readable form, so TIER_A below transcribes them
   with an explicit `source_doc` citation on every entry, faithful to the
   tables as read on 2026-07-15. Not re-derived from raw CI logs.
4. A breadth census of GitHub Actions run history (oracle.yml, mine.yml,
   mine-on-push.yml, mine-run.yml in PhysShell/Own.NET) -- repo names, run
   ids, dates, commit shas where the run title states one, taken from run
   *metadata* only (titles/timestamps via the GitHub API). No job log or
   artifact content was read for TIER_B; it is real (these are actual
   completed CI runs) but *unverified in this replay* beyond the title.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNAUDIT_ROOT = Path(os.environ.get("OWNAUDIT_ROOT", REPO_ROOT.parent / "OwnAudit"))
OWNNET_ROOT = Path(os.environ.get("OWNNET_ROOT", REPO_ROOT.parent / "Own.NET"))
OUT_DIR = REPO_ROOT / "data" / "live"

GITHUB_REF_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(pull|issues)/(\d+)")


@dataclass
class TechnicalPrevalenceItem:
    """One documented technical_prevalence data point. NOT the EvidenceItem
    schema -- there is no author to hash and usually no single publish
    timestamp (a repeatedly-rerun analyzer sweep isn't a "published" event).
    """

    repo: str
    tier: str  # "A" verified/triaged | "B" named in CI history only | "C" cited-origin
    finding_summary: str
    verdict: str  # e.g. "TP", "FP", "review", "mixed", "not independently re-verified"
    source_doc: str  # path or "GitHub Actions run <id>", auditable back to a real artifact
    commit: str | None = None
    url: str | None = None
    excluded_reason: str | None = None  # set when found but NOT counted (e.g. internal issue ref)


@dataclass
class Census:
    technical_prevalence: list[TechnicalPrevalenceItem] = field(default_factory=list)
    problem_evidence_raw: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. leakmine -- confirm it produced nothing (checked, not assumed).
# ---------------------------------------------------------------------------
def check_leakmine(census: Census) -> None:
    leakmine_dir = OWNAUDIT_ROOT / "leakmine"
    out_dir = OWNAUDIT_ROOT / "leakmine-out"
    if not leakmine_dir.is_dir():
        census.notes.append(
            f"OwnAudit/leakmine/ not found under {OWNAUDIT_ROOT} -- skipped entirely."
        )
        return
    py_files = sorted(p.name for p in leakmine_dir.glob("*.py"))
    census.notes.append(
        f"OwnAudit/leakmine/ is a mining PIPELINE ({', '.join(py_files)}), not a dataset. "
        f"leakmine-out/ (its gitignored output dir) exists on disk: {out_dir.is_dir()}. "
        "leakmine-mine.yml (the CI workflow that runs it) has 0 completed runs as of this "
        "replay (GitHub Actions API, checked 2026-07-15) -- 0 records available from this source."
    )
    if out_dir.is_dir():
        found = list(out_dir.rglob("*"))
        census.notes.append(
            f"UNEXPECTED: leakmine-out/ exists with {len(found)} entries -- "
            "inspect manually, not auto-imported by this script."
        )


# ---------------------------------------------------------------------------
# 2. Own.NET/corpus/{real-world,wpf,di}/*/notes.md -- mechanical scan.
# ---------------------------------------------------------------------------
def scan_corpus_notes(census: Census) -> None:
    case_dirs: list[Path] = []
    for sub in ("real-world", "wpf", "di"):
        d = OWNNET_ROOT / "corpus" / sub
        if d.is_dir():
            case_dirs.extend(sorted(p.parent for p in d.glob("*/notes.md")))

    seen_urls: dict[str, str] = {}  # url -> first case dir that cited it (dedup)
    for case_dir in case_dirs:
        notes = (case_dir / "notes.md").read_text(encoding="utf-8")
        matches = GITHUB_REF_RE.findall(notes)
        if not matches:
            census.excluded.append(
                {
                    "case": case_dir.name,
                    "reason": (
                        "no github.com issue/PR URL in notes.md -- pattern is "
                        "representative, not tied to a specific traceable external "
                        "record (Phase 2A provenance rule requires one)"
                    ),
                }
            )
            continue
        for owner_repo, kind, number in matches:
            url = f"https://github.com/{owner_repo}/{kind}/{number}"
            if owner_repo.lower() in ("physshell/own.net", "physshell/ownaudit"):
                census.excluded.append(
                    {
                        "case": case_dir.name,
                        "url": url,
                        "reason": "internal self-reference, not external",
                    }
                )
                continue
            if url in seen_urls:
                census.notes.append(
                    f"corpus case '{case_dir.name}' cites the same external record as "
                    f"'{seen_urls[url]}' ({url}) -- canonical identity is repo+issue/PR number, "
                    "so this is ONE external record, not two."
                )
                continue
            seen_urls[url] = case_dir.name
            corpus_sub = case_dir.relative_to(OWNNET_ROOT / "corpus").parts[0]
            census.technical_prevalence.append(
                TechnicalPrevalenceItem(
                    repo=owner_repo,
                    tier="C",
                    finding_summary=(
                        f"corpus/real-world/{case_dir.name} -- hand-reduced regression "
                        "case citing this record as the pattern's real-world origin"
                    ),
                    verdict=(
                        "cited-origin (not independently re-fetched; "
                        "see notes.md for the paraphrase)"
                    ),
                    source_doc=f"Own.NET/corpus/{corpus_sub}/{case_dir.name}/notes.md",
                    url=url,
                )
            )


# ---------------------------------------------------------------------------
# 3. TIER A -- hand-transcribed, source-cited, from the 3 write-up docs.
#    Every row below is faithful to a specific doc; verify by re-reading the
#    cited file. Commits are copied verbatim from those docs.
# ---------------------------------------------------------------------------
_ORACLE_SWEEP_DOC = "Own.NET/docs/notes/oracle-sweep-2026-07-10.md"
_ORACLE_RERUN_DOC = "Own.NET/docs/notes/oracle-sweep-rerun-2026-07-11.md"
_MINING_DOC = "Own.NET/docs/notes/real-world-mining.md"

TIER_A: list[TechnicalPrevalenceItem] = [
    # --- oracle sweep 2026-07-10 (issue #201), per-repo triaged clusters ---
    TechnicalPrevalenceItem(
        "ShareX/ShareX",
        "A",
        "OWN001 Controls.Add/AddRange transitive disposal (~24 sites)",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="0df9ca4",
    ),
    TechnicalPrevalenceItem(
        "ShareX/ShareX",
        "A",
        "OWN001 IContainer-registered components (~6 sites)",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="0df9ca4",
    ),
    TechnicalPrevalenceItem(
        "ShareX/ShareX",
        "A",
        "OWN001 using(field = new T()) (3 sites)",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="0df9ca4",
    ),
    TechnicalPrevalenceItem(
        "ShareX/ShareX",
        "A",
        "OWN001 HistoryItemManager_ContextMenu cluster, no Dispose() at all (48 findings)",
        "TP",
        _ORACLE_SWEEP_DOC,
        commit="0df9ca4",
    ),
    TechnicalPrevalenceItem(
        "ShareX/ShareX",
        "A",
        "OWN001 ShapeManagerMenu.cs / menuForm leak -- flagship (39 findings); "
        "reduced to corpus/real-world/sharex-shapemanager-menuform-leak",
        "TP",
        _ORACLE_SWEEP_DOC,
        commit="0df9ca4",
    ),
    TechnicalPrevalenceItem(
        "ShareX/ShareX",
        "A",
        "OWN001 plain Timer/ImageList fields never disposed (4 sites)",
        "TP",
        _ORACLE_SWEEP_DOC,
        commit="0df9ca4",
    ),
    TechnicalPrevalenceItem(
        "ShareX/ShareX",
        "A",
        "OWN001 custom-type Dispose semantics unread (3 sites)",
        "review",
        _ORACLE_SWEEP_DOC,
        commit="0df9ca4",
    ),
    TechnicalPrevalenceItem(
        "ShareX/ShareX",
        "A",
        "OWN001 event-subscription findings, bulk (89 sites, 1 spot-checked)",
        "review, bulk",
        _ORACLE_SWEEP_DOC,
        commit="0df9ca4",
    ),
    TechnicalPrevalenceItem(
        "MahApps/MahApps.Metro",
        "A",
        "OWN001 CommandTriggerAction.cs:116 DP subscription-rotation",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="72099e3",
    ),
    TechnicalPrevalenceItem(
        "MahApps/MahApps.Metro",
        "A",
        "OWN001 TiltBehavior.cs:70 Behavior<T>.AssociatedObject",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="72099e3",
    ),
    TechnicalPrevalenceItem(
        "MahApps/MahApps.Metro",
        "A",
        "OWN001 MetroWindow.cs:1448 template-part local pattern var",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="72099e3",
    ),
    TechnicalPrevalenceItem(
        "MaterialDesignInXAML/MaterialDesignInXamlToolkit",
        "A",
        "OWN001 App.xaml.cs:22 app-scoped themeManager",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="ef3a5ea",
    ),
    TechnicalPrevalenceItem(
        "MaterialDesignInXAML/MaterialDesignInXamlToolkit",
        "A",
        "OWN001 7x themeManager.ThemeChanged on plain windows/VMs, bulk",
        "review, bulk",
        _ORACLE_SWEEP_DOC,
        commit="ef3a5ea",
    ),
    TechnicalPrevalenceItem(
        "MaterialDesignInXAML/MaterialDesignInXamlToolkit",
        "A",
        "OWN001 SmartHint.cs:205-208 DP rotation (4 findings)",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="ef3a5ea",
    ),
    TechnicalPrevalenceItem(
        "MaterialDesignInXAML/MaterialDesignInXamlToolkit",
        "A",
        "OWN001 CircleWipe.cs/FadeWipe.cs returned-fresh-Timeline",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="ef3a5ea",
    ),
    TechnicalPrevalenceItem(
        "MaterialDesignInXAML/MaterialDesignInXamlToolkit",
        "A",
        "OWN001 ListsAndGridsViewModel.cs self-owned-collection-element",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="ef3a5ea",
    ),
    TechnicalPrevalenceItem(
        "icsharpcode/AvalonEdit",
        "A",
        "OWN001 AbstractMargin/LineNumberMargin/FoldingMargin DP rotation (3 sites)",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="ed0bd14",
    ),
    TechnicalPrevalenceItem(
        "icsharpcode/AvalonEdit",
        "A",
        "OWN001 OverloadViewer.cs:58,64 template-part local (2 sites)",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="ed0bd14",
    ),
    TechnicalPrevalenceItem(
        "icsharpcode/AvalonEdit",
        "A",
        "OWN014 ImeSupport.cs:47 CommandManager.RequerySuggested (weak-event)",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="ed0bd14",
    ),
    TechnicalPrevalenceItem(
        "icsharpcode/AvalonEdit",
        "A",
        "OWN001 DropDownButton.cs:78 self-detaching handler",
        "FP",
        _ORACLE_SWEEP_DOC,
        commit="ed0bd14",
    ),
    TechnicalPrevalenceItem(
        "icsharpcode/AvalonEdit",
        "A",
        "OWN001 Caret.cs/TextAreaAutomationPeer.cs/ImeSupport.cs:48 composition-owned back-refs",
        "review",
        _ORACLE_SWEEP_DOC,
        commit="ed0bd14",
    ),
    TechnicalPrevalenceItem(
        "icsharpcode/AvalonEdit",
        "A",
        "OWN001 TextView.cs:1843,1946 services/hoverLogic, no Dispose() at all",
        "TP",
        _ORACLE_SWEEP_DOC,
        commit="ed0bd14",
    ),
    TechnicalPrevalenceItem(
        "ClosedXML/ClosedXML",
        "A",
        "OWN001 Slice.cs Enumerator locals, Dispose() statically empty "
        "(5 sites) -- clean-code control",
        "FP",
        _ORACLE_SWEEP_DOC,
    ),
    # --- oracle sweep rerun 2026-07-11: confirms fixes silenced the FPs above, 0 regressions ---
    TechnicalPrevalenceItem(
        "MahApps/MahApps.Metro, MaterialDesignInXamlToolkit, AvalonEdit, ShareX",
        "A",
        "Rerun on identical pinned commits after PR #230/#231: OWN001+OWN014 "
        "285->269 (-16), 0 new findings; every real TP above still flagged",
        "confirmed (precision gain, no regressions)",
        _ORACLE_RERUN_DOC,
    ),
    # --- real-world-mining.md milestone 1 ---
    TechnicalPrevalenceItem(
        "DapperLib/Dapper",
        "A",
        "OWN001 BenchmarkBase._connection, undisposed SqlConnection field (benchmark project)",
        "TP",
        _MINING_DOC,
        commit="72a54c4",
    ),
    TechnicalPrevalenceItem(
        "JoshClose/CsvHelper",
        "A",
        "OWN001 undisposed StreamReader/Writer/CsvDataReader locals in tests "
        "(43 findings; using-scoped locals correctly skipped)",
        "TP",
        _MINING_DOC,
        commit="33970e5",
    ),
    TechnicalPrevalenceItem(
        "NickeManarin/ScreenToGif",
        "A",
        "OWN001 VideoSource.xaml.cs:50-83 view->view-model lambda leak, 4 inline "
        "subscriptions never detached -- flagship; "
        "corpus/real-world/screentogif-loaded-subscription",
        "TP (warning tier)",
        _MINING_DOC,
        commit="27a49c3",
    ),
    TechnicalPrevalenceItem(
        "NickeManarin/ScreenToGif",
        "A",
        "OWN001 GraphicsConfigurationDialog/Troubleshoot "
        "SystemEvents.DisplaySettingsChanged, never detached -- "
        "corpus/real-world/screentogif-systemevents-leak",
        "TP (error tier, cross-tool confirmed: CodeQL agrees, "
        "only Own.NET class oracle has no query for)",
        _MINING_DOC,
        commit="27a49c3",
    ),
]

# ---------------------------------------------------------------------------
# 4. TIER B -- breadth census from GitHub Actions run *titles* only (oracle.yml,
#    mine.yml, mine-on-push.yml, mine-run.yml in PhysShell/Own.NET). Real,
#    completed CI runs; repo names and commits below are copied from the run
#    title/metadata as returned by the GitHub Actions API on 2026-07-15. No
#    job log or artifact content was fetched for these -- unverified beyond
#    the title, unlike TIER_A.
# ---------------------------------------------------------------------------
TIER_B: list[TechnicalPrevalenceItem] = [
    TechnicalPrevalenceItem(
        "StackExchange/StackExchange.Redis",
        "B",
        "oracle run: 'StackExchange.Redis (src/) -- fresh async/connection-heavy repo'",
        "not independently re-verified",
        "GitHub Actions run 28489064469 / 28015525935 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "protobuf-net/protobuf-net",
        "B",
        "oracle run: cross-tool oracle on protobuf-net/protobuf-net",
        "not independently re-verified",
        "GitHub Actions run 28288698673 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "NLog/NLog",
        "B",
        "oracle run: re-run on NLog to confirm the dispose-helper fix clears 4 timer findings",
        "not independently re-verified",
        "GitHub Actions run 28291035613 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "serilog/serilog",
        "B",
        "oracle run: cross-tool oracle on serilog/serilog; mine run: fresh logging-domain repo",
        "not independently re-verified",
        "GitHub Actions run 28288697345 / 28017309028 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "JamesNK/Newtonsoft.Json",
        "B",
        "oracle run: cross-tool oracle on JamesNK/Newtonsoft.Json (dotted test dirs excluded)",
        "not independently re-verified",
        "GitHub Actions run 28288696849 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "RestSharp/RestSharp",
        "B",
        "oracle run (dev): target RestSharp/RestSharp",
        "not independently re-verified",
        "GitHub Actions run 28277891441 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "App-vNext/Polly",
        "B",
        "oracle run: target Polly, re-checked across 3 separate runs "
        "(D5.2/D5.4 interprocedural exercises)",
        "not independently re-verified",
        "GitHub Actions run 28276751864 / 28257875923 / 28219556226 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "npgsql/npgsql",
        "B",
        "oracle run: cross-tool check Npgsql v8.0.9; mine run: mine + re-mine "
        "to verify 3 merged FP-fixes",
        "not independently re-verified",
        "GitHub Actions run 28024878326 / 27953332804 / 28007045840 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "SixLabors/ImageSharp",
        "B",
        "mine run: mine + 2x re-mine for ArrayPool/MemoryPool detector validation",
        "not independently re-verified",
        "GitHub Actions run 27946951457 / 27949660130 / 27950215006 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "neuecc/MessagePack-CSharp",
        "B",
        "mine run: one-off mining runner (FP audit)",
        "not independently re-verified",
        "GitHub Actions run 27939076311 / 27939557514 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "mgravell/Pipelines.Sockets.Unofficial",
        "B",
        "mine run: re-mine Pipelines to verify the PipeReader FP fix (BCL-pool-heavy)",
        "not independently re-verified",
        "GitHub Actions run 27940332931 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "WalletWasabi/WalletWasabi",
        "B",
        "mine run: point push-miner at WalletWasabi (Avalonia UI) for "
        "subscription tiering, re-mined once",
        "not independently re-verified",
        "GitHub Actions run 27816073915 / 27821262838 / 27818746212 (Own.NET)",
    ),
    TechnicalPrevalenceItem(
        "Flow-Launcher/Flow.Launcher",
        "B",
        "mine run: test the imba on a fresh WPF app (Flow.Launcher)",
        "not independently re-verified",
        "GitHub Actions run 27871494148 (Own.NET)",
    ),
]

# The one real mine.yml `workflow_dispatch` run (as opposed to the push-triggered
# dev-loop scaffolding) produced a genuine `mine-report` artifact (2354 bytes,
# sha256:7f8308131aa8da9557b19708311cc56bd6cff0a7b5d7071538e112473ba36e77,
# run 27634247136, 2026-06-16). Its *content* could not be pulled into this
# replay: the GitHub API returns a signed Azure Blob Storage download URL, and
# that host is outside this session's network egress policy (blocked, not
# bypassed, per the Phase 2A network-policy constraint). Recorded here rather
# than silently dropped.
MINE_REPORT_ARTIFACT_NOTE = (
    "mine.yml run 27634247136 (2026-06-16, PR #19 'Corpus miner: run own-check over "
    "public C# repos + aggregate a report') produced artifact 'mine-report' "
    "(2354 bytes, sha256:7f8308...36e77b, not expired as of this replay). Content "
    "NOT fetched: download resolves to productionresultssa19.blob.core.windows.net, "
    "outside this session's egress allowlist -- confirmed via a real attempt (curl, "
    "403 at the proxy), not assumed. The artifact's existence and size are real "
    "(GitHub Actions API); its contents are simply out of reach in this replay."
)


def build_problem_evidence_jsonl(census: Census) -> list[str]:
    """Round-trips any genuine problem_evidence record through the real
    RawEvidenceRecord model, so a future record with a real author+timestamp
    is validated the same way `demand-radar ingest` will validate it. Empty
    today -- see module docstring.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from demand_radar.ingest.jsonl import RawEvidenceRecord

    lines = []
    for raw in census.problem_evidence_raw:
        record = RawEvidenceRecord.model_validate(
            raw
        )  # raises loudly if a future entry is malformed
        lines.append(record.model_dump_json())
    return lines


def main() -> int:
    census = Census()
    check_leakmine(census)
    scan_corpus_notes(census)
    census.technical_prevalence.extend(TIER_A)
    census.technical_prevalence.extend(TIER_B)
    census.notes.append(MINE_REPORT_ARTIFACT_NOTE)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    evidence_lines = build_problem_evidence_jsonl(census)
    evidence_path = OUT_DIR / "historical-replay-evidence.jsonl"
    evidence_path.write_text("".join(line + "\n" for line in evidence_lines), encoding="utf-8")

    manifest_path = OUT_DIR / "historical-replay-technical-prevalence.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": (
                    "demand-radar.historical-replay-technical-prevalence/1 "
                    "(NOT a production schema)"
                ),
                "technical_prevalence": [asdict(item) for item in census.technical_prevalence],
                "excluded": census.excluded,
                "notes": census.notes,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    distinct_repos = {item.repo for item in census.technical_prevalence}
    tier_a = [i for i in census.technical_prevalence if i.tier == "A"]
    tier_b = [i for i in census.technical_prevalence if i.tier == "B"]
    tier_c = [i for i in census.technical_prevalence if i.tier == "C"]

    print(f"problem_evidence records (RawEvidenceRecord, ready to ingest): {len(evidence_lines)}")
    print(f"technical_prevalence items total: {len(census.technical_prevalence)}")
    print(f"  tier A (fully verified, hand-triaged): {len(tier_a)}")
    print(f"  tier B (named in CI history, not re-verified here): {len(tier_b)}")
    print(f"  tier C (cited in a corpus regression case): {len(tier_c)}")
    print(f"distinct real external repos referenced: {len(distinct_repos)}")
    print(f"excluded candidates (no verifiable external provenance): {len(census.excluded)}")
    print(f"wrote: {evidence_path}")
    print(f"wrote: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
