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


def test_classify_item_excludes_named_migration_automation_accounts() -> None:
    """Regression for a real gap: these are migration/import accounts found
    in the live trial dataset that slipped past the login-based filter.
    Case-insensitive, since GitHub logins are case-preserving but not
    case-sensitive for matching purposes here.
    """
    for login in ["firebird-automations", "GoogleCodeExporter", "ironpythonbot", "orchardbot"]:
        item = _item(user={"login": login})
        assert acq.classify_item(item) == acq.BOT_AUTHOR, f"{login} must be excluded as a bot"


def test_classify_item_excludes_user_type_bot_regardless_of_login() -> None:
    """user.type=="Bot" is an independent signal from the curated login
    list -- catches registered GitHub Apps/bot accounts this script's
    curated list doesn't happen to name.
    """
    item = _item(user={"login": "some-unlisted-automation", "type": "Bot"})
    assert acq.classify_item(item) == acq.BOT_AUTHOR


def test_classify_item_accepts_explicit_user_type() -> None:
    item = _item(user={"login": "someuser", "type": "User"})
    assert acq.classify_item(item) is None


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


# --- deduplicate_by_query -------------------------------------------------------


def test_deduplicate_by_query_collapses_same_source_id_owned_by_first_query() -> None:
    r1 = {"source_id": "github_issue:a/b#1", "query_id": "q1"}
    r2 = {"source_id": "github_issue:a/b#1", "query_id": "q2"}  # same issue, later query
    r3 = {"source_id": "github_issue:a/b#2", "query_id": "q1"}
    eligible, dup_count = acq.deduplicate_by_query([("q1", [r1, r3]), ("q2", [r2])])
    assert dup_count == 1
    # q1 is first in the ordered input -> owns #1; q2's copy is dropped, not kept
    assert eligible == {"q1": [r1, r3], "q2": []}


def test_deduplicate_by_query_keeps_distinct_records() -> None:
    records = [{"source_id": f"github_issue:a/b#{i}"} for i in range(5)]
    eligible, dup_count = acq.deduplicate_by_query([("q1", records)])
    assert eligible == {"q1": records}
    assert dup_count == 0


def test_deduplicate_by_query_dedupes_within_a_single_query_too() -> None:
    r1 = {"source_id": "github_issue:a/b#1"}
    r2 = {"source_id": "github_issue:a/b#1"}
    eligible, dup_count = acq.deduplicate_by_query([("q1", [r1, r2])])
    assert eligible == {"q1": [r1]}
    assert dup_count == 1


# --- select_round_robin ----------------------------------------------------------


def test_select_round_robin_is_fair_across_queries_not_alphabetic() -> None:
    """Regression for the reported sampling defect: an earlier version of
    this script sorted all eligible records by source_id (alphabetic by
    owner/repo) and then capped, so repos whose name sorts early in ASCII
    crowded out every other query's results -- "a tournament of repo owners
    for an early ASCII slot," not a market sample. Here, the query whose
    repo names sort LAST alphabetically must still get its fair round-robin
    share.
    """
    mid = [{"source_id": f"github_issue:mmm-owner/repo#{i}"} for i in range(5)]
    late = [{"source_id": f"github_issue:zzz-owner/repo#{i}"} for i in range(5)]
    early = [{"source_id": f"github_issue:aaa-owner/repo#{i}"} for i in range(5)]

    eligible_by_query = {"query-a": mid, "query-b": late, "query-c": early}
    query_order = ["query-a", "query-b", "query-c"]  # request file's own order

    selected, accepted_per_query = acq.select_round_robin(query_order, eligible_by_query, 6)

    assert accepted_per_query == {"query-a": 2, "query-b": 2, "query-c": 2}
    # two full rounds, drawing one from each query in query_order each round
    assert [r["source_id"] for r in selected] == [
        "github_issue:mmm-owner/repo#0",
        "github_issue:zzz-owner/repo#0",
        "github_issue:aaa-owner/repo#0",
        "github_issue:mmm-owner/repo#1",
        "github_issue:zzz-owner/repo#1",
        "github_issue:aaa-owner/repo#1",
    ]


def test_select_round_robin_continues_past_exhausted_queries() -> None:
    """A query with fewer eligible records than its fair share must not
    block other queries from filling the remaining cap slots."""
    small = [{"source_id": "github_issue:a/b#1"}]
    big = [{"source_id": f"github_issue:c/d#{i}"} for i in range(5)]
    selected, accepted_per_query = acq.select_round_robin(
        ["small", "big"], {"small": small, "big": big}, max_records=4
    )
    assert accepted_per_query == {"small": 1, "big": 3}
    assert len(selected) == 4


def test_select_round_robin_covers_every_query_key_even_at_zero() -> None:
    selected, accepted_per_query = acq.select_round_robin(["only"], {"only": []}, max_records=10)
    assert selected == []
    assert accepted_per_query == {"only": 0}


def test_select_round_robin_under_capacity_takes_everything() -> None:
    records = {"q": [{"source_id": f"github_issue:a/b#{i}"} for i in range(3)]}
    selected, accepted_per_query = acq.select_round_robin(["q"], records, max_records=100)
    assert len(selected) == 3
    assert accepted_per_query == {"q": 3}


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


def test_run_acquisition_selection_is_not_alphabetically_biased() -> None:
    """End-to-end regression for the reported sampling defect (see
    select_round_robin's docstring): a query whose repos sort alphabetically
    LAST must still get a fair share of the cap, not be crowded out by a
    query whose repos happen to sort first.
    """

    def make_items(owner_prefix: str, count: int) -> list[dict]:
        return [
            _item(
                number=i,
                repository_url=f"https://api.github.com/repos/{owner_prefix}/repo",
                html_url=f"https://github.com/{owner_prefix}/repo/issues/{i}",
                user={"login": f"{owner_prefix}-user{i}"},
            )
            for i in range(count)
        ]

    pages = {
        "query-early-alpha": {"items": make_items("aaa-owner", 5)},
        "query-late-alpha": {"items": make_items("zzz-owner", 5)},
    }

    def fake_fetch(query: str, *, token: str, per_page: int) -> dict:
        return pages[query]

    config = _fake_config(
        [
            acq.QuerySpec(id="early", q="query-early-alpha"),
            acq.QuerySpec(id="late", q="query-late-alpha"),
        ],
        max_records=4,
    )
    result = acq.run_acquisition(config, token="fake-token", fetch=fake_fetch, sleep=lambda _: None)

    assert result["accepted_per_query"] == {"early": 2, "late": 2}
    repos_selected = {
        r["source_id"].split(":", 1)[1].rsplit("#", 1)[0] for r in result["validated"]
    }
    assert repos_selected == {"aaa-owner/repo", "zzz-owner/repo"}
    assert result["selection_strategy"] == "round_robin_by_query_order_then_source_id_sort"
    assert result["eligible_total"] == 10
    assert result["eligible_per_query"] == {"early": 5, "late": 5}
    assert result["excluded_by_cap_per_query"] == {"early": 3, "late": 3}


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
