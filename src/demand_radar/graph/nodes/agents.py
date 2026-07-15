"""classify, generate_opportunities, critic_review — the three agent-invoking
nodes. Each follows the same shape: build a prompt, call the runner with
retry, persist the full call record, then re-validate the structured output
with Pydantic before trusting it at all (the runner's own --json-schema/
--output-schema check is not the only guard). A BLOCKED_AUTH/BLOCKED_USAGE
result aborts the rest of *this node's* calls immediately (no point burning
more calls against broken auth); any other failure is scoped to just that
one item and the loop continues -- partial results are real results, never
hidden (spec: "не скрывать частичные или malformed outputs").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from demand_radar.agents.base import (
    READ_ONLY_DATA_PROFILE,
    call_agent_with_retry,
    persist_call_artifacts,
)
from demand_radar.agents.prompts.classify import build_classification_prompt
from demand_radar.agents.prompts.critic import build_critic_prompt
from demand_radar.agents.prompts.opportunity import (
    OPPORTUNITY_CANDIDATE_SCHEMA,
    build_opportunity_prompt,
)
from demand_radar.graph.state import DemandState, error, get_ctx
from demand_radar.models import (
    CheapestExperiment,
    Classification,
    CriticVerdict,
    OpportunityCard,
    OpportunityContext,
    OpportunityEvidence,
    OpportunityPersona,
    OpportunityProblem,
    OpportunitySignals,
    ProblemCluster,
    Wedge,
)
from demand_radar.scoring.demand_score import compute_confidence, compute_demand_score
from demand_radar.scoring.independence import duplication_ratio, gather_independence_inputs

_SYSTEMIC_STATUSES = {"BLOCKED_AUTH", "BLOCKED_USAGE"}


def classify(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    classified_ids: list[str] = []
    errors: list[dict[str, Any]] = []
    schema_path = ctx.schemas_dir / "classification.schema.json"
    base_dir = ctx.run_dir / "agents" / "analyst" / "classify"

    for eid in state["accepted_evidence_ids"]:
        item = ctx.store.get_evidence_item(eid)
        if item is None:
            errors.append(error("classify", "evidence_id vanished from store", eid))
            continue

        prompt = build_classification_prompt(item, ctx.product)
        task_id = f"classify:{eid}"
        call_dir = base_dir / eid
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

        if result.status in _SYSTEMIC_STATUSES:
            errors.append(
                error(
                    "classify", f"analyst {result.status}, aborting remaining classify calls", eid
                )
            )
            break
        if result.status != "PASS" or not result.structured_output_path:
            errors.append(error("classify", f"analyst call failed: {result.status}", eid))
            continue

        try:
            raw = json.loads(Path(result.structured_output_path).read_text(encoding="utf-8"))
            classification = Classification.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            errors.append(error("classify", f"post-hoc schema validation failed: {exc}", eid))
            continue
        if classification.evidence_id != eid:
            errors.append(
                error(
                    "classify",
                    f"evidence_id mismatch: response claims {classification.evidence_id}",
                    eid,
                )
            )
            continue

        ctx.store.upsert_classification(state["run_id"], classification)
        classified_ids.append(eid)

    return {
        "classifications": classified_ids,
        "analyst_run_id": f"{state['run_id']}-analyst",
        "errors": errors,
    }


def _assemble_opportunity_card(
    ctx: Any, product: str, cluster: ProblemCluster, candidate: dict[str, Any]
) -> OpportunityCard:
    member_items = {
        eid: item
        for eid in cluster.member_evidence_ids
        if (item := ctx.store.get_evidence_item(eid))
    }
    duplicate_links_by_id = {
        link.evidence_id: link for link in ctx.store.list_duplicate_links(product)
    }
    inputs = gather_independence_inputs(
        cluster.member_evidence_ids, member_items, duplicate_links_by_id
    )
    dup_ratio = duplication_ratio(inputs)
    score_result = compute_demand_score(
        cluster.signals,
        source_families=cluster.independence.source_families,
        duplication_ratio=dup_ratio,
        weights=ctx.product.scoring_weights,
    )

    canonical_classifications = [
        c for item in inputs.canonical_members if (c := ctx.store.get_classification(item.id))
    ]
    mean_confidence = (
        sum(c.relevance.confidence for c in canonical_classifications)
        / len(canonical_classifications)
        if canonical_classifications
        else 0.0
    )
    confidence = compute_confidence(
        unique_authors=cluster.independence.unique_authors,
        minimum_unique_authors=ctx.product.thresholds.minimum_unique_authors,
        source_families=cluster.independence.source_families,
        minimum_source_families=ctx.product.thresholds.minimum_source_families,
        mean_relevance_confidence=mean_confidence,
        has_fatal_objection=False,
    )

    opp_id = f"opp_{cluster.id.removeprefix('cluster_')}"
    return OpportunityCard(
        id=opp_id,
        product=product,
        cluster_ids=[cluster.id],
        problem=OpportunityProblem(statement=candidate["problem"]["statement"]),
        persona=OpportunityPersona(primary=candidate["persona"]["primary"]),
        context=OpportunityContext(situation=candidate["context"]["situation"]),
        evidence=OpportunityEvidence(
            evidence_ids=cluster.member_evidence_ids,
            unique_authors=cluster.independence.unique_authors,
            source_families=cluster.independence.source_families,
        ),
        signals=OpportunitySignals(demand_score=score_result.total, confidence=confidence),
        current_workarounds=candidate["current_workarounds"],
        existing_substitutes=candidate["existing_substitutes"],
        possible_wedges=[Wedge.model_validate(w) for w in candidate["possible_wedges"]],
        risks=candidate["risks"],
        cheapest_experiment=CheapestExperiment.model_validate(candidate["cheapest_experiment"]),
        status="observed",
    )


def generate_opportunities(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    opportunity_ids: list[str] = []
    errors: list[dict[str, Any]] = []

    candidate_schema_path = ctx.run_dir / "schemas" / "opportunity-candidate.schema.json"
    candidate_schema_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_schema_path.write_text(json.dumps(OPPORTUNITY_CANDIDATE_SCHEMA, indent=2))
    base_dir = ctx.run_dir / "agents" / "analyst" / "generate_opportunities"

    for cluster_id in state["cluster_ids"]:
        cluster = ctx.store.get_problem_cluster(cluster_id)
        if cluster is None:
            errors.append(error("generate_opportunities", "cluster vanished from store", None))
            continue

        member_classifications = [
            c for eid in cluster.member_evidence_ids if (c := ctx.store.get_classification(eid))
        ]
        evidence_by_id = {
            eid: item
            for eid in cluster.member_evidence_ids
            if (item := ctx.store.get_evidence_item(eid))
        }
        prompt = build_opportunity_prompt(
            cluster, member_classifications, evidence_by_id, ctx.product
        )
        task_id = f"generate_opportunity:{cluster_id}"
        call_dir = base_dir / cluster_id
        result = call_agent_with_retry(
            ctx.analyst_runner,
            task_id=task_id,
            prompt=prompt,
            input_paths=[],
            output_schema=candidate_schema_path,
            capability_profile=READ_ONLY_DATA_PROFILE,
            run_dir=call_dir,
            max_retries=ctx.max_agent_retries,
        )
        persist_call_artifacts(call_dir, task_id, prompt, result)

        if result.status in _SYSTEMIC_STATUSES:
            errors.append(
                error(
                    "generate_opportunities",
                    f"analyst {result.status}, aborting remaining opportunity generation",
                    cluster_id,
                )
            )
            break
        if result.status != "PASS" or not result.structured_output_path:
            errors.append(
                error("generate_opportunities", f"analyst call failed: {result.status}", cluster_id)
            )
            continue

        try:
            raw = json.loads(Path(result.structured_output_path).read_text(encoding="utf-8"))
            card = _assemble_opportunity_card(ctx, state["product"], cluster, raw)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            errors.append(
                error("generate_opportunities", f"invalid candidate shape: {exc}", cluster_id)
            )
            continue

        ctx.store.upsert_opportunity_card(state["run_id"], card)
        opportunity_ids.append(card.id)

    return {"opportunity_ids": opportunity_ids, "errors": errors}


def critic_review(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    errors: list[dict[str, Any]] = []
    schema_path = ctx.schemas_dir / "critic-verdict.schema.json"
    base_dir = ctx.run_dir / "agents" / "critic" / "critic_review"

    for opp_id in state["opportunity_ids"]:
        card = ctx.store.get_opportunity_card(opp_id)
        if card is None:
            errors.append(error("critic_review", "opportunity vanished from store", None))
            continue

        evidence_by_id = {
            eid: item
            for eid in card.evidence.evidence_ids
            if (item := ctx.store.get_evidence_item(eid))
        }
        prompt = build_critic_prompt(card, evidence_by_id)
        task_id = f"critic:{opp_id}"
        call_dir = base_dir / opp_id
        result = call_agent_with_retry(
            ctx.critic_runner,
            task_id=task_id,
            prompt=prompt,
            input_paths=[],
            output_schema=schema_path,
            capability_profile=READ_ONLY_DATA_PROFILE,
            run_dir=call_dir,
            max_retries=ctx.max_agent_retries,
        )
        persist_call_artifacts(call_dir, task_id, prompt, result)

        if result.status in _SYSTEMIC_STATUSES:
            errors.append(
                error(
                    "critic_review",
                    f"critic {result.status}, aborting remaining critic calls",
                    opp_id,
                )
            )
            break
        if result.status != "PASS" or not result.structured_output_path:
            errors.append(error("critic_review", f"critic call failed: {result.status}", opp_id))
            continue

        try:
            raw = json.loads(Path(result.structured_output_path).read_text(encoding="utf-8"))
            verdict = CriticVerdict.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            errors.append(
                error("critic_review", f"post-hoc schema validation failed: {exc}", opp_id)
            )
            continue
        if verdict.opportunity_id != opp_id:
            errors.append(
                error("critic_review", f"opportunity_id mismatch: {verdict.opportunity_id}", opp_id)
            )
            continue
        unknown_refs = [
            eid
            for o in verdict.objections
            for eid in o.evidence_ids
            if ctx.store.get_evidence_item(eid) is None
        ]
        if unknown_refs:
            errors.append(
                error(
                    "critic_review",
                    f"critic referenced unknown evidence ids: {unknown_refs}",
                    opp_id,
                )
            )
            continue

        ctx.store.upsert_critic_verdict(state["run_id"], verdict)

    return {"critic_run_id": f"{state['run_id']}-critic", "errors": errors}
