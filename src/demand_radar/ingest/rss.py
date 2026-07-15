"""RSS 2.0 / Atom ingestion. Stdlib only (urllib + xml.etree) — no crawler
framework, no feedparser dependency (spec section 5 forbids adding a
dependency without a demonstrated need, and stdlib covers both formats for
the well-formed-XML case an MVP targets). Feeds funnel through the same
RawEvidenceRecord -> build_evidence_item path as JSONL ingestion, so a
future collector needs only to produce that one shape (spec section 7). One
malformed entry is reported and skipped, exactly like a malformed JSONL line
— it never aborts the rest of the feed.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element

from pydantic import ValidationError

from demand_radar.ingest.jsonl import (
    IngestResult,
    LineError,
    RawEvidenceRecord,
    build_evidence_item,
)
from demand_radar.storage.sqlite import Store

USER_AGENT = "demand-radar/0.1 (+https://github.com/PhysShell/demand-radar)"
RSS_PROVIDER = "rss"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_local(elem: Element, name: str) -> Element | None:
    for child in elem:
        if _localname(child.tag) == name:
            return child
    return None


def _findall_local(elem: Element, name: str) -> Sequence[Element]:
    return [child for child in elem if _localname(child.tag) == name]


def _text_of(elem: Element | None) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def _parse_date(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    raw = raw.strip()
    try:
        parsed = parsedate_to_datetime(raw)  # RFC 822 - RSS pubDate
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))  # ISO 8601 - Atom
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return datetime.now(UTC)


def fetch_feed_bytes(url: str, *, timeout: float = 15.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read()
        return data


def parse_feed(
    content: bytes, *, query_id: str, source_family: str = RSS_PROVIDER
) -> tuple[list[RawEvidenceRecord], list[LineError]]:
    root = ET.fromstring(content)
    root_name = _localname(root.tag)
    if root_name == "feed":
        return _parse_atom(root, query_id=query_id, source_family=source_family)
    if root_name == "rss":
        return _parse_rss2(root, query_id=query_id, source_family=source_family)
    raise ValueError(f"unrecognized feed root element: <{root.tag}>")


def _build_record(
    *,
    source_id: str,
    source_family: str,
    url: str | None,
    author: str | None,
    published_at: datetime,
    text: str,
    query_id: str,
) -> RawEvidenceRecord:
    return RawEvidenceRecord(
        source_id=source_id,
        source_kind="rss_entry",
        source_family=source_family,
        url=url,
        author=author,
        published_at=published_at,
        text=text,
        query_id=query_id,
    )


def _parse_rss2(
    root: Element, *, query_id: str, source_family: str
) -> tuple[list[RawEvidenceRecord], list[LineError]]:
    channel = _find_local(root, "channel")
    records: list[RawEvidenceRecord] = []
    errors: list[LineError] = []
    if channel is None:
        return records, errors
    for position, item in enumerate(_findall_local(channel, "item"), start=1):
        title = _text_of(_find_local(item, "title"))
        link = _text_of(_find_local(item, "link")) or None
        description = _text_of(_find_local(item, "description"))
        guid = _text_of(_find_local(item, "guid")) or link or title
        published_at = _parse_date(_text_of(_find_local(item, "pubDate")))
        author = _text_of(_find_local(item, "creator") or _find_local(item, "author")) or None
        text = f"{title}\n\n{description}".strip() if description else title
        if not guid or not text:
            errors.append(
                LineError(position, "rss item missing guid/link and title/description", guid or "")
            )
            continue
        try:
            records.append(
                _build_record(
                    source_id=guid,
                    source_family=source_family,
                    url=link,
                    author=author,
                    published_at=published_at,
                    text=text,
                    query_id=query_id,
                )
            )
        except ValidationError as exc:
            errors.append(LineError(position, f"schema validation failed: {exc.errors()!r}", guid))
    return records, errors


def _parse_atom(
    root: Element, *, query_id: str, source_family: str
) -> tuple[list[RawEvidenceRecord], list[LineError]]:
    records: list[RawEvidenceRecord] = []
    errors: list[LineError] = []
    for position, entry in enumerate(_findall_local(root, "entry"), start=1):
        title = _text_of(_find_local(entry, "title"))
        link: str | None = None
        for link_elem in _findall_local(entry, "link"):
            href = link_elem.attrib.get("href")
            if not href:
                continue
            rel = link_elem.attrib.get("rel", "alternate")
            link = href
            if rel == "alternate":
                break
        summary = _text_of(_find_local(entry, "summary")) or _text_of(_find_local(entry, "content"))
        entry_id = _text_of(_find_local(entry, "id")) or link or title
        published_at = _parse_date(
            _text_of(_find_local(entry, "published") or _find_local(entry, "updated"))
        )
        author_elem = _find_local(entry, "author")
        author_name = (
            _text_of(_find_local(author_elem, "name")) if author_elem is not None else None
        )
        text = f"{title}\n\n{summary}".strip() if summary else title
        if not entry_id or not text:
            errors.append(
                LineError(position, "atom entry missing id/link and title/summary", entry_id or "")
            )
            continue
        try:
            records.append(
                _build_record(
                    source_id=entry_id,
                    source_family=source_family,
                    url=link,
                    author=author_name or None,
                    published_at=published_at,
                    text=text,
                    query_id=query_id,
                )
            )
        except ValidationError as exc:
            errors.append(
                LineError(position, f"schema validation failed: {exc.errors()!r}", entry_id)
            )
    return records, errors


def ingest_rss_feed(
    url: str,
    *,
    product: str,
    store: Store,
    query_id: str,
    source_family: str = RSS_PROVIDER,
    collector_version: str = "rss-ingest/0.1.0",
    import_batch_id: str | None = None,
    _content_override: bytes | None = None,
) -> IngestResult:
    """_content_override lets tests feed a canned feed body without a
    network call; the CLI path always fetches live."""
    content = _content_override if _content_override is not None else fetch_feed_bytes(url)
    batch_id = import_batch_id or f"batch-rss-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    collected_at = datetime.now(UTC)

    result = IngestResult()
    try:
        raw_records, parse_errors = parse_feed(
            content, query_id=query_id, source_family=source_family
        )
    except ET.ParseError as exc:
        result.errors.append(LineError(0, f"feed XML did not parse: {exc}", raw_line=""))
        return result
    result.errors.extend(parse_errors)

    for raw in raw_records:
        item = build_evidence_item(
            raw,
            product=product,
            provider=RSS_PROVIDER,
            collector_version=collector_version,
            import_batch_id=batch_id,
            collected_at=collected_at,
        )
        result.accepted.append(item)
        if store.insert_evidence_item(item):
            result.inserted_ids.append(item.id)
        else:
            result.already_present_ids.append(item.id)

    return result
