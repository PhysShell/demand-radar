"""load_run, load_new_evidence, normalize, deduplicate — all deterministic,
no agent calls, no LLM. Spec Step 3: this half of the pipeline is fully
testable without any model.
"""

from __future__ import annotations

from typing import Any

from demand_radar.graph.state import DemandState, error, get_ctx
from demand_radar.ingest.deduplicate import canonical_evidence_ids, deduplicate_evidence
from demand_radar.ingest.normalize import normalize_text
from demand_radar.models import EvidenceItem
from demand_radar.timeutil import parse_since


def load_run(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    if not ctx.store.run_exists(state["run_id"]):
        raise RuntimeError(
            f"run {state['run_id']} not found in store -- "
            f"the CLI must create it before invoking the graph"
        )
    return {}


def load_new_evidence(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    # .get(), not state["since"]: a LastValue channel whose persisted value is
    # exactly None deserializes as *unset* on resume (langgraph's
    # LastValue.from_checkpoint treats a None checkpoint as "no value"), so a
    # bare subscript would KeyError here after any resume for an all-time run.
    since = state.get("since")
    since_dt = parse_since(since, ctx.now) if since else None
    items = ctx.store.list_evidence_items(state["product"], since=since_dt)
    return {"input_evidence_ids": [item.id for item in items]}


def normalize(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    """normalize_text already ran once at ingest time (ingest/jsonl.py,
    ingest/rss.py both call it immediately, since it needs no agent). This
    node is a deterministic consistency check, not a second normalization
    pass: it re-derives normalized_text/content_hash from the stored
    raw_text and flags drift (e.g. a future change to normalize_text that
    silently invalidates already-stored hashes) as an error rather than
    silently trusting stale data.
    """
    ctx = get_ctx(config)
    errors = []
    for eid in state["input_evidence_ids"]:
        item = ctx.store.get_evidence_item(eid)
        if item is None:
            errors.append(error("normalize", "evidence_id vanished from store", eid))
            continue
        recomputed = normalize_text(item.content.raw_text)
        if recomputed != item.content.normalized_text:
            errors.append(
                error("normalize", "stored normalized_text does not match recomputation", eid)
            )
    return {"errors": errors}


def deduplicate(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    maybe_items = [ctx.store.get_evidence_item(eid) for eid in state["input_evidence_ids"]]
    items: list[EvidenceItem] = [it for it in maybe_items if it is not None]

    links = deduplicate_evidence(
        items,
        similarity_threshold=ctx.product.deduplication.similarity_threshold,
        cross_repo_similarity_threshold=ctx.product.deduplication.cross_repo_similarity_threshold,
    )
    for link in links:
        ctx.store.upsert_duplicate_link(link)

    all_ids = [it.id for it in items]
    accepted = canonical_evidence_ids(all_ids, links)
    duplicate_ids = sorted({link.evidence_id for link in links})
    return {"accepted_evidence_ids": accepted, "duplicate_evidence_ids": duplicate_ids}
