"""The evidence store. One SQLite file per product database; every stored
row keeps its full canonical JSON (`data` column) plus the handful of
columns needed to query/join/dedupe. The JSON is the source of truth — the
flat columns exist only for indexing.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from demand_radar.models import (
    Classification,
    CriticVerdict,
    DuplicateLink,
    EvidenceItem,
    OpportunityCard,
    ProblemCluster,
)
from demand_radar.storage.migrations import applied_versions, run_migrations


class DatabaseNotInitializedError(RuntimeError):
    pass


def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(UTC).isoformat()


class Store:
    """Not thread-safe; one Store per process/CLI invocation."""

    def __init__(self, db_path: Path | str, *, create: bool = False) -> None:
        db_path = Path(db_path)
        self.db_path = db_path
        if create:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        elif not db_path.exists():
            raise DatabaseNotInitializedError(
                f"database not found at {db_path} — run `demand-radar init --db {db_path}` first"
            )
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if create:
            run_migrations(self.conn)
        elif not applied_versions(self.conn):
            raise DatabaseNotInitializedError(
                f"database at {db_path} exists but has no migrations applied — "
                f"it is not a demand-radar database"
            )

    @classmethod
    def init(cls, db_path: Path | str) -> Store:
        return cls(db_path, create=True)

    @classmethod
    def open(cls, db_path: Path | str) -> Store:
        return cls(db_path, create=False)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- Evidence -----------------------------------------------------

    def insert_evidence_item(self, item: EvidenceItem) -> bool:
        """Returns True if this row is new, False if it already existed
        (idempotent re-import — spec section 10.1 / acceptance 23.1)."""
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO evidence_items
            (id, product, source_kind, source_provider, source_id, source_url, source_family,
             author_stable_hash, published_at, collected_at, content_hash, query_id,
             import_batch_id, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.product,
                item.source.kind,
                item.source.provider,
                item.source.source_id,
                item.source.url,
                item.source.source_family,
                item.author.stable_hash,
                _to_utc_iso(item.timestamps.published_at),
                _to_utc_iso(item.timestamps.collected_at),
                item.content.content_hash,
                item.collection.query_id,
                item.collection.import_batch_id,
                item.model_dump_json(by_alias=True, exclude_none=True),
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_evidence_item(self, evidence_id: str) -> EvidenceItem | None:
        row = self.conn.execute(
            "SELECT data FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()
        return EvidenceItem.model_validate_json(row["data"]) if row else None

    def evidence_exists(self, evidence_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()
        return row is not None

    def list_evidence_items(
        self, product: str, since: datetime | None = None
    ) -> list[EvidenceItem]:
        if since is not None:
            rows = self.conn.execute(
                "SELECT data FROM evidence_items WHERE product = ? AND published_at >= ? "
                "ORDER BY published_at ASC",
                (product, _to_utc_iso(since)),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT data FROM evidence_items WHERE product = ? ORDER BY published_at ASC",
                (product,),
            ).fetchall()
        return [EvidenceItem.model_validate_json(r["data"]) for r in rows]

    def count_evidence(self, product: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_items WHERE product = ?", (product,)
        ).fetchone()
        return int(row["c"])

    def find_evidence_by_content_hash(self, product: str, content_hash: str) -> list[EvidenceItem]:
        rows = self.conn.execute(
            "SELECT data FROM evidence_items WHERE product = ? AND content_hash = ? "
            "ORDER BY published_at ASC",
            (product, content_hash),
        ).fetchall()
        return [EvidenceItem.model_validate_json(r["data"]) for r in rows]

    def find_evidence_by_url(self, product: str, url: str) -> list[EvidenceItem]:
        rows = self.conn.execute(
            "SELECT data FROM evidence_items WHERE product = ? AND source_url = ? "
            "ORDER BY published_at ASC",
            (product, url),
        ).fetchall()
        return [EvidenceItem.model_validate_json(r["data"]) for r in rows]

    # -- Duplicate links ------------------------------------------------

    def upsert_duplicate_link(self, link: DuplicateLink) -> None:
        self.conn.execute(
            """
            INSERT INTO duplicate_links
                (evidence_id, duplicate_group_id, canonical_evidence_id, duplicate_reason,
                 similarity_score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                duplicate_group_id = excluded.duplicate_group_id,
                canonical_evidence_id = excluded.canonical_evidence_id,
                duplicate_reason = excluded.duplicate_reason,
                similarity_score = excluded.similarity_score
            """,
            (
                link.evidence_id,
                link.duplicate_group_id,
                link.canonical_evidence_id,
                link.duplicate_reason,
                link.similarity_score,
            ),
        )
        self.conn.commit()

    def get_duplicate_link(self, evidence_id: str) -> DuplicateLink | None:
        row = self.conn.execute(
            "SELECT * FROM duplicate_links WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        return _row_to_duplicate_link(row) if row else None

    def list_duplicate_links(self, product: str) -> list[DuplicateLink]:
        rows = self.conn.execute(
            """
            SELECT dl.* FROM duplicate_links dl
            JOIN evidence_items e ON e.id = dl.evidence_id
            WHERE e.product = ?
            """,
            (product,),
        ).fetchall()
        return [_row_to_duplicate_link(r) for r in rows]

    # -- Runs -------------------------------------------------------------

    def create_run(
        self, run_id: str, product: str, since: str | None, analyst: str, critic: str
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO runs
                (run_id, product, since, analyst, critic, status, verdict, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'running', NULL, ?, ?)
            """,
            (run_id, product, since, analyst, critic, now, now),
        )
        self.conn.commit()

    def update_run_status(self, run_id: str, status: str, verdict: str | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET status = ?, verdict = COALESCE(?, verdict), updated_at = ? "
            "WHERE run_id = ?",
            (status, verdict, datetime.now(UTC).isoformat(), run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, object] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def run_exists(self, run_id: str) -> bool:
        return self.get_run(run_id) is not None

    # -- Classifications ---------------------------------------------------

    def upsert_classification(self, run_id: str, classification: Classification) -> None:
        self.conn.execute(
            """
            INSERT INTO classifications (evidence_id, run_id, relevant, problem_key, data)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                run_id = excluded.run_id,
                relevant = excluded.relevant,
                problem_key = excluded.problem_key,
                data = excluded.data
            """,
            (
                classification.evidence_id,
                run_id,
                int(classification.relevance.relevant),
                classification.problem.problem_key,
                classification.model_dump_json(by_alias=True, exclude_none=True),
            ),
        )
        self.conn.commit()

    def get_classification(self, evidence_id: str) -> Classification | None:
        row = self.conn.execute(
            "SELECT data FROM classifications WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        return Classification.model_validate_json(row["data"]) if row else None

    def list_classifications_for_run(self, run_id: str) -> list[Classification]:
        rows = self.conn.execute(
            "SELECT data FROM classifications WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [Classification.model_validate_json(r["data"]) for r in rows]

    # -- Problem clusters -----------------------------------------------

    def upsert_problem_cluster(self, run_id: str, cluster: ProblemCluster) -> None:
        self.conn.execute(
            """
            INSERT INTO problem_clusters (id, run_id, product, data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET data = excluded.data
            """,
            (
                cluster.id,
                run_id,
                cluster.product,
                cluster.model_dump_json(by_alias=True, exclude_none=True),
            ),
        )
        self.conn.execute("DELETE FROM cluster_members WHERE cluster_id = ?", (cluster.id,))
        self.conn.executemany(
            "INSERT INTO cluster_members (cluster_id, evidence_id) VALUES (?, ?)",
            [(cluster.id, eid) for eid in cluster.member_evidence_ids],
        )
        self.conn.commit()

    def get_problem_cluster(self, cluster_id: str) -> ProblemCluster | None:
        row = self.conn.execute(
            "SELECT data FROM problem_clusters WHERE id = ?", (cluster_id,)
        ).fetchone()
        return ProblemCluster.model_validate_json(row["data"]) if row else None

    def list_problem_clusters_for_run(self, run_id: str) -> list[ProblemCluster]:
        rows = self.conn.execute(
            "SELECT data FROM problem_clusters WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [ProblemCluster.model_validate_json(r["data"]) for r in rows]

    # -- Opportunity cards ------------------------------------------------

    def upsert_opportunity_card(self, run_id: str, card: OpportunityCard) -> None:
        self.conn.execute(
            """
            INSERT INTO opportunity_cards (id, run_id, product, status, data)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data
            """,
            (
                card.id,
                run_id,
                card.product,
                card.status,
                card.model_dump_json(by_alias=True, exclude_none=True),
            ),
        )
        self.conn.execute("DELETE FROM opportunity_clusters WHERE opportunity_id = ?", (card.id,))
        self.conn.executemany(
            "INSERT INTO opportunity_clusters (opportunity_id, cluster_id) VALUES (?, ?)",
            [(card.id, cid) for cid in card.cluster_ids],
        )
        self.conn.commit()

    def get_opportunity_card(self, opportunity_id: str) -> OpportunityCard | None:
        row = self.conn.execute(
            "SELECT data FROM opportunity_cards WHERE id = ?", (opportunity_id,)
        ).fetchone()
        return OpportunityCard.model_validate_json(row["data"]) if row else None

    def list_opportunity_cards_for_run(self, run_id: str) -> list[OpportunityCard]:
        rows = self.conn.execute(
            "SELECT data FROM opportunity_cards WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [OpportunityCard.model_validate_json(r["data"]) for r in rows]

    # -- Critic verdicts --------------------------------------------------

    def upsert_critic_verdict(self, run_id: str, verdict: CriticVerdict) -> None:
        self.conn.execute(
            """
            INSERT INTO critic_verdicts (opportunity_id, run_id, data)
            VALUES (?, ?, ?)
            ON CONFLICT(opportunity_id) DO UPDATE SET data = excluded.data
            """,
            (
                verdict.opportunity_id,
                run_id,
                verdict.model_dump_json(by_alias=True, exclude_none=True),
            ),
        )
        self.conn.commit()

    def get_critic_verdict(self, opportunity_id: str) -> CriticVerdict | None:
        row = self.conn.execute(
            "SELECT data FROM critic_verdicts WHERE opportunity_id = ?", (opportunity_id,)
        ).fetchone()
        return CriticVerdict.model_validate_json(row["data"]) if row else None


def _row_to_duplicate_link(row: sqlite3.Row) -> DuplicateLink:
    return DuplicateLink(
        evidence_id=row["evidence_id"],
        duplicate_group_id=row["duplicate_group_id"],
        canonical_evidence_id=row["canonical_evidence_id"],
        duplicate_reason=row["duplicate_reason"],
        similarity_score=row["similarity_score"],
    )
