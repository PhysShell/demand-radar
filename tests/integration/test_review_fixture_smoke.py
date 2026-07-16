"""Integration tests for the Phase 2C-SMOKE synthetic review pipeline test:
generate-fixtures -> import --allow-test-fixture -> finalize, run through
the real pipeline (reusing test_review_workflow.py's
run_human_review_pipeline, not mocked), proving the mechanics work and
that no card can reach experiment_ready/externally_validated from a
fixture review. See tests/unit/test_review_fixtures.py for the offline
schema/validation-only tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from demand_radar.config import load_product_config_by_name
from demand_radar.review import (
    export_review_packet,
    finalize_run,
    generate_fixtures,
    import_reviews,
    mark_run_as_test_fixture,
)
from demand_radar.storage.sqlite import Store
from tests.integration.test_review_workflow import PRODUCT, REPO_ROOT, run_human_review_pipeline


def _prepare_fixture_run(
    tmp_path: Path, *, run_id: str = "run-fixture-smoke", packet_dir_name: str = "packet"
) -> tuple[Store, Path, dict, Path]:
    """A completed --critic human run, marked test_fixture=true and with a
    review packet exported -- the state Phase 2C-SMOKE's real copy-based
    setup produces, minus the copy step itself (nothing here is a
    canonical asset at risk, so there is nothing to protect by copying in
    a test)."""
    store, run_dir, final_state = run_human_review_pipeline(tmp_path, run_id=run_id)
    mark_run_as_test_fixture(
        run_dir,
        kind="synthetic_review_pipeline_smoke",
        canonical_parent_run="canonical-example-001",
    )
    output_dir = tmp_path / packet_dir_name
    export_review_packet(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        product=PRODUCT,
        output_dir=output_dir,
        generated_at=datetime.now(UTC),
    )
    return store, run_dir, final_state, output_dir


def _generate_and_import_all(
    store: Store, run_dir: Path, run_id: str, packet_dir: Path, tmp_path: Path
):
    fixtures_path = tmp_path / "fixtures.jsonl"
    generated = generate_fixtures(
        run_id=run_id,
        packet_dir=packet_dir,
        output_path=fixtures_path,
        generated_at=datetime.now(UTC),
    )
    result = import_reviews(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        input_path=fixtures_path,
        imported_at=datetime.now(UTC),
        allow_test_fixture=True,
    )
    return generated, result


def test_complete_fixture_set_reaches_mechanics_complete(tmp_path: Path) -> None:
    store, run_dir, final_state, packet_dir = _prepare_fixture_run(tmp_path)
    run_id = final_state["run_id"]

    generated, result = _generate_and_import_all(store, run_dir, run_id, packet_dir, tmp_path)

    assert len(generated.fixtures) == 4
    assert result.contains_test_fixture is True
    assert set(result.imported_opportunity_ids) == {f.opportunity_id for f in generated.fixtures}
    for f in generated.fixtures:
        assert store.get_critic_verdict(f.opportunity_id) is not None

    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    finalize_run(
        store=store,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        product=product_config,
        run_id=run_id,
        now=datetime.now(UTC),
    )

    verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
    assert verification["checks"]["critic_status"] == "PASS"

    marker_path = run_dir / "reviews" / "review-channel-status.json"
    assert marker_path.is_file()
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker == {
        "review_channel_status": "TEST_FIXTURE_REVIEW",
        "pipeline_mechanics": "PASS",
        "substantive_review": "NOT_PERFORMED",
    }
    store.close()


def test_partial_fixture_set_stays_blocked(tmp_path: Path) -> None:
    store, run_dir, final_state, packet_dir = _prepare_fixture_run(tmp_path)
    run_id = final_state["run_id"]

    fixtures_path = tmp_path / "fixtures.jsonl"
    generated = generate_fixtures(
        run_id=run_id,
        packet_dir=packet_dir,
        output_path=fixtures_path,
        generated_at=datetime.now(UTC),
    )
    # Import only the first fixture, not the full set.
    only_one = generated.fixtures[0]
    partial_path = tmp_path / "partial.jsonl"
    partial_path.write_text(
        only_one.model_dump_json(by_alias=True, exclude_none=True) + "\n", encoding="utf-8"
    )
    import_reviews(
        store=store,
        run_dir=run_dir,
        run_id=run_id,
        input_path=partial_path,
        imported_at=datetime.now(UTC),
        allow_test_fixture=True,
    )

    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    final = finalize_run(
        store=store,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        product=product_config,
        run_id=run_id,
        now=datetime.now(UTC),
    )

    verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
    assert verification["checks"]["critic_status"] != "PASS"
    assert final["verdict"] == "BLOCKED"
    # Not a complete fixture review channel -- no TEST_FIXTURE_REVIEW marker.
    assert not (run_dir / "reviews" / "review-channel-status.json").exists()
    store.close()


def test_finalize_calls_no_agent_for_fixture_reviewed_run(tmp_path: Path) -> None:
    """Would raise AssertionError from NeverCalledRunner if finalize_run
    ever called an agent -- same structural proof as the human-review path,
    now exercised via a fixture-only critic channel."""
    store, run_dir, final_state, packet_dir = _prepare_fixture_run(tmp_path)
    run_id = final_state["run_id"]
    _generate_and_import_all(store, run_dir, run_id, packet_dir, tmp_path)

    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    final = finalize_run(  # would raise AssertionError if it called an agent
        store=store,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        product=product_config,
        run_id=run_id,
        now=datetime.now(UTC),
    )
    assert final["verdict"] in ("PASS", "FAIL", "BLOCKED")  # completed at all, not raised
    store.close()


def test_fixture_finalize_never_produces_experiment_ready_or_externally_validated(
    tmp_path: Path,
) -> None:
    store, run_dir, final_state, packet_dir = _prepare_fixture_run(tmp_path)
    run_id = final_state["run_id"]
    _generate_and_import_all(store, run_dir, run_id, packet_dir, tmp_path)

    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    finalize_run(
        store=store,
        run_dir=run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        product=product_config,
        run_id=run_id,
        now=datetime.now(UTC),
    )

    cards = store.list_opportunity_cards_for_run(run_id)
    assert len(cards) == 4
    statuses = {card.status for card in cards}
    assert "experiment_ready" not in statuses
    assert "externally_validated" not in statuses
    assert statuses <= {"investigate", "rejected"}
    store.close()


def test_fixture_pipeline_never_touches_a_separate_run(tmp_path: Path) -> None:
    """A second, independent run/db/packet (standing in for a canonical run
    in this test) sits untouched while the fixture pipeline runs entirely
    against a different run/db/packet -- the same file-level isolation
    Phase 2C-SMOKE's real copy-based setup relies on."""
    import hashlib

    other_store, other_run_dir, other_final_state = run_human_review_pipeline(
        tmp_path / "other", run_id="run-other"
    )
    other_store.close()
    other_db_path = tmp_path / "other" / "demand.db"

    def _hash_tree(path: Path) -> dict[str, str]:
        return {
            str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*"))
            if p.is_file()
        }

    before_db_hash = hashlib.sha256(other_db_path.read_bytes()).hexdigest()
    before_run_dir_hashes = _hash_tree(other_run_dir)

    smoke_store, smoke_run_dir, smoke_final_state, packet_dir = _prepare_fixture_run(
        tmp_path / "smoke", run_id="run-smoke"
    )
    run_id = smoke_final_state["run_id"]
    _generate_and_import_all(smoke_store, smoke_run_dir, run_id, packet_dir, tmp_path / "smoke")
    product_config = load_product_config_by_name(PRODUCT, products_dir=REPO_ROOT / "products")
    finalize_run(
        store=smoke_store,
        run_dir=smoke_run_dir,
        schemas_dir=REPO_ROOT / "schemas",
        product=product_config,
        run_id=run_id,
        now=datetime.now(UTC),
    )
    smoke_store.close()

    after_db_hash = hashlib.sha256(other_db_path.read_bytes()).hexdigest()
    after_run_dir_hashes = _hash_tree(other_run_dir)
    assert after_db_hash == before_db_hash
    assert after_run_dir_hashes == before_run_dir_hashes
