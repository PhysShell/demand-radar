"""Offline tests for scripts/acquire_stackoverflow_questions.py's pure logic
-- html_to_text / classify_item / build_raw_record / deduplicate_by_query /
select_round_robin / run_acquisition / write_outputs. None of these touch
the real Stack Exchange API: fetch is injectable, matching the same
offline-testable pattern as scripts/acquire_github_issues.py. Live network
behavior is verified only when the real workflow runs on a GitHub-hosted
runner (see docs/trials/phase-2c-combined-trial.md once that exists).
"""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "acquire_stackoverflow_questions.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("acquire_stackoverflow_questions", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


acq = _load_script()

FROM_DATE = "2018-05-02"
FROM_DATE_TS = acq.from_date_timestamp(FROM_DATE)


def _item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "question_id": 12345678,
        "link": "https://stackoverflow.com/questions/12345678/some-slug",
        "title": "Persistent memory leak in WPF event handler subscription",
        "body": "<p>We have a persistent memory leak when subscribing to "
        "<code>PropertyChanged</code> events on a long-lived view model.</p>",
        "creation_date": FROM_DATE_TS + 86400,  # one day after the floor
        "owner": {
            "user_id": 555,
            "display_name": "Some User",
            "link": "https://stackoverflow.com/users/555/some-user",
        },
        "content_license": "CC BY-SA 4.0",
        "tags": ["c#", "wpf"],
        "score": 3,
        "view_count": 120,
        "answer_count": 1,
        "is_answered": True,
        "accepted_answer_id": None,
    }
    base.update(overrides)
    return base


# --- html_to_text --------------------------------------------------------------


def test_html_to_text_strips_tags_and_preserves_text() -> None:
    html = "<p>We have a <strong>memory leak</strong> in our WPF app.</p>"
    assert acq.html_to_text(html) == "We have a memory leak in our WPF app."


def test_html_to_text_strips_script_and_style_content() -> None:
    html = "<p>Visible text</p><script>alert(1)</script><style>.x{color:red}</style>"
    text = acq.html_to_text(html)
    assert "Visible text" in text
    assert "alert" not in text
    assert "color:red" not in text


def test_html_to_text_preserves_code_fragment_indentation() -> None:
    html = "<p>Repro:</p><pre><code>void Foo() {\n    handler += OnBar;\n}</code></pre>"
    text = acq.html_to_text(html)
    assert "    handler += OnBar;" in text


def test_html_to_text_unescapes_entities() -> None:
    html = "<p>See &lt;Window&gt; docs &amp; friends.</p>"
    assert acq.html_to_text(html) == "See <Window> docs & friends."


def test_html_to_text_empty_input_returns_empty() -> None:
    assert acq.html_to_text("") == ""
    assert acq.html_to_text("<p></p>") == ""


def test_html_to_text_never_shells_out_or_renders() -> None:
    """Untrusted content: a script-injection payload must come back as
    inert text (if anything), never executed -- there is no code path in
    html_to_text that invokes subprocess/eval/exec."""
    html = "<img src=x onerror=alert(1)><p>real text</p>"
    text = acq.html_to_text(html)
    assert "real text" in text
    assert "onerror" not in text  # attribute values are never emitted as text


# --- classify_item -----------------------------------------------------------


def test_classify_item_accepts_a_well_formed_question() -> None:
    assert acq.classify_item(_item(), from_date_ts=FROM_DATE_TS) is None


def test_classify_item_excludes_missing_question_id() -> None:
    item = _item()
    del item["question_id"]
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.MISSING_REQUIRED_FIELD


def test_classify_item_excludes_missing_link() -> None:
    item = _item(link=None)
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.MISSING_REQUIRED_FIELD


def test_classify_item_excludes_before_from_date() -> None:
    item = _item(creation_date=FROM_DATE_TS - 1)
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.BEFORE_FROM_DATE


def test_classify_item_excludes_community_owned() -> None:
    item = _item(community_owned_date="2019-01-01T00:00:00Z")
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.COMMUNITY_OWNED


def test_classify_item_excludes_migrated_away() -> None:
    item = _item(migrated_to={"on_date": 1600000000, "other_site": {}})
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.MIGRATED_AWAY


