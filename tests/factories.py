"""Shared test-data builders. Not a test module itself (no test_ prefix)."""

from __future__ import annotations

from datetime import datetime

from demand_radar.ingest.normalize import (
    canonical_url,
    content_hash,
    evidence_id_for,
    normalize_text,
    stable_author_hash,
)
from demand_radar.models import (
    EvidenceAuthor,
    EvidenceCollection,
    EvidenceContent,
    EvidenceItem,
    EvidenceSource,
    EvidenceTimestamps,
    EvidenceTrust,
)


def make_evidence_item(
    *,
    product: str = "own-audit",
    source_kind: str = "reddit_post",
    source_id: str,
    source_family: str = "reddit",
    url: str | None = None,
    author: str | None = "author-1",
    published_at: datetime,
    raw_text: str,
    query_id: str = "q1",
    collector_version: str = "test/0.1.0",
    import_batch_id: str = "test-batch",
) -> EvidenceItem:
    normalized = normalize_text(raw_text)
    return EvidenceItem(
        id=evidence_id_for(product, source_kind, source_id),
        product=product,
        source=EvidenceSource(
            kind=source_kind,
            provider="test",
            source_id=source_id,
            # canonicalized here to match what build_evidence_item does at real
            # ingest time (ingest/jsonl.py) -- deduplicate_evidence assumes its
            # input URLs are already canonical and never re-canonicalizes.
            url=canonical_url(url) if url else None,
            source_family=source_family,
        ),
        author=EvidenceAuthor(stable_hash=stable_author_hash(author, fallback_seed=source_id)),
        timestamps=EvidenceTimestamps(published_at=published_at, collected_at=published_at),
        content=EvidenceContent(
            raw_text=raw_text,
            normalized_text=normalized,
            language="en",
            content_hash=content_hash(normalized),
        ),
        collection=EvidenceCollection(
            query_id=query_id, collector_version=collector_version, import_batch_id=import_batch_id
        ),
        trust=EvidenceTrust(),
    )
