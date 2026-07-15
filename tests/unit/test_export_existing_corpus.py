"""Non-empty round-trip for scripts/export_existing_corpus.py's
build_problem_evidence_jsonl(). The real Phase 2A corpus census never
populated census.problem_evidence_raw (0 problem_evidence records -- see
docs/trials/historical-corpus-replay.md), so that code path had zero
coverage beyond the empty-list case. This test exercises it with one
synthetic, clearly-fake record -- never counted in the replay's research
counts, never written to data/live/ -- to prove
dict -> RawEvidenceRecord -> JSONL line -> re-parsed by the real ingest
module is correct end to end, not just that empty input produces empty
output.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from demand_radar.ingest.jsonl import parse_jsonl_records

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "export_existing_corpus.py"


def _load_export_script():
    spec = importlib.util.spec_from_file_location("export_existing_corpus", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves `from __future__ import annotations` field types via
    # sys.modules.get(cls.__module__) -- the module must be registered before
    # exec_module runs, or CorpusObservation's own dataclass fields fail to resolve.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_problem_evidence_jsonl_round_trips_a_synthetic_record(tmp_path: Path) -> None:
    export_existing_corpus = _load_export_script()

    synthetic_record = {
        "source_id": "test-only:not-a-real-corpus-record",
        "source_kind": "github_issue",
        "source_family": "github",
        "url": "https://example.invalid/not-real",
        "author": "test-fixture-author",
        "published_at": "2026-01-01T00:00:00Z",
        "text": "synthetic record for round-trip testing only, never part of the real census",
        "query_id": "test-fixture",
    }
    census = export_existing_corpus.Census(problem_evidence_raw=[synthetic_record])

    lines = export_existing_corpus.build_problem_evidence_jsonl(census)
    assert len(lines) == 1

    jsonl_path = tmp_path / "synthetic.jsonl"
    jsonl_path.write_text(lines[0] + "\n", encoding="utf-8")

    records, errors = parse_jsonl_records(jsonl_path)
    assert errors == []
    assert len(records) == 1
    _, record = records[0]
    assert record.source_id == "test-only:not-a-real-corpus-record"
    assert record.text.startswith("synthetic record for round-trip testing only")


def test_build_problem_evidence_jsonl_stays_empty_with_no_input() -> None:
    export_existing_corpus = _load_export_script()
    assert (
        export_existing_corpus.build_problem_evidence_jsonl(export_existing_corpus.Census()) == []
    )
