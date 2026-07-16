"""One-off setup for the Phase 2C-SMOKE synthetic review pipeline test:
copies the completed, pre-review canonical run (DB, run_dir, review packet)
into an isolated runs/phase-2c-smoke/ tree, then fixes up only the run-owned
identifiers the review-fixture contract requires -- run_id in the copied
DB's runs/opportunity_cards/problem_clusters/classifications rows,
task.yaml, state.json, packet-manifest.json, and each review-template.json
-- and marks the copy test_fixture=true. Evidence ids, opportunity ids,
cluster ids, and every opportunity/evidence hash are left byte-identical:
none of those are embedded in models.py's schemas, only the run_id column/
field is. Never touches the canonical files -- shutil.copy2/copytree always
produce a separate file before anything is rewritten, and this script never
opens the canonical paths for writing.

Never re-runs acquisition, ingest, dedup, classify, clustering, or
opportunity generation -- the copy is exactly the prior run's completed
state, nothing regenerated.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from demand_radar.review import mark_run_as_test_fixture  # noqa: E402

PRODUCT = "own-audit"
CANONICAL_RUN_ID = "phase-2c-combined-001"
SMOKE_RUN_ID = "phase-2c-review-smoke-001"

CANONICAL_ROOT = REPO_ROOT / "runs" / "phase-2c"
CANONICAL_DB = CANONICAL_ROOT / "store.db"
CANONICAL_RUN_DIR = CANONICAL_ROOT / "runs" / PRODUCT / CANONICAL_RUN_ID
CANONICAL_PACKET = CANONICAL_ROOT / "review-packet"

SMOKE_ROOT = REPO_ROOT / "runs" / "phase-2c-smoke"
SMOKE_DB = SMOKE_ROOT / "store.db"
SMOKE_RUN_DIR = SMOKE_ROOT / "runs" / PRODUCT / SMOKE_RUN_ID
SMOKE_PACKET = SMOKE_ROOT / "review-packet"

_RUN_ID_TABLES = (
    "runs",
    "opportunity_cards",
    "problem_clusters",
    "classifications",
    "critic_verdicts",
)


def _rekey_run_id_in_db(db_path: Path) -> None:
    """opportunity_cards.id / problem_clusters.id / evidence_items.id are
    the real primary keys and are never touched -- only the run_id column
    that tags which run a row belongs to. evidence_items and
    duplicate_links have no run_id column at all (evidence is product-
    scoped, not run-scoped) and are correspondingly left alone.
    """
    conn = sqlite3.connect(db_path)
    try:
        for table in _RUN_ID_TABLES:
            # table is always one of the fixed names in _RUN_ID_TABLES above.
            cur = conn.execute(
                f"UPDATE {table} SET run_id = ? WHERE run_id = ?",
                (SMOKE_RUN_ID, CANONICAL_RUN_ID),
            )
            print(f"  {table}: {cur.rowcount} row(s) rekeyed")
        conn.commit()
    finally:
        conn.close()


def _rewrite_task_yaml(run_dir: Path) -> None:
    task_yaml_path = run_dir / "task.yaml"
    task = yaml.safe_load(task_yaml_path.read_text(encoding="utf-8"))
    task["run_id"] = SMOKE_RUN_ID
    task_yaml_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    mark_run_as_test_fixture(
        run_dir,
        kind="synthetic_review_pipeline_smoke",
        canonical_parent_run=CANONICAL_RUN_ID,
    )


def _rewrite_state_json(run_dir: Path) -> None:
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["run_id"] == CANONICAL_RUN_ID, state["run_id"]
    state["run_id"] = SMOKE_RUN_ID
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _rewrite_packet_manifest(packet_dir: Path) -> None:
    manifest_path = packet_dir / "packet-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == CANONICAL_RUN_ID, manifest["run_id"]
    manifest["run_id"] = SMOKE_RUN_ID
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for opp_id in manifest["opportunity_ids"]:
        template_path = packet_dir / opp_id / "review-template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        template["run_id"] = SMOKE_RUN_ID
        template_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if SMOKE_ROOT.exists():
        raise SystemExit(
            f"{SMOKE_ROOT} already exists -- remove it first if you intend to redo the smoke copy"
        )
    for path in (CANONICAL_DB, CANONICAL_RUN_DIR, CANONICAL_PACKET):
        if not path.exists():
            raise SystemExit(f"canonical path missing: {path}")

    SMOKE_ROOT.mkdir(parents=True)

    print(f"copying {CANONICAL_DB} -> {SMOKE_DB}")
    shutil.copy2(CANONICAL_DB, SMOKE_DB)
    print("rekeying run_id in the copy:")
    _rekey_run_id_in_db(SMOKE_DB)

    print(f"copying {CANONICAL_RUN_DIR} -> {SMOKE_RUN_DIR}")
    shutil.copytree(CANONICAL_RUN_DIR, SMOKE_RUN_DIR)
    _rewrite_task_yaml(SMOKE_RUN_DIR)
    _rewrite_state_json(SMOKE_RUN_DIR)

    print(f"copying {CANONICAL_PACKET} -> {SMOKE_PACKET}")
    shutil.copytree(CANONICAL_PACKET, SMOKE_PACKET)
    _rewrite_packet_manifest(SMOKE_PACKET)

    print(f"smoke copy ready: db={SMOKE_DB} run_dir={SMOKE_RUN_DIR} packet={SMOKE_PACKET}")
    print(f"smoke run_id={SMOKE_RUN_ID} (canonical_parent_run={CANONICAL_RUN_ID})")


if __name__ == "__main__":
    main()
