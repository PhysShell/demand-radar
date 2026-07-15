#!/usr/bin/env python3
"""Phase 2C1 "Stack Overflow Acquisition" -- runs INSIDE
.github/workflows/acquire-stackoverflow-questions.yml on a GitHub-hosted
runner, never in this agent's own session (no general network egress -- see
scripts/acquire_github_issues.py's module docstring for the same, already
-verified constraint; this is the second acquisition bridge built on that
same premise). The runner calls the Stack Exchange API v2.3 directly.

Reads research/stackoverflow-acquisition-request.yaml (queries + volume
limits -- editing and pushing it is the "run acquisition" input, same
acceptance-independence pattern as Phase 2B). Writes:

  data/evidence.jsonl           RawEvidenceRecord contract (demand_radar.
                                 ingest.jsonl), validated against the real
                                 model before being written.
  data/provenance.jsonl         one record per accepted question: owner,
                                 license, tags, score, matched queries --
                                 see write_outputs() for the exact fields.
  data/manifest.json            counts, exclusions, hashes, quota.
  data/acquisition-summary.md   human-readable summary of the same run.
  data/ATTRIBUTION.md           Stack Overflow content-license notice.

The workflow commits these five files (nothing else) to a fresh orphan
branch `research-data/own-audit-stackoverflow-<date>-<run-id>` and pushes
it -- same orphan-branch delivery mechanism as Phase 2B, for the same
reason (this agent's session cannot retrieve an Actions artifact).

Determinism
-----------
- One page per query (page=1, no pagination loop) -- so re-running against
  unchanged upstream Stack Exchange state returns the same items per query.
- Dedup key (canonical identity): `question_id` -- a question matched by
  more than one query collapses to one record, owned by whichever query is
  first in the request file's own query list; every match (not just the
  owning one) is still recorded in provenance.jsonl's matched_query_ids.
- Selection when eligible records exceed max_records: deterministic
  round-robin across queries, in the request file's own query order (the
  exact fix applied to scripts/acquire_github_issues.py after a real
  reported sampling-bias defect -- see docs/decisions.log.md -- applied
  here from the start rather than discovered again the same way).
- Final output order: sorted by `source_id`, only after selection, purely
  for stable, reviewable serialization.

What is deliberately NOT done here (documented, not silently skipped)
----------------------------------------------------------------------
- No answers, comments, revisions, user profiles, images, or attachments
  fetched. `filter=withbody` on /search/advanced returns everything this
  script needs (question body included) in one call per query.
- No OAuth access token. `STACKEXCHANGE_KEY` (optional) raises the request
  quota; it is not a write credential and is never required.
- API-level failures (a response carrying error_id/error_name/error_message,
  or an HTTP 4xx) are NOT treated as "zero results" and do not let the
  workflow finish looking like a quiet, empty success -- they propagate out
  of run_acquisition() and end the process with a non-zero exit. This is a
  deliberate difference from acquire_github_issues.py's per-query
  resilience: there, a single bad query degrading gracefully was the right
  call; here, silently swallowing a contract/auth/quota failure would be
  indistinguishable from "these queries just have no matches," which is
  the one failure mode this script must not produce quietly.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from demand_radar.ingest.jsonl import RawEvidenceRecord  # noqa: E402

REQUEST_PATH = REPO_ROOT / "research" / "stackoverflow-acquisition-request.yaml"
OUT_DIR = REPO_ROOT / "data"

STACKEXCHANGE_API = "https://api.stackexchange.com/2.3/search/advanced"
API_SITE = "stackoverflow"
API_VERSION = "2.3"
USER_AGENT = "demand-radar-acquisition-bridge/0.1 (+https://github.com/PhysShell/demand-radar)"

# Ceilings this script enforces regardless of what the request file asks for
# -- the request file can only ask for LESS than these, never more.
MAX_RECORDS_CEILING = 100
BODY_TRUNCATE_CEILING = 8000
PAGE_SIZE_CEILING = 100  # Stack Exchange API's own per-page ceiling.
INTER_QUERY_SLEEP_SECONDS = 2.0

MISSING_REQUIRED_FIELD = "missing_required_field"
BEFORE_FROM_DATE = "before_from_date"
COMMUNITY_OWNED = "community_owned"
MIGRATED_AWAY = "migrated_away"
DELETED_OWNER = "deleted_owner"
EMPTY_BODY_AFTER_NORMALIZATION = "empty_body_after_normalization"
SCHEMA_VALIDATION_FAILED = "schema_validation_failed"


class StackExchangeApiError(RuntimeError):
    """A response carrying error_id/error_name/error_message -- a contract,
    auth, or quota failure, never treated as "zero results."
    """

    def __init__(
        self,
        *,
        error_id: Any = None,
        error_name: str | None = None,
        error_message: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.error_id = error_id
        self.error_name = error_name
        self.error_message = error_message
        self.status_code = status_code
        super().__init__(
            f"Stack Exchange API error {error_id} ({error_name}): {error_message} "
            f"[http {status_code}]"
        )


@dataclass
class QuerySpec:
    id: str
    q: str
    tagged: list[str] = field(default_factory=list)


@dataclass
class AcquisitionConfig:
    product: str
    site: str
    query_id_prefix: str
    max_records: int
    body_truncate_chars: int
    page_size: int
    from_date: str
    sort: str
    order: str
    queries: list[QuerySpec]


def load_config(path: Path = REQUEST_PATH) -> AcquisitionConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AcquisitionConfig(
        product=raw["product"],
        site=raw["site"],
        query_id_prefix=raw["query_id_prefix"],
        max_records=min(int(raw["max_records"]), MAX_RECORDS_CEILING),
        body_truncate_chars=min(int(raw["body_truncate_chars"]), BODY_TRUNCATE_CEILING),
        page_size=min(int(raw["page_size"]), PAGE_SIZE_CEILING),
        from_date=str(raw["from_date"]),
        sort=raw["sort"],
        order=raw["order"],
        queries=[
            QuerySpec(id=q["id"], q=q["q"], tagged=list(q.get("tagged") or []))
            for q in raw["queries"]
        ],
    )


def from_date_timestamp(from_date: str) -> int:
    """YYYY-MM-DD -> unix timestamp at 00:00:00 UTC that day."""
    parsed = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(parsed.timestamp())


# --- HTML-to-text (untrusted content: never executed, never rendered) --------


_BLOCK_TAGS = {
    "p",
    "div",
    "li",
    "br",
    "pre",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "tr",
    "table",
    "ul",
    "ol",
    "hr",
}
_SKIP_TAGS = {"script", "style"}


class _HtmlToTextParser(HTMLParser):
    """Minimal, stdlib-only HTML-to-text extraction for untrusted Stack
    Overflow post bodies. Never executes or renders the HTML -- this only
    ever walks the parse tree to collect visible text via Python's own
    HTMLParser (no external HTML/JS engine, no shell involved anywhere in
    this path). script/style content is dropped entirely; block-level tags
    (including pre/code's containing pre) become newlines so paragraphs and
    code blocks stay visually separated and readable.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    # handle_startendtag is deliberately not overridden: HTMLParser's default
    # delegates a self-closing tag (e.g. <br/>) to handle_starttag then
    # handle_endtag, which is exactly the block-tag/skip-tag behavior wanted
    # here too; any resulting double newline is collapsed by text() below.

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        lines = [line.rstrip() for line in joined.splitlines()]
        collapsed: list[str] = []
        blank_run = 0
        for line in lines:
            if line == "":
                blank_run += 1
                if blank_run > 1:
                    continue
            else:
                blank_run = 0
            collapsed.append(line)
        return "\n".join(collapsed).strip()


def html_to_text(html_body: str) -> str:
    parser = _HtmlToTextParser()
    parser.feed(html_body)
    parser.close()
    return parser.text()


# --- fetch ---------------------------------------------------------------------


def _decode_response_body(raw_bytes: bytes) -> dict[str, Any]:
    # The Stack Exchange API always gzips response bodies -- a documented
    # API behavior independent of any Accept-Encoding header sent -- and
    # urllib does not auto-decompress. A body that isn't actually gzipped
    # (e.g. a fake/test opener's canned bytes) is used as-is.
    try:
        raw_bytes = gzip.decompress(raw_bytes)
    except OSError:
        pass
    return json.loads(raw_bytes.decode("utf-8"))  # type: ignore[no-any-return]


def _raise_if_api_error(payload: dict[str, Any], *, status_code: int | None) -> None:
    if any(k in payload for k in ("error_id", "error_name", "error_message")):
        raise StackExchangeApiError(
            error_id=payload.get("error_id"),
            error_name=payload.get("error_name"),
            error_message=payload.get("error_message"),
            status_code=status_code,
        )


def fetch_search_page(
    query_text: str,
    tags: list[str],
    *,
    page_size: int,
    from_date_ts: int,
    sort: str,
    order: str,
    api_key: str | None,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    """One real HTTP call to /search/advanced. `opener` is injectable so
    tests never make a real network call. Exactly one page (page=1) --
    `has_more` in the response is deliberately never inspected: pagination
    is out of scope for this bridge.
    """
    params: dict[str, Any] = {
        "site": API_SITE,
        "sort": sort,
        "order": order,
        "fromdate": from_date_ts,
        "filter": "withbody",
        "pagesize": page_size,
        "page": 1,
    }
    if query_text:
        params["q"] = query_text
    if tags:
        params["tagged"] = ";".join(tags)
    if api_key:
        params["key"] = api_key

    request = urllib.request.Request(
        f"{STACKEXCHANGE_API}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with opener(request, timeout=30) as response:
            raw_bytes = response.read()
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        try:
            payload = _decode_response_body(body_bytes)
        except (OSError, ValueError):
            raise exc from None
        _raise_if_api_error(payload, status_code=exc.code)
        raise exc from None

    payload = _decode_response_body(raw_bytes)
    _raise_if_api_error(payload, status_code=None)
    return payload


def fetch_search_page_with_retry(
    query_text: str,
    tags: list[str],
    *,
    page_size: int,
    from_date_ts: int,
    sort: str,
    order: str,
    api_key: str | None,
    opener: Any = urllib.request.urlopen,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Exactly one retry, and only for a transient transport failure or a
    5xx response -- never for a 4xx or a parsed API error body (those are
    contract/auth/quota failures, not transient, and must not be retried
    into looking like they eventually "worked").
    """
    kwargs = {
        "page_size": page_size,
        "from_date_ts": from_date_ts,
        "sort": sort,
        "order": order,
        "api_key": api_key,
        "opener": opener,
    }
    try:
        return fetch_search_page(query_text, tags, **kwargs)
    except StackExchangeApiError as exc:
        if exc.status_code is not None and 500 <= exc.status_code < 600:
            sleep(5.0)
            return fetch_search_page(query_text, tags, **kwargs)
        raise
    except urllib.error.HTTPError as exc:
        if 500 <= exc.code < 600:
            sleep(5.0)
            return fetch_search_page(query_text, tags, **kwargs)
        raise
    except urllib.error.URLError:
        sleep(5.0)
        return fetch_search_page(query_text, tags, **kwargs)


# --- classify / build ------------------------------------------------------------


def classify_item(item: dict[str, Any], *, from_date_ts: int) -> str | None:
    """Pure, no I/O. Returns the exclusion reason, or None if the item
    should be accepted. Checks are independent; order only decides which
    single reason gets reported when more than one would apply.
    """
    question_id = item.get("question_id")
    link = item.get("link")
    creation_date = item.get("creation_date")
    if not isinstance(question_id, int) or not link or not isinstance(creation_date, int):
        return MISSING_REQUIRED_FIELD
    if creation_date < from_date_ts:
        return BEFORE_FROM_DATE
    if item.get("community_owned_date"):
        return COMMUNITY_OWNED
    if item.get("migrated_to"):
        return MIGRATED_AWAY
    owner = item.get("owner") or {}
    if owner.get("user_id") is None:
        return DELETED_OWNER
    if not owner.get("display_name"):
        return MISSING_REQUIRED_FIELD
    title = str(item.get("title") or "").strip()
    if not title:
        return MISSING_REQUIRED_FIELD
    if not item.get("content_license"):
        return MISSING_REQUIRED_FIELD
    body_text = html_to_text(str(item.get("body") or "")).strip()
    if not body_text:
        return EMPTY_BODY_AFTER_NORMALIZATION
    return None


def build_raw_record(
    item: dict[str, Any], *, query_id: str, body_truncate_chars: int
) -> tuple[dict[str, Any], bool]:
    """Pure, no I/O. Returns (record_dict, was_truncated)."""
    question_id = item["question_id"]
    title = str(item.get("title") or "").strip()
    body_text = html_to_text(str(item.get("body") or "")).strip()
    truncated = len(body_text) > body_truncate_chars
    if truncated:
        body_text = body_text[:body_truncate_chars]
    text = f"{title}\n\n{body_text}".strip() if body_text else title
    owner = item.get("owner") or {}
    published_at = datetime.fromtimestamp(item["creation_date"], tz=UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    record = {
        "source_id": f"stackoverflow_question:{question_id}",
        "source_kind": "stackoverflow_question",
        "source_family": "stackexchange",
        "url": item.get("link"),
        "author": f"stackoverflow:user:{owner['user_id']}",
        "display_name": owner.get("display_name"),
        "published_at": published_at,
        "text": text,
        "query_id": query_id,
        "language": "en",
    }
    return record, truncated


# --- dedup / selection (mirrors the corrected acquire_github_issues.py) --------


def deduplicate_by_query(
    records_by_query: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Canonical identity = source_id (question_id-derived). Whichever query
    is first in the ordered `records_by_query` list "owns" a source_id;
    every later occurrence (same query or a later one) is an exact
    duplicate. Per-query internal order is preserved in the result.
    """
    seen: set[str] = set()
    duplicate_count = 0
    result: dict[str, list[dict[str, Any]]] = {}
    for query_id, records in records_by_query:
        kept: list[dict[str, Any]] = []
        for record in records:
            source_id = record["source_id"]
            if source_id in seen:
                duplicate_count += 1
                continue
            seen.add(source_id)
            kept.append(record)
        result[query_id] = kept
    return result, duplicate_count


def select_round_robin(
    query_order: list[str],
    eligible_by_query: dict[str, list[dict[str, Any]]],
    max_records: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Deterministic, fair selection up to max_records: one record per query
    per round, visiting queries in `query_order`, continuing in rounds until
    either max_records is reached or every query is exhausted. See
    scripts/acquire_github_issues.py::select_round_robin -- same fix,
    applied identically here from the start rather than discovered again
    the same way against real Stack Overflow data.
    """
    next_index = {key: 0 for key in query_order}
    accepted_per_query = {key: 0 for key in query_order}
    selected: list[dict[str, Any]] = []
    made_progress = True
    while made_progress and len(selected) < max_records:
        made_progress = False
        for key in query_order:
            if len(selected) >= max_records:
                break
            records = eligible_by_query.get(key, [])
            idx = next_index[key]
            if idx >= len(records):
                continue
            selected.append(records[idx])
            accepted_per_query[key] += 1
            next_index[key] = idx + 1
            made_progress = True
    return selected, accepted_per_query


# --- orchestration ---------------------------------------------------------------


def run_acquisition(
    config: AcquisitionConfig,
    *,
    api_key: str | None,
    fetch: Any = fetch_search_page_with_retry,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Orchestrates, in this order: fetch -> classify/filter (including
    schema validation) -> exact question_id dedup across queries ->
    deterministic round-robin selection up to max_records -> final sort by
    source_id for stable serialization only.

    Unlike acquire_github_issues.py, a fetch failure here is NOT caught
    per-query -- it propagates out of this function (see the module
    docstring for why: a swallowed API error must never look like "zero
    matches").
    """
    from_date_ts = from_date_timestamp(config.from_date)

    fetched_per_query: dict[str, int] = {}
    exclusion_counts: dict[str, int] = {}
    truncated_count = 0
    schema_failures = 0
    records_by_query: list[tuple[str, list[dict[str, Any]]]] = []
    matched_query_ids_by_qid: dict[int, list[str]] = {}
    raw_item_by_qid: dict[int, dict[str, Any]] = {}
    truncated_qids: set[int] = set()
    quota_max: int | None = None
    quota_remaining: int | None = None
    next_min_sleep = 0.0

    for i, spec in enumerate(config.queries):
        query_id = f"{config.query_id_prefix}:{spec.id}"
        if i > 0:
            sleep(max(INTER_QUERY_SLEEP_SECONDS, next_min_sleep))
        payload = fetch(
            spec.q,
            spec.tagged,
            page_size=config.page_size,
            from_date_ts=from_date_ts,
            sort=config.sort,
            order=config.order,
            api_key=api_key,
        )
        quota_max = payload.get("quota_max", quota_max)
        quota_remaining = payload.get("quota_remaining", quota_remaining)
        next_min_sleep = float(payload.get("backoff") or 0)

        items = payload.get("items", [])
        fetched_per_query[spec.id] = len(items)
        query_records: list[dict[str, Any]] = []
        for item in items:
            reason = classify_item(item, from_date_ts=from_date_ts)
            if reason is not None:
                exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
                continue
            record, was_truncated = build_raw_record(
                item, query_id=query_id, body_truncate_chars=config.body_truncate_chars
            )
            try:
                RawEvidenceRecord.model_validate(record)
            except (
                Exception
            ) as exc:  # any validation failure excludes the record, never crashes the run
                schema_failures += 1
                print(
                    f"record {record.get('source_id')} failed schema validation: {exc}",
                    file=sys.stderr,
                )
                continue
            qid = item["question_id"]
            matched_query_ids_by_qid.setdefault(qid, []).append(query_id)
            raw_item_by_qid.setdefault(qid, item)
            if was_truncated:
                truncated_count += 1
                truncated_qids.add(qid)
            query_records.append(record)
        records_by_query.append((spec.id, query_records))

    if schema_failures:
        exclusion_counts[SCHEMA_VALIDATION_FAILED] = schema_failures

    eligible_by_query, duplicate_count = deduplicate_by_query(records_by_query)
    eligible_per_query = {key: len(records) for key, records in eligible_by_query.items()}
    eligible_total = sum(eligible_per_query.values())

    query_order = [spec.id for spec in config.queries]
    selected, accepted_per_query = select_round_robin(
        query_order, eligible_by_query, config.max_records
    )
    excluded_by_cap_per_query = {
        key: eligible_per_query[key] - accepted_per_query[key] for key in query_order
    }
    over_cap_count = eligible_total - len(selected)

    selected.sort(key=lambda r: str(r["source_id"]))  # for serialization only

    provenance: list[dict[str, Any]] = []
    for record in selected:
        qid = int(str(record["source_id"]).rsplit(":", 1)[1])
        raw_item = raw_item_by_qid[qid]
        owner = raw_item.get("owner") or {}
        provenance.append(
            {
                "source_id": record["source_id"],
                "question_id": qid,
                "question_url": record["url"],
                "owner_user_id": owner.get("user_id"),
                "owner_display_name": owner.get("display_name"),
                "owner_profile_url": owner.get("link"),
                "content_license": raw_item.get("content_license"),
                "tags": raw_item.get("tags", []),
                "score": raw_item.get("score"),
                "view_count": raw_item.get("view_count"),
                "answer_count": raw_item.get("answer_count"),
                "is_answered": raw_item.get("is_answered"),
                "accepted_answer_id": raw_item.get("accepted_answer_id"),
                "matched_query_ids": matched_query_ids_by_qid.get(qid, []),
                "body_truncated": qid in truncated_qids,
            }
        )

    return {
        "validated": selected,
        "provenance": provenance,
        "fetched_per_query": fetched_per_query,
        "exclusion_counts": exclusion_counts,
        "duplicate_count": duplicate_count,
        "over_cap_count": over_cap_count,
        "truncated_count": truncated_count,
        "selection_strategy": "round_robin_by_query_order_then_source_id_sort",
        "eligible_total": eligible_total,
        "eligible_per_query": eligible_per_query,
        "accepted_per_query": accepted_per_query,
        "excluded_by_cap_per_query": excluded_by_cap_per_query,
        "quota_max": quota_max,
        "quota_remaining": quota_remaining,
    }


def write_outputs(result: dict[str, Any], config: AcquisitionConfig) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    validated: list[dict[str, Any]] = result["validated"]
    provenance: list[dict[str, Any]] = result["provenance"]

    evidence_path = OUT_DIR / "evidence.jsonl"
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in validated]
    evidence_path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    evidence_sha256 = f"sha256:{hashlib.sha256(evidence_path.read_bytes()).hexdigest()}"

    provenance_path = OUT_DIR / "provenance.jsonl"
    prov_lines = [json.dumps(p, ensure_ascii=False, sort_keys=True) for p in provenance]
    provenance_path.write_text("".join(line + "\n" for line in prov_lines), encoding="utf-8")
    provenance_sha256 = f"sha256:{hashlib.sha256(provenance_path.read_bytes()).hexdigest()}"

    unique_authors = sorted({r["author"] for r in validated})
    fetched_total = sum(result["fetched_per_query"].values())
    license_counts: dict[str, int] = {}
    for p in provenance:
        lic = str(p.get("content_license") or "unknown")
        license_counts[lic] = license_counts.get(lic, 0) + 1

    manifest = {
        "schema": "demand-radar.stackoverflow-acquisition-manifest/1 (NOT a production schema)",
        "request_commit_sha": os.environ.get("REQUEST_COMMIT_SHA", "unknown"),
        "workflow_run_id": os.environ.get("WORKFLOW_RUN_ID", "unknown"),
        "product": config.product,
        "api_site": API_SITE,
        "api_version": API_VERSION,
        "query_parameters": {
            "sort": config.sort,
            "order": config.order,
            "from_date": config.from_date,
            "filter": "withbody",
            "page_size": config.page_size,
            "queries": [{"id": s.id, "q": s.q, "tagged": s.tagged} for s in config.queries],
        },
        "fetched_per_query": result["fetched_per_query"],
        "fetched_total": fetched_total,
        "excluded_counts": result["exclusion_counts"],
        "excluded_total": sum(result["exclusion_counts"].values()),
        "duplicate_count": result["duplicate_count"],
        "selection_strategy": result["selection_strategy"],
        "eligible_total_before_cap": result["eligible_total"],
        "eligible_per_query_before_cap": result["eligible_per_query"],
        "accepted_per_query": result["accepted_per_query"],
        "excluded_by_cap_per_query": result["excluded_by_cap_per_query"],
        "over_max_records_cap_count": result["over_cap_count"],
        "truncated_body_count": result["truncated_count"],
        "accepted_count": len(validated),
        "unique_author_count": len(unique_authors),
        "unique_authors": unique_authors,
        "count_by_content_license": license_counts,
        "collection_timestamp": datetime.now(UTC).isoformat(),
        # manifest.json cannot record its own hash (it would have to contain
        # a hash of itself, before it is fully written) -- these are the
        # other four output files; manifest.json's own hash is printed to
        # stdout and recorded in acquisition-summary.md, written after this
        # file, instead.
        "file_sha256": {
            "evidence_jsonl": evidence_sha256,
            "provenance_jsonl": provenance_sha256,
        },
        "quota_max": result["quota_max"],
        "quota_remaining": result["quota_remaining"],
        "max_records": config.max_records,
        "body_truncate_chars": config.body_truncate_chars,
    }
    manifest_path = OUT_DIR / "manifest.json"
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
    manifest_path.write_text(manifest_json, encoding="utf-8")
    manifest_sha256 = f"sha256:{hashlib.sha256(manifest_json.encode('utf-8')).hexdigest()}"

    summary = [
        f"# Stack Overflow questions acquisition — {manifest['collection_timestamp']}",
        "",
        f"Product: `{config.product}` · Run: `{manifest['workflow_run_id']}` · "
        f"Request commit: `{manifest['request_commit_sha']}`",
        "",
        "## Queries",
        "",
        *[
            f"- `{s.id}` (tagged {s.tagged}): `{s.q}` — fetched "
            f"{result['fetched_per_query'].get(s.id, 0)}, "
            f"eligible {result['eligible_per_query'].get(s.id, 0)}, "
            f"selected {result['accepted_per_query'].get(s.id, 0)}, "
            f"excluded by cap {result['excluded_by_cap_per_query'].get(s.id, 0)}"
            for s in config.queries
        ],
        "",
        "## Counts",
        "",
        f"- fetched total: {fetched_total}",
        f"- excluded total: {manifest['excluded_total']} ({result['exclusion_counts']})",
        f"- duplicates (same question, multiple queries): {result['duplicate_count']}",
        f"- eligible before cap: {result['eligible_total']}",
        f"- selection strategy: `{result['selection_strategy']}`",
        f"- over max_records cap ({config.max_records}): {result['over_cap_count']}",
        f"- truncated bodies (> {config.body_truncate_chars} chars): {result['truncated_count']}",
        f"- **accepted (in evidence.jsonl): {len(validated)}**",
        f"- unique authors: {len(unique_authors)}",
        f"- content licenses: {license_counts}",
        f"- API quota: {result['quota_remaining']} / {result['quota_max']} remaining",
        "",
        "## Files",
        "",
        "- `data/evidence.jsonl` — RawEvidenceRecord JSONL, ready for `demand-radar ingest`",
        "- `data/provenance.jsonl` — per-question owner/license/tags/scores/matched queries",
        "- `data/manifest.json` — full counts, exclusions, hashes, quota",
        "- `data/ATTRIBUTION.md` — Stack Overflow content-license notice",
        f"- evidence.jsonl sha256: `{evidence_sha256}`",
        f"- provenance.jsonl sha256: `{provenance_sha256}`",
        f"- manifest.json sha256: `{manifest_sha256}`",
        "",
    ]
    (OUT_DIR / "acquisition-summary.md").write_text("\n".join(summary), encoding="utf-8")

    attribution = [
        "# Attribution",
        "",
        "The evidence in this data branch (`data/evidence.jsonl`,",
        "`data/provenance.jsonl`) originates from Stack Overflow",
        "(https://stackoverflow.com), collected via the official Stack Exchange",
        "API v2.3 (`/search/advanced`).",
        "",
        "- Every accepted question's real author, direct post URL, and content",
        "  license are preserved in `data/provenance.jsonl`",
        "  (`owner_user_id`, `owner_display_name`, `owner_profile_url`,",
        "  `question_url`, `content_license`) -- `data/evidence.jsonl`'s own",
        "  `author` field is a stable `stackoverflow:user:<id>` identifier, not",
        "  a display name, by design (see RawEvidenceRecord's contract);",
        "  `provenance.jsonl` is where the human-readable attribution lives.",
        "- Import is limited to questions created on or after 2018-05-02 UTC",
        f"  (`from_date: {config.from_date}` in the request file).",
        "- This text is used as research evidence for opportunity discovery,",
        "  not republished, redistributed, or presented as this project's own",
        "  writing.",
        "- Any downstream report, opportunity card, or analysis that cites a",
        "  Stack Overflow question MUST preserve and display the direct post",
        "  URL (`question_url` in provenance, or `url` in the evidence",
        "  record) alongside the citation -- never paraphrase without a link",
        "  back to the original post.",
        "",
    ]
    (OUT_DIR / "ATTRIBUTION.md").write_text("\n".join(attribution), encoding="utf-8")


def main() -> int:
    config = load_config()
    api_key = os.environ.get("STACKEXCHANGE_KEY") or None

    try:
        result = run_acquisition(config, api_key=api_key)
    except StackExchangeApiError as exc:
        print(f"Stack Exchange API error, aborting: {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"transport failure calling Stack Exchange API, aborting: {exc}", file=sys.stderr)
        return 1

    write_outputs(result, config)

    print(f"fetched_total={sum(result['fetched_per_query'].values())}")
    print(f"excluded_total={sum(result['exclusion_counts'].values())} {result['exclusion_counts']}")
    print(f"duplicate_count={result['duplicate_count']}")
    print(f"accepted_count={len(result['validated'])}")
    print(f"quota_remaining={result['quota_remaining']}/{result['quota_max']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
