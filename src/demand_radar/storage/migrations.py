"""Versioned SQLite migrations. Applied in order, tracked in
schema_migrations, and safe to re-run: `run_migrations` only applies
versions not yet recorded.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS evidence_items (
            id TEXT PRIMARY KEY,
            product TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_provider TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_url TEXT,
            source_family TEXT NOT NULL,
            author_stable_hash TEXT NOT NULL,
            published_at TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            query_id TEXT NOT NULL,
            import_batch_id TEXT NOT NULL,
            data TEXT NOT NULL,
            UNIQUE (product, source_kind, source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_product ON evidence_items(product);
        CREATE INDEX IF NOT EXISTS idx_evidence_content_hash
            ON evidence_items(product, content_hash);
        CREATE INDEX IF NOT EXISTS idx_evidence_url ON evidence_items(product, source_url);

        CREATE TABLE IF NOT EXISTS duplicate_links (
            evidence_id TEXT PRIMARY KEY REFERENCES evidence_items(id),
            duplicate_group_id TEXT NOT NULL,
            canonical_evidence_id TEXT NOT NULL REFERENCES evidence_items(id),
            duplicate_reason TEXT NOT NULL,
            similarity_score REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_dup_group ON duplicate_links(duplicate_group_id);

        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            product TEXT NOT NULL,
            since TEXT,
            analyst TEXT NOT NULL,
            critic TEXT NOT NULL,
            status TEXT NOT NULL,
            verdict TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS classifications (
            evidence_id TEXT PRIMARY KEY REFERENCES evidence_items(id),
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            relevant INTEGER NOT NULL,
            problem_key TEXT,
            data TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_classifications_run ON classifications(run_id);

        CREATE TABLE IF NOT EXISTS problem_clusters (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            product TEXT NOT NULL,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cluster_members (
            cluster_id TEXT NOT NULL REFERENCES problem_clusters(id),
            evidence_id TEXT NOT NULL REFERENCES evidence_items(id),
            PRIMARY KEY (cluster_id, evidence_id)
        );

        CREATE TABLE IF NOT EXISTS opportunity_cards (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            product TEXT NOT NULL,
            status TEXT NOT NULL,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS opportunity_clusters (
            opportunity_id TEXT NOT NULL REFERENCES opportunity_cards(id),
            cluster_id TEXT NOT NULL REFERENCES problem_clusters(id),
            PRIMARY KEY (opportunity_id, cluster_id)
        );

        CREATE TABLE IF NOT EXISTS critic_verdicts (
            opportunity_id TEXT PRIMARY KEY REFERENCES opportunity_cards(id),
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            data TEXT NOT NULL
        );
        """,
    ),
]


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def run_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply any migration not yet recorded. Returns newly-applied versions."""
    _ensure_migrations_table(conn)
    already = applied_versions(conn)
    newly_applied = []
    for version, sql in MIGRATIONS:
        if version in already:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(UTC).isoformat()),
        )
        newly_applied.append(version)
    conn.commit()
    return newly_applied