def test_classify_item_excludes_deleted_owner_missing_user_id() -> None:
    item = _item(owner={"display_name": "ghost"})
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.DELETED_OWNER


def test_classify_item_excludes_owner_missing_display_name() -> None:
    item = _item(owner={"user_id": 555})
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.MISSING_REQUIRED_FIELD


def test_classify_item_excludes_empty_title() -> None:
    item = _item(title="   ")
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.MISSING_REQUIRED_FIELD


def test_classify_item_excludes_missing_content_license() -> None:
    item = _item(content_license=None)
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.MISSING_REQUIRED_FIELD


def test_classify_item_excludes_body_empty_after_normalization() -> None:
    """An image-only body has no visible text once HTML is stripped."""
    item = _item(body='<p><img src="diagram.png"></p>')
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.EMPTY_BODY_AFTER_NORMALIZATION


def test_classify_item_excludes_missing_body_entirely() -> None:
    item = _item(body=None)
    assert acq.classify_item(item, from_date_ts=FROM_DATE_TS) == acq.EMPTY_BODY_AFTER_NORMALIZATION


# --- build_raw_record ---------------------------------------------------------


def test_build_raw_record_shape() -> None:
    record, truncated = acq.build_raw_record(
        _item(), query_id="own-audit-stackoverflow:wpf-memory-leak", body_truncate_chars=8000
    )
    assert record["source_id"] == "stackoverflow_question:12345678"
    assert record["source_kind"] == "stackoverflow_question"
    assert record["source_family"] == "stackexchange"
    assert record["url"] == "https://stackoverflow.com/questions/12345678/some-slug"
    assert record["author"] == "stackoverflow:user:555"
    assert record["display_name"] == "Some User"
    assert record["query_id"] == "own-audit-stackoverflow:wpf-memory-leak"
    assert record["language"] == "en"
    assert "Persistent memory leak" in record["text"]
    assert "PropertyChanged" in record["text"]
    assert truncated is False


def test_build_raw_record_author_is_stable_user_id_not_display_name() -> None:
    record, _ = acq.build_raw_record(
        _item(owner={"user_id": 999, "display_name": "changed their name"}),
        query_id="q",
        body_truncate_chars=8000,
    )
    assert record["author"] == "stackoverflow:user:999"
    assert "changed their name" not in record["author"]


def test_build_raw_record_published_at_is_iso8601_utc() -> None:
    record, _ = acq.build_raw_record(
        _item(creation_date=1525219200), query_id="q", body_truncate_chars=8000
    )
    assert record["published_at"] == "2018-05-02T00:00:00Z"


def test_build_raw_record_truncates_long_body_and_reports_it() -> None:
    long_body = "<p>" + "x" * 9000 + "</p>"
    record, truncated = acq.build_raw_record(
        _item(body=long_body), query_id="q", body_truncate_chars=8000
    )
    assert truncated is True
    body_in_text = record["text"].split("\n\n", 1)[1]
    assert len(body_in_text) == 8000


