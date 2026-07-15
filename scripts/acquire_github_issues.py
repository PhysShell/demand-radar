#!/usr/bin/env python3
"""Phase 2B "GitHub Actions Acquisition Bridge" -- runs INSIDE
.github/workflows/acquire-github-issues.yml on a GitHub-hosted runner, never
in this agent's own session (which has no general network egress and cannot
add cross-owner repositories -- see docs/trials/historical-corpus-replay.md
for why that path was exhausted in Phase 2A). The runner has real internet
access and a real `GITHUB_TOKEN`; this script is what actually calls
GitHub's Search API.

Reads research/acquisition-request.yaml (queries + volume limits -- see that
file's own comments: editing and pushing it is the "run acquisition" input,
since this agent cannot invoke workflow_dispatch either). Writes:

  data/evidence.jsonl           RawEvidenceRecord contract (demand_radar.
                                 ingest.jsonl), validated against the real
                                 model before being written, not just
                                 shaped to look like it.
  data/manifest.json            counts, exclusions, hashes -- see
                                 write_outputs() for the exact fields.
  data/acquisition-summary.md   human-readable summary of the same run.

The workflow commits these three files (nothing else) to a fresh orphan
branch `research-data/<product>-live-<date>-<run-id>` and pushes it --
deliberately NOT an Actions artifact, since this agent's session cannot
retrieve one (the download URL resolves to Azure Blob Storage, outside its
network egress allowlist -- confirmed by a real, blocked attempt during
Phase 2A).

Determinism
-----------
- Per-query fetch: `sort=created&order=asc`, one page, so the same
  underlying GitHub state always returns items in the same order.
- Dedup key (canonical identity): `source_id` (`github_issue:owner/repo#N`)
  -- an issue matched by more than one query collapses to one record,
  logged as a duplicate, not two.
- Final output order: sorted by `source_id` -- alphabetic, trivially
  auditable, independent of any API pagination/tie-break quirk.
- Re-running this workflow against unchanged upstream GitHub state
  reproduces the same evidence.jsonl content; re-running it later reflects
  whatever changed on GitHub in the meantime, which is real data changing,
  not a determinism bug here. Ingesting the output of two different runs
  into the same demand-radar store still dedupes correctly at that layer
  too, via `evidence_id_for()`'s own deterministic hashing.

What is deliberately NOT done here (documented, not silently skipped)
----------------------------------------------------------------------
- No comment fetching. The Phase 2B spec allows comments only as "limited
  context, never a separate evidence item / independent author" -- i.e. it
  is optional. Fetching comments would add one extra API call per accepted
  issue (up to 100 more requests) for uncertain value in a first bridge;
  `text` is title + (possibly truncated) body only. A future revision can
  add bounded comment context without changing this contract.
- No fuzzy cross-post similarity dedup. Canonical-identity dedup (same
  issue matched by 2+ queries) is this script's job; near-duplicate CONTENT
  across different issues/repos is exactly what demand-radar's existing
  production dedup step (`ingest/normalize.py` + the deterministic core) is
  for -- reimplementing it here would duplicate, not add, capability.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from demand_radar.ingest.jsonl import RawEvidenceRecord  # noqa: E402

REQUEST_PATH = REPO_ROOT / "research" / "acquisition-request.yaml"
OUT_DIR = REPO_ROOT / "data"

GITHUB_SEARCH_API = "https://api.github.com/search/issues"
USER_AGENT = "demand-radar-acquisition-bridge/0.1 (+https://github.com/PhysShell/demand-radar)"

# Ceilings this script enforces regardless of what the request file asks for
# -- the request file can only ask for LESS than these, never more.
MAX_RECORDS_CEILING = 100
BODY_TRUNCATE_CEILING = 8000
PER_QUERY_FETCH_CAP = 30  # one page/query; 10 queries well under the 30 req/min Search API limit
INTER_QUERY_SLEEP_SECONDS = 2.0
MIN_MEANINGFUL_TITLE_CHARS = 15

EXCLUDE_OWNERS_LOWER = {"physshell"}
BOT_LOGIN_EXACT_LOWER = {
    "dependabot",
    "dependabot-preview",
    "renovate",
    "renovate-bot",
    "snyk-bot",
    "greenkeeper",
    "greenkeeperio-bot",
    "imgbot",
    "allcontributors",
    "codecov-commenter",
    "github-actions",
}

PULL_REQUEST = "pull_request"
PHYSSHELL_OWNER = "physshell_owner"
BOT_AUTHOR = "bot_author"
MISSING_AUTHOR_OR_CREATED_AT = "missing_author_or_created_at"
NO_BODY_AND_NO_MEANINGFUL_TITLE = "no_body_and_no_meaningful_title"
SCHEMA_VALIDATION_FAILED = "schema_validation_failed"


@dataclass
class QuerySpec:
    id: str
    q: str


@dataclass
class AcquisitionConfig:
    product: str
    query_id_prefix: str
    max_records: int
    body_truncate_chars: int
    queries: list[QuerySpec]


def load_config(path: Path = REQUEST_PATH) -> AcquisitionConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AcquisitionConfig(
        product=raw["product"],
        query_id_prefix=raw["query_id_prefix"],
        max_records=min(int(raw["max_records"]), MAX_RECORDS_CEILING),
        body_truncate_chars=min(int(raw["body_truncate_chars"]), BODY_TRUNCATE_CEILING),
        queries=[QuerySpec(id=q["id"], q=q["q"]) for q in raw["queries"]],
    )


def fetch_search_page(
    query: str,
    *,
    token: str,
    per_page: int,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    """One real HTTP call to GitHub's Search Issues API. `opener` is
    injectable so tests never make a real network call.
    """
    params = urllib.parse.urlencode(
        {
            "q": query,
            "sort": "created",
            "order": "asc",
            "per_page": per_page,
        }
    )
    request = urllib.request.Request(
        f"{GITHUB_SEARCH_API}?{params}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    with opener(request, timeout=30) as response:
        payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return payload


def fetch_search_page_with_retry(
    query: str, *, token: str, per_page: int, opener: Any = urllib.request.urlopen
) -> dict[str, Any]:
    try:
        return fetch_search_page(query, token=token, per_page=per_page, opener=opener)
    except (urllib.error.URLError, urllib.error.HTTPError):
        time.sleep(5.0)
        return fetch_search_page(query, token=token, per_page=per_page, opener=opener)


def _repo_full_name_from_item(item: dict[str, Any]) -> str:
    # Search Issues API items have no direct repo field -- only
    # repository_url: "https://api.github.com/repos/{owner}/{repo}".
    repo_url = str(item.get("repository_url", ""))
    parts = repo_url.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else "unknown/unknown"


def classify_item(item: dict[str, Any]) -> str | None:
    """Pure, no I/O. Returns the exclusion reason, or None if the item
    should be accepted.
    """
    if PULL_REQUEST in item:
        return PULL_REQUEST
    repo_full_name = _repo_full_name_from_item(item)
    owner = repo_full_name.split("/", 1)[0].lower()
    if owner in EXCLUDE_OWNERS_LOWER:
        return PHYSSHELL_OWNER
    user = item.get("user") or {}
    login = user.get("login")
    created_at = item.get("created_at")
    if not login or not created_at:
        return MISSING_AUTHOR_OR_CREATED_AT
    login_lower = str(login).lower()
    if login_lower in BOT_LOGIN_EXACT_LOWER or login_lower.endswith("[bot]"):
        return BOT_AUTHOR
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    if not body and len(title) < MIN_MEANINGFUL_TITLE_CHARS:
        return NO_BODY_AND_NO_MEANINGFUL_TITLE
    return None


def build_raw_record(
    item: dict[str, Any], *, query_id: str, body_truncate_chars: int
) -> tuple[dict[str, Any], bool]:
    """Pure, no I/O. Returns (record_dict, was_truncated)."""
    repo_full_name = _repo_full_name_from_item(item)
    number = item["number"]
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    truncated = len(body) > body_truncate_chars
    if truncated:
        body = body[:body_truncate_chars]
    text = f"{title}\n\n{body}".strip() if body else title
    record = {
        "source_id": f"github_issue:{repo_full_name}#{number}",
        "source_kind": "github_issue",
        "source_family": "github",
        "url": item.get("html_url"),
        "author": item["user"]["login"],
        "published_at": item["created_at"],
        "text": text,
        "query_id": query_id,
    }
    return record, truncated


def deduplicate(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Canonical identity = source_id. First-seen wins; input order is
    itself deterministic (query-list order, then per-query created-ascending
    API order), so this is deterministic given the same upstream data.
    """
    seen: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for record in records:
        key = record["source_id"]
        if key in seen:
            duplicate_count += 1
            continue
        seen[key] = record
    return list(seen.values()), duplicate_count


