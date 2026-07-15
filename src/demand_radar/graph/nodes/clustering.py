"""propose_clusters, verify_clusters, score_clusters.

Order matches spec section 19's preferred pipeline exactly: deterministic
candidate grouping (by exact problem_key) -> agent-assisted naming/merge
proposal -> deterministic membership validation. The agent call is
optional in the sense that its failure has a fully deterministic fallback
(one cluster per problem_key) -- clustering never blocks on the model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from demand_radar.agents.base import (
    READ_ONLY_DATA_PROFILE,
    call_agent_with_retry,
    persist_call_artifacts,
)
from demand_radar.agents.prompts.cluster_naming import (
    CLUSTER_PROPOSAL_SCHEMA,
    build_cluster_naming_prompt,
)
from demand_radar.graph.state import DemandState, error, get_ctx
from demand_radar.models import Classification, ClusterIndependence, ClusterSignals, ProblemCluster
from demand_radar.scoring.independence import (
    compute_independence,
    gather_independence_inputs,
)

# frequency/saturation are both normalized against a scale constant rather
# than an unbounded raw count -- see docs/scoring.md.
FREQUENCY_CAP = 10
SATURATION_TOOL_CAP = 5


def _cluster_id_for(problem_keys: list[str]) -> str:
    digest = hashlib.sha256(":".join(sorted(problem_keys)).encode("utf-8")).hexdigest()
    return f"cluster_{digest[:12]}"


def _slug_to_label(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.split("-"))


def propose_clusters(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    errors: list[dict[str, Any]] = []

    classifications: list[Classification] = [
        c
        for eid in state["classifications"]
        if (c := ctx.store.get_classification(eid)) is not None and c.relevance.relevant
    ]
    candidate_groups: dict[str, list[str]] = {}
    for c in classifications:
        candidate_groups.setdefault(c.problem.problem_key, []).append(c.evidence_id)

    if not candidate_groups:
        return {"cluster_ids": [], "errors": errors}

    representative_statements = {
        key: [
            cc.problem.statement
            for eid in eids
            if (cc := ctx.store.get_classification(eid)) is not None
        ]
        for key, eids in candidate_groups.items()
    }

    grouped_keys: list[list[str]] = [[k] for k in candidate_groups]  # deterministic fallback
    labels: dict[str, str] = {k: _slug_to_label(k) for k in candidate_groups}
    canonical_problems: dict[str, str] = {
        k: (representative_statements[k][0] if representative_statements[k] else _slug_to_label(k))
        for k in candidate_groups
    }

    schema_path = ctx.run_dir / "schemas" / "cluster-proposal.schema.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(json.dumps(CLUSTER_PROPOSAL_SCHEMA, indent=2))
    prompt = build_cluster_naming_prompt(representative_statements)
    task_id = f"propose_clusters:{state['run_id']}"
    call_dir = ctx.run_dir / "agents" / "analyst" / "propose_clusters"
    result = call_agent_with_retry(
        ctx.analyst_runner,
        task_id=task_id,
        prompt=prompt,
        input_paths=[],
        output_schema=schema_path,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=call_dir,
        max_retries=ctx.max_agent_retries,
    )
    persist_call_artifacts(call_dir, task_id, prompt, result)

    if result.status == "PASS" and result.structured_output_path:
        try:
            raw = json.loads(Path(result.structured_output_path).read_text(encoding="utf-8"))
            proposed = raw["clusters"]
            seen_keys: set[str] = set()
            validated_groups = []
            for group in proposed:
                keys = [
                    k for k in group["problem_keys"] if k in candidate_groups and k not in seen_keys
                ]
                if not keys:
                    continue
                seen_keys.update(keys)
                validated_groups.append((keys, group["label"], group["canonical_problem"]))
            missing = set(candidate_groups) - seen_keys
            for key in missing:
                validated_groups.append(([key], labels[key], canonical_problems[key]))
            if validated_groups:
                grouped_keys = [keys for keys, _, _ in validated_groups]
                for keys, label, canonical_problem in validated_groups:
                    labels[keys[0]] = label
                    canonical_problems[keys[0]] = canonical_problem
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            errors.append(
                error(
                    "propose_clusters",
                    f"cluster proposal malformed, using deterministic fallback: {exc}",
                )
            )
    else:
        errors.append(
            error(
                "propose_clusters",
                f"cluster naming call {result.status}, using deterministic fallback",
            )
        )

    cluster_ids = []
    for keys in grouped_keys:
        cluster_id = _cluster_id_for(keys)
        member_ids = [eid for k in keys for eid in candidate_groups[k]]
        representative_key = keys[0]
        cluster = ProblemCluster(
            id=cluster_id,
            product=state["product"],
            label=labels.get(representative_key, _slug_to_label(representative_key)),
            canonical_problem=canonical_problems.get(representative_key, representative_key),
            member_evidence_ids=sorted(set(member_ids)),
            independence=ClusterIndependence(
                unique_authors=0, source_families=0, duplicate_groups=0
            ),
            signals=ClusterSignals(
                frequency=0,
                growth=0,
                pain=0,
                urgency=0,
                commercial_intent=0,
                commitment=0,
                product_fit=0,
                saturation=0,
            ),
            first_seen=_earliest_published(ctx, member_ids),
            last_seen=_latest_published(ctx, member_ids),
        )
        ctx.store.upsert_problem_cluster(state["run_id"], cluster)
        cluster_ids.append(cluster_id)

    return {"cluster_ids": cluster_ids, "errors": errors}


def _earliest_published(ctx: Any, evidence_ids: list[str]) -> Any:
    items = [ctx.store.get_evidence_item(eid) for eid in evidence_ids]
    return min(item.timestamps.published_at for item in items if item is not None)


def _latest_published(ctx: Any, evidence_ids: list[str]) -> Any:
    items = [ctx.store.get_evidence_item(eid) for eid in evidence_ids]
    return max(item.timestamps.published_at for item in items if item is not None)


def verify_clusters(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    """Deterministic membership validation (spec section 19): expand each
    cluster to include duplicate-linked members of its canonical evidence,
    drop any member id that turns out not to exist, and recompute
    independence from the corrected, real membership."""
    ctx = get_ctx(config)
    errors: list[dict[str, Any]] = []
    duplicate_links = ctx.store.list_duplicate_links(state["product"])
    duplicates_by_canonical: dict[str, list[str]] = {}
    for link in duplicate_links:
        duplicates_by_canonical.setdefault(link.canonical_evidence_id, []).append(link.evidence_id)
    duplicate_links_by_id = {link.evidence_id: link for link in duplicate_links}

    verified_ids = []
    for cluster_id in state["cluster_ids"]:
        cluster = ctx.store.get_problem_cluster(cluster_id)
        if cluster is None:
            errors.append(error("verify_clusters", "cluster vanished from store", None))
            continue

        valid_members = [
            eid for eid in cluster.member_evidence_ids if ctx.store.evidence_exists(eid)
        ]
        dropped = set(cluster.member_evidence_ids) - set(valid_members)
        if dropped:
            errors.append(
                error(
                    "verify_clusters",
                    f"dropped non-existent member ids: {sorted(dropped)}",
                    cluster_id,
                )
            )

        expanded = set(valid_members)
        for eid in valid_members:
            expanded.update(duplicates_by_canonical.get(eid, []))
        member_ids = sorted(expanded)

        member_items = {
            eid: item for eid in member_ids if (item := ctx.store.get_evidence_item(eid))
        }
        inputs = gather_independence_inputs(member_ids, member_items, duplicate_links_by_id)
        independence = compute_independence(inputs)

        updated = cluster.model_copy(
            update={"member_evidence_ids": member_ids, "independence": independence}
        )
        ctx.store.upsert_problem_cluster(state["run_id"], updated)
        verified_ids.append(cluster_id)

    return {"cluster_ids": verified_ids, "errors": errors}


def score_clusters(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    all_evidence = ctx.store.list_evidence_items(state["product"])
    all_times = [e.timestamps.published_at for e in all_evidence]
    midpoint = min(all_times) + (max(all_times) - min(all_times)) / 2 if all_times else None
    duplicate_links_by_id = {
        link.evidence_id: link for link in ctx.store.list_duplicate_links(state["product"])
    }

    for cluster_id in state["cluster_ids"]:
        cluster = ctx.store.get_problem_cluster(cluster_id)
        if cluster is None:
            continue
        member_items = {
            eid: item
            for eid in cluster.member_evidence_ids
            if (item := ctx.store.get_evidence_item(eid))
        }
        inputs = gather_independence_inputs(
            cluster.member_evidence_ids, member_items, duplicate_links_by_id
        )
        canonical_classifications = [
            c for item in inputs.canonical_members if (c := ctx.store.get_classification(item.id))
        ]
        n = len(canonical_classifications)

        if n == 0:
            signals = ClusterSignals(
                frequency=0,
                growth=0,
                pain=0,
                urgency=0,
                commercial_intent=0,
                commitment=0,
                product_fit=0,
                saturation=0,
            )
        else:
            frequency = min(1.0, n / FREQUENCY_CAP)
            if midpoint is not None:
                recent = sum(
                    1
                    for item in inputs.canonical_members
                    if item.timestamps.published_at >= midpoint
                )
                growth = recent / n
            else:
                growth = 0.0
            pain = sum(c.signals.pain for c in canonical_classifications) / n
            urgency = sum(c.signals.urgency for c in canonical_classifications) / n
            commercial_intent = (
                sum(c.signals.commercial_intent for c in canonical_classifications) / n
            )
            commitment = max(c.signals.commitment for c in canonical_classifications)
            product_fit = sum(c.relevance.product_fit for c in canonical_classifications) / n
            distinct_tools = {tool for c in canonical_classifications for tool in c.mentioned_tools}
            saturation = min(1.0, len(distinct_tools) / SATURATION_TOOL_CAP)
            signals = ClusterSignals(
                frequency=frequency,
                growth=growth,
                pain=pain,
                urgency=urgency,
                commercial_intent=commercial_intent,
                commitment=commitment,
                product_fit=product_fit,
                saturation=saturation,
            )

        updated = cluster.model_copy(update={"signals": signals})
        ctx.store.upsert_problem_cluster(state["run_id"], updated)

    return {}