# --- fetch_search_page: URL/query encoding, fromdate, filter=withbody, gzip ----


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = gzip.compress(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_fetch_search_page_builds_correct_query_params() -> None:
    captured: dict[str, Any] = {}

    def fake_opener(request: Any, timeout: int = 30) -> _FakeResponse:
        captured["url"] = request.full_url
        return _FakeResponse({"items": [], "quota_max": 300, "quota_remaining": 299})

    acq.fetch_search_page(
        "memory leak",
        ["wpf"],
        page_size=30,
        from_date_ts=FROM_DATE_TS,
        sort="creation",
        order="desc",
        api_key=None,
        opener=fake_opener,
    )
    url = captured["url"]
    assert url.startswith(acq.STACKEXCHANGE_API + "?")
    assert "site=stackoverflow" in url
    assert "sort=creation" in url
    assert "order=desc" in url
    assert f"fromdate={FROM_DATE_TS}" in url
    assert "filter=withbody" in url
    assert "pagesize=30" in url
    assert "page=1" in url
    assert "tagged=wpf" in url
    assert "q=memory" in url and "leak" in url  # urlencoded, space becomes +
    assert "key=" not in url


def test_fetch_search_page_includes_key_when_present() -> None:
    captured: dict[str, Any] = {}

    def fake_opener(request: Any, timeout: int = 30) -> _FakeResponse:
        captured["url"] = request.full_url
        return _FakeResponse({"items": []})

    acq.fetch_search_page(
        "q",
        [],
        page_size=30,
        from_date_ts=FROM_DATE_TS,
        sort="creation",
        order="desc",
        api_key="secret-key-123",
        opener=fake_opener,
    )
    assert "key=secret-key-123" in captured["url"]


def test_fetch_search_page_decodes_gzip_response() -> None:
    def fake_opener(request: Any, timeout: int = 30) -> _FakeResponse:
        return _FakeResponse({"items": [{"question_id": 1}], "quota_remaining": 42})

    payload = acq.fetch_search_page(
        "q",
        [],
        page_size=30,
        from_date_ts=FROM_DATE_TS,
        sort="creation",
        order="desc",
        api_key=None,
        opener=fake_opener,
    )
    assert payload["items"] == [{"question_id": 1}]
    assert payload["quota_remaining"] == 42


def test_fetch_search_page_raises_on_error_payload_without_http_error() -> None:
    def fake_opener(request: Any, timeout: int = 30) -> _FakeResponse:
        return _FakeResponse(
            {"error_id": 500, "error_name": "internal_error", "error_message": "oops"}
        )

    with pytest.raises(acq.StackExchangeApiError) as exc_info:
        acq.fetch_search_page(
            "q",
            [],
            page_size=30,
            from_date_ts=FROM_DATE_TS,
            sort="creation",
            order="desc",
            api_key=None,
            opener=fake_opener,
        )
    assert exc_info.value.error_id == 500
    assert exc_info.value.error_name == "internal_error"


def test_fetch_search_page_raises_stackexchangeapierror_on_http_error_with_body() -> None:
    body = json.dumps(
        {"error_id": 400, "error_name": "bad_parameter", "error_message": "invalid fromdate"}
    ).encode("utf-8")

    def fake_opener(request: Any, timeout: int = 30) -> Any:
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", None, io.BytesIO(gzip.compress(body))
        )

    with pytest.raises(acq.StackExchangeApiError) as exc_info:
        acq.fetch_search_page(
            "q",
            [],
            page_size=30,
            from_date_ts=FROM_DATE_TS,
            sort="creation",
            order="desc",
            api_key=None,
            opener=fake_opener,
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_name == "bad_parameter"


def test_fetch_search_page_reraises_http_error_with_unparseable_body() -> None:
    def fake_opener(request: Any, timeout: int = 30) -> Any:
        raise urllib.error.HTTPError(
            request.full_url, 502, "Bad Gateway", None, io.BytesIO(b"not json at all")
        )

    with pytest.raises(urllib.error.HTTPError):
        acq.fetch_search_page(
            "q",
            [],
            page_size=30,
            from_date_ts=FROM_DATE_TS,
            sort="creation",
            order="desc",
            api_key=None,
            opener=fake_opener,
        )


# --- fetch_search_page_with_retry: backoff/retry semantics --------------------


def test_fetch_with_retry_retries_once_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def fake_opener(request: Any, timeout: int = 30) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                request.full_url, 503, "Service Unavailable", None, io.BytesIO(b"{}")
            )
        return _FakeResponse({"items": [{"question_id": 1}]})

    sleeps: list[float] = []
    payload = acq.fetch_search_page_with_retry(
        "q",
        [],
        page_size=30,
        from_date_ts=FROM_DATE_TS,
        sort="creation",
        order="desc",
        api_key=None,
        opener=fake_opener,
        sleep=sleeps.append,
    )
    assert calls["n"] == 2
    assert payload["items"] == [{"question_id": 1}]
    assert len(sleeps) == 1


def test_fetch_with_retry_does_not_retry_on_4xx() -> None:
    calls = {"n": 0}
    body = json.dumps(
        {"error_id": 400, "error_name": "bad_parameter", "error_message": "nope"}
    ).encode("utf-8")

    def fake_opener(request: Any, timeout: int = 30) -> Any:
        calls["n"] += 1
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", None, io.BytesIO(gzip.compress(body))
        )

    with pytest.raises(acq.StackExchangeApiError):
        acq.fetch_search_page_with_retry(
            "q",
            [],
            page_size=30,
            from_date_ts=FROM_DATE_TS,
            sort="creation",
            order="desc",
            api_key=None,
            opener=fake_opener,
            sleep=lambda _: None,
        )
    assert calls["n"] == 1  # never retried


