"""Ingestion acceptance criteria -- spec section 23.1."""

from __future__ import annotations

import json
from pathlib import Path

from demand_radar.ingest.jsonl import ingest_jsonl_file
from demand_radar.storage.sqlite import Store

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "mixed-demand-signals.jsonl"


def test_repeat_import_creates_no_new_rows(tmp_path: Path) -> None:
    store = Store.init(tmp_path / "d.db")
    first = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    count_after_first = store.count_evidence("own-audit")

    second = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    count_after_second = store.count_evidence("own-audit")

    assert count_after_first == count_after_second
    assert len(second.inserted_ids) == 0
    assert len(second.already_present_ids) == len(first.accepted)
    store.close()


def test_malformed_json_line_reported_not_fatal(tmp_path: Path) -> None:
    store = Store.init(tmp_path / "d.db")
    result = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    json_errors = [e for e in result.errors if "invalid JSON" in e.message]
    assert len(json_errors) == 1
    store.close()


def test_schema_invalid_line_reported_not_fatal(tmp_path: Path) -> None:
    store = Store.init(tmp_path / "d.db")
    result = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    schema_errors = [e for e in result.errors if "schema validation failed" in e.message]
    assert len(schema_errors) == 1
    store.close()


def test_invalid_lines_do_not_corrupt_the_rest_of_the_batch(tmp_path: Path) -> None:
    store = Store.init(tmp_path / "d.db")
    result = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    assert len(result.accepted) == 22
    assert len(result.errors) == 2
    store.close()


def test_content_hash_stable_on_reimport(tmp_path: Path) -> None:
    store = Store.init(tmp_path / "d.db")
    result = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    hashes_first = {item.id: item.content.content_hash for item in result.accepted}

    result2 = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    hashes_second = {item.id: item.content.content_hash for item in result2.accepted}

    assert hashes_first == hashes_second
    store.close()


def test_record_without_url_is_accepted(tmp_path: Path) -> None:
    store = Store.init(tmp_path / "d.db")
    result = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    no_url_items = [item for item in result.accepted if item.source.url is None]
    assert len(no_url_items) >= 1
    store.close()


def test_idempotent_import_across_process_restarts(tmp_path: Path) -> None:
    """A fresh Store instance (simulating a new CLI invocation) re-importing
    the same file must still be a no-op, not just within one Python object."""
    db_path = tmp_path / "d.db"
    store1 = Store.init(db_path)
    ingest_jsonl_file(FIXTURE, product="own-audit", store=store1)
    store1.close()

    store2 = Store.open(db_path)
    before = store2.count_evidence("own-audit")
    result = ingest_jsonl_file(FIXTURE, product="own-audit", store=store2)
    after = store2.count_evidence("own-audit")
    assert before == after
    assert len(result.inserted_ids) == 0
    store2.close()


def test_two_records_same_content_different_url_both_stored_but_flagged_elsewhere(
    tmp_path: Path,
) -> None:
    """Ingestion itself stores every accepted record regardless of duplicate
    status -- deduplication is a separate, later pipeline stage (spec
    section 18), not something ingestion decides."""
    store = Store.init(tmp_path / "d.db")
    result = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    by_content_hash: dict[str, int] = {}
    for item in result.accepted:
        by_content_hash[item.content.content_hash] = (
            by_content_hash.get(item.content.content_hash, 0) + 1
        )
    assert any(count > 1 for count in by_content_hash.values())
    store.close()


def test_malformed_record_error_is_understandable(tmp_path: Path) -> None:
    store = Store.init(tmp_path / "d.db")
    result = ingest_jsonl_file(FIXTURE, product="own-audit", store=store)
    for e in result.errors:
        assert e.line_number > 0
        assert len(e.message) > 0
    store.close()


def test_ingest_missing_file_raises(tmp_path: Path) -> None:
    store = Store.init(tmp_path / "d.db")
    try:
        ingest_jsonl_file(tmp_path / "does-not-exist.jsonl", product="own-audit", store=store)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError:
        pass
    finally:
        store.close()


def test_single_bad_record_written_by_hand_does_not_break_valid_siblings(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    good = {
        "source_id": "good-1",
        "source_kind": "forum_post",
        "source_family": "forum",
        "url": "https://example.com/1",
        "author": "a",
        "published_at": "2026-06-01T00:00:00Z",
        "text": "a perfectly valid record",
        "query_id": "q1",
    }
    bad = {"source_id": "bad-1"}  # missing everything else
    path.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")

    store = Store.init(tmp_path / "d.db")
    result = ingest_jsonl_file(path, product="own-audit", store=store)
    assert len(result.accepted) == 1
    assert len(result.errors) == 1
    store.close()
