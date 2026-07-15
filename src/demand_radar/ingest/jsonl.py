"""Generic JSONL ingestion. Zone 1 per docs/trust-boundaries.md: accepts
external content, runs no agent, executes no shell. One bad line reports an
error and is skipped; it never aborts the batch or corrupts other rows.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

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
from demand_radar.storage.sqlite import Store

JSONL_PROVIDER = "imported-jsonl"


class RawEvidenceRecord(BaseModel):
    """The one generic shape every collector (JSONL export, RSS, future
    adapters) must produce. Everything derived (id, hashes, stable author
    hash) is computed from this by `build_evidence_item`, never trusted from
    the input as-is.
    """

    source_id: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    source_family: str = Field(min_length=1)
    url: str | None = None
    author: str | None = None
    display_name: str | None = None
    published_at: datetime
    text: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    language: str = "en"


@dataclass
class LineError:
    line_number: int
    message: str
    raw_line: str


@dataclass
class IngestResult:
    accepted: list[EvidenceItem] = field(default_factory=list)
    inserted_ids: list[str] = field(default_factory=list)
    already_present_ids: list[str] = field(default_factory=list)
    errors: list[LineError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def build_evidence_item(
    raw: RawEvidenceRecord,
    *,
    product: str,
    provider: str,
    collector_version: str,
    import_batch_id: str,
    collected_at: datetime,
) -> EvidenceItem:
    normalized = normalize_text(raw.text)
    evidence_id = evidence_id_for(product, raw.source_kind, raw.source_id)
    return EvidenceItem(
        id=evidence_id,
        product=product,
        source=EvidenceSource(
            kind=raw.source_kind,
            provider=provider,
            source_id=raw.source_id,
            url=canonical_url(raw.url) if raw.url else None,
            source_family=raw.source_family,
        ),
        author=EvidenceAuthor(
            stable_hash=stable_author_hash(raw.author, fallback_seed=f"{product}:{raw.source_id}"),
            display_name=raw.display_name,
        ),
        timestamps=EvidenceTimestamps(published_at=raw.published_at, collected_at=collected_at),
        content=EvidenceContent(
            raw_text=raw.text,
            normalized_text=normalized,
            language=raw.language,
            content_hash=content_hash(normalized),
        ),
        collection=EvidenceCollection(
            query_id=raw.query_id,
            collector_version=collector_version,
            import_batch_id=import_batch_id,
        ),
        trust=EvidenceTrust(),
    )


def iter_jsonl_lines(path: Path) -> Iterator[tuple[int, str]]:
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            yield line_number, stripped


def parse_jsonl_records(path: Path) -> tuple[list[tuple[int, RawEvidenceRecord]], list[LineError]]:
    """Pure parse step: no store, no side effects. Split out for testability."""
    records: list[tuple[int, RawEvidenceRecord]] = []
    errors: list[LineError] = []
    for line_number, raw_line in iter_jsonl_lines(path):
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            errors.append(LineError(line_number, f"invalid JSON: {exc}", raw_line))
            continue
        try:
            record = RawEvidenceRecord.model_validate(payload)
        except ValidationError as exc:
            errors.append(
                LineError(line_number, f"schema validation failed: {exc.errors()!r}", raw_line)
            )
            continue
        records.append((line_number, record))
    return records, errors


def ingest_jsonl_file(
    path: Path,
    *,
    product: str,
    store: Store,
    collector_version: str = "jsonl-ingest/0.1.0",
    import_batch_id: str | None = None,
) -> IngestResult:
    if not path.is_file():
        raise FileNotFoundError(f"input file not found: {path}")

    batch_id = import_batch_id or f"batch-{path.stem}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    collected_at = datetime.now(UTC)

    records, errors = parse_jsonl_records(path)
    result = IngestResult(errors=errors)

    for _line_number, raw in records:
        item = build_evidence_item(
            raw,
            product=product,
            provider=JSONL_PROVIDER,
            collector_version=collector_version,
            import_batch_id=batch_id,
            collected_at=collected_at,
        )
        result.accepted.append(item)
        inserted = store.insert_evidence_item(item)
        if inserted:
            result.inserted_ids.append(item.id)
        else:
            result.already_present_ids.append(item.id)

    return result