def test_fetch_with_retry_retries_once_on_transport_error() -> None:
    calls = {"n": 0}

    def fake_opener(request: Any, timeout: int = 30) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("connection reset")
        return _FakeResponse({"items": []})

    acq.fetch_search_page_with_retry(
        "q",
        [],
        page_size=30,
        from_date_ts=FROM_DATE_TS,
        sort="creation",
        order="desc",
        api_key=None,
        opener=fake_opener,
        sleep=lambda _: None,
    )
    assert calls["n"] == 2


# --- deduplicate_by_query / select_round_robin (same fix as acquire_github_issues) --


def test_deduplicate_by_query_collapses_same_source_id_owned_by_first_query() -> None:
    r1 = {"source_id": "stackoverflow_question:1", "query_id": "q1"}
    r2 = {"source_id": "stackoverflow_question:1", "query_id": "q2"}
    r3 = {"source_id": "stackoverflow_question:2", "query_id": "q1"}
    eligible, dup_count = acq.deduplicate_by_query([("q1", [r1, r3]), ("q2", [r2])])
    assert dup_count == 1
    assert eligible == {"q1": [r1, r3], "q2": []}


def test_select_round_robin_is_fair_across_queries_not_alphabetic() -> None:
    mid = [{"source_id": f"stackoverflow_question:{i}"} for i in range(500, 505)]
    late = [{"source_id": f"stackoverflow_question:{i}"} for i in range(900, 905)]
    early = [{"source_id": f"stackoverflow_question:{i}"} for i in range(100, 105)]
    eligible_by_query = {"query-a": mid, "query-b": late, "query-c": early}
    selected, accepted_per_query = acq.select_round_robin(
        ["query-a", "query-b", "query-c"], eligible_by_query, 6
    )
    assert accepted_per_query == {"query-a": 2, "query-b": 2, "query-c": 2}
    assert len(selected) == 6


# --- run_acquisition (end to end, fake fetch, no network) ---------------------


def _fake_config(queries: list[Any], max_records: int = 100) -> Any:
    return acq.AcquisitionConfig(
        product="own-audit",
        site="stackoverflow",
        query_id_prefix="test",
        max_records=max_records,
        body_truncate_chars=8000,
        page_size=30,
        from_date=FROM_DATE,
        sort="creation",
        order="desc",
        queries=queries,
    )


