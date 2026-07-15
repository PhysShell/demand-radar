"""Offline tests for scripts/acquire_github_issues.py's pure logic --
classify_item / build_raw_record / deduplicate / run_acquisition. None of
these touch the real GitHub Search API: run_acquisition takes an injectable
`fetch` callable, matching the same offline-testable pattern as
demand_radar.ingest.rss's `_content_override`. Live network behavior is
verified only when the real workflow runs on a GitHub-hosted runner (see
docs/trials/github-live-issues-trial.md once that exists).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "acquire_github_issues.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("acquire_github_issues", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


acq = _load_script()


def _item(**overrides: Any) -> dict[str, Any]:
    base = {
        "number": 123,
        "title": "Persistent memory leak in event handler subscription",
        "body": "We have a persistent memory leak that occurs when subscribing to "
        "PropertyChanged events on a long-lived view model.",
        "user": {"login": "someuser"},
        "created_at": "2024-01-15T10:30:00Z",
        "html_url": "https://github.com/someorg/somerepo/issues/123",
        "repository_url": "https://api.github.com/repos/someorg/somerepo",
    }
    base.update(overrides)
    return base


# --- classify_item -----------------------------------------------------------


def test_classify_item_accepts_a_well_formed_issue() -> None:
    assert acq.classify_item(_item()) is None


def test_classify_item_excludes_pull_requests() -> None:
    item = _item(pull_request={"url": "https://api.github.com/repos/someorg/somerepo/pulls/123"})
    assert acq.classify_item(item) == acq.PULL_REQUEST


def test_classify_item_excludes_physshell_owner_case_insensitive() -> None:
    item = _item(repository_url="https://api.github.com/repos/PhysShell/demand-radar")
    assert acq.classify_item(item) == acq.PHYSSHELL_OWNER
    item2 = _item(repository_url="https://api.github.com/repos/physshell/own.net")
    assert acq.classify_item(item2) == acq.PHYSSHELL_OWNER


def test_classify_item_excludes_known_bot_logins() -> None:
    item = _item(user={"login": "dependabot"})
    assert acq.classify_item(item) == acq.BOT_AUTHOR


def test_classify_item_excludes_bracket_bot_suffix() -> None:
    item = _item(user={"login": "some-custom-bot[bot]"})
    assert acq.classify_item(item) == acq.BOT_AUTHOR


def test_classify_item_excludes_missing_author() -> None:
    item = _item(user=None)
    assert acq.classify_item(item) == acq.MISSING_AUTHOR_OR_CREATED_AT


def test_classify_item_excludes_missing_created_at() -> None:
    item = _item(created_at=None)
    assert acq.classify_item(item) == acq.MISSING_AUTHOR_OR_CREATED_AT


def test_classify_item_excludes_no_body_and_short_title() -> None:
    item = _item(body="", title="fix it")
    assert acq.classify_item(item) == acq.NO_BODY_AND_NO_MEANINGFUL_TITLE


def test_classify_item_accepts_no_body_with_meaningful_title() -> None:
    item = _item(body="", title="A sufficiently long and descriptive issue title here")
    assert acq.classify_item(item) is None


# --- build_raw_record ---------------------------------------------------------


def test_build_raw_record_shape() -> None:
    record, truncated = acq.build_raw_record(
        _item(), query_id="own-audit-live:memory-leak-csharp", body_truncate_chars=8000
    )
    assert record["source_id"] == "github_issue:someorg/somerepo#123"
    assert record["source_kind"] == "github_issue"
    assert record["source_family"] == "github"
    assert record["url"] == "https://github.com/someorg/somerepo/issues/123"
    assert record["author"] == "someuser"
    assert record["published_at"] == "2024-01-15T10:30:00Z"
    assert record["query_id"] == "own-audit-live:memory-leak-csharp"
    assert "Persistent memory leak" in record["text"]
    assert truncated is False


def test_build_raw_record_truncates_long_body_and_reports_it() -> None:
    long_body = "x" * 9000
    record, truncated = acq.build_raw_record(
        _item(body=long_body), query_id="q", body_truncate_chars=8000
    )
    assert truncated is True
    # text = title + "\n\n" + truncated body; body portion must be exactly capped
    body_in_text = record["text"].split("\n\n", 1)[1]
    assert len(body_in_text) == 8000


# --- deduplicate ---------------------------------------------------------------


def test_deduplicate_collapses_same_source_id() -> None:
    r1 = {"source_id": "github_issue:a/b#1"}
    r2 = {"source_id": "github_issue:a/b#1"}
    r3 = {"source_id": "github_issue:a/b#2"}
    deduped, dup_count = acq.deduplicate([r1, r2, r3])
    assert len(deduped) == 2
    assert dup_count == 1


def test_deduplicate_keeps_distinct_records() -> None:
    records = [{"source_id": f"github_issue:a/b#{i}"} for i in range(5)]
    deduped, dup_count = acq.deduplicate(records)
    assert len(deduped) == 5
    assert dup_count == 0


# --- run_acquisition (end to end, fake fetch, no network) ---------------------


def _fake_config(queries: list[acq.QuerySpec], max_records: int = 100) -> Any:
    return acq.AcquisitionConfig(
        product="own-audit",
        query_id_prefix="test",
        max_records=max_records,
        body_truncate_chars=8000,
        queries=queries,
    )


def test_run_acquisition_dedupes_across_queries_and_reports_counts() -> None:
    shared_item = _item(number=1, repository_url="https://api.github.com/repos/org1/repo1")
    other_item = _item(
        number=2,
        repository_url="https://api.github.com/repos/org2/repo2",
        html_url="https://github.com/org2/repo2/issues/2",
        user={"login": "otheruser"},
    )
    bot_item = _item(number=3, user={"login": "dependabot"})

    # Keyed by the actual query text run_acquisition passes through (spec.q),
    # not by query id -- matches the real fetch(spec.q, ...) call shape.
    pages = {
        "whatever-a": {"items": [shared_item, bot_item]},
        "whatever-b": {"items": [shared_item, other_item]},
    }

    def fake_fetch(query: str, *, token: str, per_page: int) -> dict:
        return pages[query]

    config = _fake_config(
        [acq.QuerySpec(id="query-a", q="whatever-a"), acq.QuerySpec(id="query-b", q="whatever-b")]
    )
    result = acq.run_acquisition(config, token="fake-token", fetch=fake_fetch, sleep=lambda _: None)

    assert result["fetched_per_query"] == {"query-a": 2, "query-b": 2}
    assert result["exclusion_counts"] == {acq.BOT_AUTHOR: 1}
    assert result["duplicate_count"] == 1  # shared_item matched by both queries
    assert len(result["validated"]) == 2  # shared_item once + other_item
    source_ids = {r["source_id"] for r in result["validated"]}
    assert source_ids == {"github_issue:org1/repo1#1", "github_issue:org2/repo2#2"}


def test_run_acquisition_enforces_max_records_cap() -> None:
    items = [
        _item(
            number=i,
            repository_url=f"https://api.github.com/repos/org/repo{i}",
            html_url=f"https://github.com/org/repo{i}/issues/{i}",
            user={"login": f"user{i}"},
        )
        for i in range(10)
    ]

    def fake_fetch(query: str, *, token: str, per_page: int) -> dict:
        return {"items": items}

    config = _fake_config([acq.QuerySpec(id="q", q="whatever")], max_records=3)
    result = acq.run_acquisition(config, token="fake-token", fetch=fake_fetch, sleep=lambda _: None)

    assert len(result["validated"]) == 3
    assert result["over_cap_count"] == 7


def test_run_acquisition_survives_a_failing_query() -> None:
    def fake_fetch(query: str, *, token: str, per_page: int) -> dict:
        if query == "boom":
            import urllib.error

            raise urllib.error.URLError("network is down")
        return {"items": [_item()]}

    config = _fake_config([acq.QuerySpec(id="ok", q="fine"), acq.QuerySpec(id="broken", q="boom")])
    result = acq.run_acquisition(config, token="fake-token", fetch=fake_fetch, sleep=lambda _: None)

    assert result["fetched_per_query"]["broken"] == 0
    assert len(result["validated"]) == 1


# --- load_config against the real, committed request file ---------------------


def test_load_config_parses_the_real_request_file() -> None:
    config = acq.load_config()
    assert config.product == "own-audit"
    assert config.max_records <= acq.MAX_RECORDS_CEILING
    assert config.body_truncate_chars <= acq.BODY_TRUNCATE_CEILING
    assert len(config.queries) == 10
    assert all(q.id and q.q for q in config.queries)