def run_acquisition(
    config: AcquisitionConfig,
    *,
    token: str,
    fetch: Any = fetch_search_page_with_retry,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Orchestrates fetch -> classify -> build -> dedup -> cap -> validate.
    `fetch`/`sleep` are injectable so this whole function is testable
    offline with a canned fetch stub and no real delay.
    """
    fetched_per_query: dict[str, int] = {}
    exclusion_counts: dict[str, int] = {}
    truncated_count = 0
    accepted: list[dict[str, Any]] = []

    for i, spec in enumerate(config.queries):
        query_id = f"{config.query_id_prefix}:{spec.id}"
        if i > 0:
            sleep(INTER_QUERY_SLEEP_SECONDS)
        try:
            payload = fetch(spec.q, token=token, per_page=PER_QUERY_FETCH_CAP)
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            print(f"query {spec.id!r} failed, skipping: {exc}", file=sys.stderr)
            fetched_per_query[spec.id] = 0
            continue
        items = payload.get("items", [])
        fetched_per_query[spec.id] = len(items)
        for item in items:
            reason = classify_item(item)
            if reason is not None:
                exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
                continue
            record, was_truncated = build_raw_record(
                item, query_id=query_id, body_truncate_chars=config.body_truncate_chars
            )
            if was_truncated:
                truncated_count += 1
            accepted.append(record)

    deduped, duplicate_count = deduplicate(accepted)
    deduped.sort(key=lambda r: str(r["source_id"]))  # final order: alphabetic by canonical identity

    over_cap_count = max(0, len(deduped) - config.max_records)
    capped = deduped[: config.max_records]

    validated: list[dict[str, Any]] = []
    schema_failures = 0
    for record in capped:
        try:
            RawEvidenceRecord.model_validate(record)
        except (
            Exception
        ) as exc:  # any validation failure excludes the record, never crashes the run
            schema_failures += 1
            print(
                f"record {record.get('source_id')} failed schema validation: {exc}", file=sys.stderr
            )
            continue
        validated.append(record)
    if schema_failures:
        exclusion_counts[SCHEMA_VALIDATION_FAILED] = schema_failures

    return {
        "validated": validated,
        "fetched_per_query": fetched_per_query,
        "exclusion_counts": exclusion_counts,
        "duplicate_count": duplicate_count,
        "over_cap_count": over_cap_count,
        "truncated_count": truncated_count,
    }


def write_outputs(result: dict[str, Any], config: AcquisitionConfig) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    validated: list[dict[str, Any]] = result["validated"]

    evidence_path = OUT_DIR / "evidence.jsonl"
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in validated]
    evidence_path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    evidence_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()

    unique_repos = sorted({r["source_id"].split(":", 1)[1].rsplit("#", 1)[0] for r in validated})
    unique_authors = sorted({r["author"] for r in validated})
    fetched_total = sum(result["fetched_per_query"].values())

    manifest = {
        "schema": "demand-radar.acquisition-manifest/1 (NOT a production schema)",
        "request_commit_sha": os.environ.get("REQUEST_COMMIT_SHA", "unknown"),
        "workflow_run_id": os.environ.get("WORKFLOW_RUN_ID", "unknown"),
        "product": config.product,
        "queries": [{"id": s.id, "q": s.q} for s in config.queries],
        "fetched_per_query": result["fetched_per_query"],
        "fetched_total": fetched_total,
        "excluded_counts": result["exclusion_counts"],
        "excluded_total": sum(result["exclusion_counts"].values()),
        "duplicate_count": result["duplicate_count"],
        "over_max_records_cap_count": result["over_cap_count"],
        "truncated_body_count": result["truncated_count"],
        "accepted_count": len(validated),
        "unique_repo_count": len(unique_repos),
        "unique_repos": unique_repos,
        "unique_author_count": len(unique_authors),
        "unique_authors": unique_authors,
        "collection_timestamp": datetime.now(UTC).isoformat(),
        "evidence_file_sha256": evidence_sha256,
        "max_records": config.max_records,
        "body_truncate_chars": config.body_truncate_chars,
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    summary = [
        f"# GitHub issues acquisition — {manifest['collection_timestamp']}",
        "",
        f"Product: `{config.product}` · Run: `{manifest['workflow_run_id']}` · "
        f"Request commit: `{manifest['request_commit_sha']}`",
        "",
        "## Queries",
        "",
        *[
            f"- `{s.id}`: `{s.q}` — fetched {result['fetched_per_query'].get(s.id, 0)}"
            for s in config.queries
        ],
        "",
        "## Counts",
        "",
        f"- fetched total: {fetched_total}",
        f"- excluded total: {manifest['excluded_total']} ({result['exclusion_counts']})",
        f"- duplicates (same issue, multiple queries): {result['duplicate_count']}",
        f"- over max_records cap ({config.max_records}): {result['over_cap_count']}",
        f"- truncated bodies (> {config.body_truncate_chars} chars): {result['truncated_count']}",
        f"- **accepted (in evidence.jsonl): {len(validated)}**",
        f"- unique repos: {len(unique_repos)}",
        f"- unique issue openers: {len(unique_authors)}",
        "",
        "## Files",
        "",
        "- `data/evidence.jsonl` — RawEvidenceRecord JSONL, ready for `demand-radar ingest`",
        "- `data/manifest.json` — full counts, exclusions, hashes",
        f"- evidence.jsonl sha256: `{evidence_sha256}`",
        "",
    ]
    (OUT_DIR / "acquisition-summary.md").write_text("\n".join(summary), encoding="utf-8")


def main() -> int:
    config = load_config()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "GITHUB_TOKEN not set -- refusing to call the Search API unauthenticated",
            file=sys.stderr,
        )
        return 1

    result = run_acquisition(config, token=token)
    write_outputs(result, config)

    print(f"fetched_total={sum(result['fetched_per_query'].values())}")
    print(f"excluded_total={sum(result['exclusion_counts'].values())} {result['exclusion_counts']}")
    print(f"duplicate_count={result['duplicate_count']}")
    print(f"accepted_count={len(result['validated'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