def test_run_acquisition_dedupes_across_queries_and_tracks_matched_query_ids() -> None:
    shared_item = _item(question_id=1)
    other_item = _item(
        question_id=2,
        link="https://stackoverflow.com/questions/2/other",
        owner={
            "user_id": 777,
            "display_name": "Other User",
            "link": "https://stackoverflow.com/users/777/other-user",
        },
    )
    deleted_owner_item = _item(question_id=3, owner={"display_name": "ghost"})

    pages = {
        "whatever-a": {
            "items": [shared_item, deleted_owner_item],
            "quota_max": 300,
            "quota_remaining": 250,
        },
        "whatever-b": {
            "items": [shared_item, other_item],
            "quota_max": 300,
            "quota_remaining": 249,
        },
    }

    def fake_fetch(query: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        return pages[query]

    config = _fake_config(
        [
            acq.QuerySpec(id="query-a", q="whatever-a", tagged=["wpf"]),
            acq.QuerySpec(id="query-b", q="whatever-b", tagged=["c#"]),
        ]
    )
    result = acq.run_acquisition(config, api_key=None, fetch=fake_fetch, sleep=lambda _: None)

    assert result["fetched_per_query"] == {"query-a": 2, "query-b": 2}
    assert result["exclusion_counts"] == {acq.DELETED_OWNER: 1}
    assert result["duplicate_count"] == 1
    assert len(result["validated"]) == 2
    source_ids = {r["source_id"] for r in result["validated"]}
    assert source_ids == {"stackoverflow_question:1", "stackoverflow_question:2"}
    assert result["quota_remaining"] == 249

    prov_by_id = {p["source_id"]: p for p in result["provenance"]}
    assert prov_by_id["stackoverflow_question:1"]["matched_query_ids"] == [
        "test:query-a",
        "test:query-b",
    ]
    assert prov_by_id["stackoverflow_question:2"]["matched_query_ids"] == ["test:query-b"]
    assert prov_by_id["stackoverflow_question:1"]["owner_user_id"] == 555
    assert prov_by_id["stackoverflow_question:1"]["content_license"] == "CC BY-SA 4.0"
    assert prov_by_id["stackoverflow_question:1"]["tags"] == ["c#", "wpf"]


def test_run_acquisition_enforces_max_records_cap() -> None:
    items = [
        _item(
            question_id=i,
            link=f"https://stackoverflow.com/questions/{i}/x",
            owner={"user_id": i, "display_name": f"user{i}", "link": f"https://x/{i}"},
        )
        for i in range(10)
    ]

    def fake_fetch(query: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        return {"items": items}

    config = _fake_config([acq.QuerySpec(id="q", q="whatever", tagged=["wpf"])], max_records=3)
    result = acq.run_acquisition(config, api_key=None, fetch=fake_fetch, sleep=lambda _: None)

    assert len(result["validated"]) == 3
    assert result["over_cap_count"] == 7


def test_run_acquisition_selection_is_not_alphabetically_biased() -> None:
    def make_items(start: int, count: int) -> list[dict[str, Any]]:
        return [
            _item(
                question_id=start + i,
                link=f"https://stackoverflow.com/questions/{start + i}/x",
                owner={
                    "user_id": start + i,
                    "display_name": f"user{start + i}",
                    "link": f"https://x/{start + i}",
                },
            )
            for i in range(count)
        ]

    pages = {
        "query-early": {"items": make_items(100, 5)},
        "query-late": {"items": make_items(900, 5)},
    }

    def fake_fetch(query: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        return pages[query]

    config = _fake_config(
        [
            acq.QuerySpec(id="early", q="query-early", tagged=["wpf"]),
            acq.QuerySpec(id="late", q="query-late", tagged=["c#"]),
        ],
        max_records=4,
    )
    result = acq.run_acquisition(config, api_key=None, fetch=fake_fetch, sleep=lambda _: None)

    assert result["accepted_per_query"] == {"early": 2, "late": 2}
    assert result["selection_strategy"] == "round_robin_by_query_order_then_source_id_sort"


def test_run_acquisition_propagates_api_error_instead_of_swallowing() -> None:
    """A Stack Exchange API error must never look like "zero results" --
    unlike acquire_github_issues.py's per-query resilience, this must
    propagate out of run_acquisition entirely."""

    def fake_fetch(query: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        raise acq.StackExchangeApiError(
            error_id=502, error_name="quota_exceeded", error_message="too many requests"
        )

    config = _fake_config([acq.QuerySpec(id="q", q="whatever", tagged=["wpf"])])
    with pytest.raises(acq.StackExchangeApiError):
        acq.run_acquisition(config, api_key=None, fetch=fake_fetch, sleep=lambda _: None)


def test_run_acquisition_honors_backoff_before_next_query() -> None:
    pages = {
        "q1": {"items": [], "backoff": 7},
        "q2": {"items": []},
    }

    def fake_fetch(query: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        return pages[query]

    sleeps: list[float] = []
    config = _fake_config(
        [
            acq.QuerySpec(id="a", q="q1", tagged=["wpf"]),
            acq.QuerySpec(id="b", q="q2", tagged=["c#"]),
        ]
    )
    acq.run_acquisition(config, api_key=None, fetch=fake_fetch, sleep=sleeps.append)
    assert len(sleeps) == 1
    assert sleeps[0] >= 7  # backoff from q1's response honored before q2


def test_run_acquisition_is_deterministic_across_repeated_calls() -> None:
    items = [
        _item(
            question_id=i,
            link=f"https://stackoverflow.com/questions/{i}/x",
            owner={"user_id": i, "display_name": f"user{i}", "link": f"https://x/{i}"},
        )
        for i in range(20)
    ]

    def fake_fetch(query: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        return {"items": items}

    config = _fake_config([acq.QuerySpec(id="q", q="whatever", tagged=["wpf"])], max_records=7)
    result_a = acq.run_acquisition(config, api_key=None, fetch=fake_fetch, sleep=lambda _: None)
    result_b = acq.run_acquisition(config, api_key=None, fetch=fake_fetch, sleep=lambda _: None)
    assert [r["source_id"] for r in result_a["validated"]] == [
        r["source_id"] for r in result_b["validated"]
    ]


# --- write_outputs: attribution fields + manifest arithmetic (tmp_path only) ---


def test_write_outputs_manifest_arithmetic_and_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acq, "OUT_DIR", tmp_path)
    monkeypatch.setenv("REQUEST_COMMIT_SHA", "deadbeef")
    monkeypatch.setenv("WORKFLOW_RUN_ID", "999")

    items = [
        _item(
            question_id=i,
            link=f"https://stackoverflow.com/questions/{i}/x",
            owner={"user_id": i, "display_name": f"user{i}", "link": f"https://x/{i}"},
            content_license="CC BY-SA 4.0" if i % 2 == 0 else "CC BY-SA 3.0",
        )
        for i in range(6)
    ]

    def fake_fetch(query: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        return {"items": items, "quota_max": 300, "quota_remaining": 280}

    config = _fake_config([acq.QuerySpec(id="q", q="whatever", tagged=["wpf"])], max_records=100)
    result = acq.run_acquisition(config, api_key=None, fetch=fake_fetch, sleep=lambda _: None)
    acq.write_outputs(result, config)

    for name in (
        "evidence.jsonl",
        "provenance.jsonl",
        "manifest.json",
        "acquisition-summary.md",
        "ATTRIBUTION.md",
    ):
        assert (tmp_path / name).is_file(), f"missing {name}"

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["request_commit_sha"] == "deadbeef"
    assert manifest["workflow_run_id"] == "999"
    assert manifest["accepted_count"] == 6
    assert manifest["fetched_total"] == 6
    assert manifest["excluded_total"] == 0
    # fetched = excluded + duplicates + eligible; eligible = accepted + over_cap
    assert manifest["fetched_total"] == (
        manifest["excluded_total"]
        + manifest["duplicate_count"]
        + manifest["eligible_total_before_cap"]
    )
    assert (
        manifest["eligible_total_before_cap"]
        == manifest["accepted_count"] + manifest["over_max_records_cap_count"]
    )
    assert manifest["count_by_content_license"] == {"CC BY-SA 4.0": 3, "CC BY-SA 3.0": 3}
    assert manifest["quota_max"] == 300
    assert manifest["quota_remaining"] == 280
    assert "evidence_jsonl" in manifest["file_sha256"]
    assert "provenance_jsonl" in manifest["file_sha256"]

    attribution = (tmp_path / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Stack Overflow" in attribution
    assert "provenance.jsonl" in attribution
    assert "2018-05-02" in attribution
    assert "research evidence" in attribution
    assert "direct post URL" in attribution.lower() or "question_url" in attribution

    provenance_lines = (
        (tmp_path / "provenance.jsonl").read_text(encoding="utf-8").strip().splitlines()
    )
    assert len(provenance_lines) == 6
    first = json.loads(provenance_lines[0])
    assert set(first.keys()) == {
        "source_id",
        "question_id",
        "question_url",
        "owner_user_id",
        "owner_display_name",
        "owner_profile_url",
        "content_license",
        "tags",
        "score",
        "view_count",
        "answer_count",
        "is_answered",
        "accepted_answer_id",
        "matched_query_ids",
        "body_truncated",
    }


# --- load_config against the real, committed request file ---------------------


def test_load_config_parses_the_real_request_file() -> None:
    config = acq.load_config()
    assert config.product == "own-audit"
    assert config.site == "stackoverflow"
    assert config.max_records <= acq.MAX_RECORDS_CEILING
    assert config.body_truncate_chars <= acq.BODY_TRUNCATE_CEILING
    assert config.page_size <= acq.PAGE_SIZE_CEILING
    assert config.from_date == "2018-05-02"
    assert config.sort == "creation"
    assert config.order == "desc"
    assert len(config.queries) == 10
    assert all(q.id and q.q and q.tagged for q in config.queries)
