"""deterministic_judge, render_report, verify_run — no agent calls. The
judge is the one place OpportunityCard.status is ever written after
creation; render_report never calls a model (spec acceptance 23.7); verify_run
runs last so it can hash report.md itself into the signed record.
"""

from __future__ import annotations

import json
from typing import Any

from demand_radar.config import slugify
from demand_radar.graph.state import DemandState, get_ctx
from demand_radar.models import Verification, VerificationArtifacts, VerificationChecks
from demand_radar.reporting.json_report import (
    hash_file,
    hash_json_manifest,
    write_classifications_jsonl,
    write_clusters_json,
    write_critic_verdicts_json,
    write_opportunities_json,
)
from demand_radar.reporting.markdown import ReportContext
from demand_radar.reporting.markdown import render_report as render_report_markdown
from demand_radar.scoring.independence import (
    compute_independence,
    gather_independence_inputs,
)
from demand_radar.scoring.judge import JudgeInputs, judge_opportunity
from demand_radar.verification import compute_checks, overall_verdict


def deterministic_judge(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    thresholds = ctx.product.thresholds
    duplicate_links_by_id = {
        link.evidence_id: link for link in ctx.store.list_duplicate_links(state["product"])
    }

    for opp_id in state["opportunity_ids"]:
        card = ctx.store.get_opportunity_card(opp_id)
        if card is None:
            continue
        cluster = ctx.store.get_problem_cluster(card.cluster_ids[0]) if card.cluster_ids else None
        critic_verdict = ctx.store.get_critic_verdict(opp_id)

        evidence_refs_valid = all(
            ctx.store.evidence_exists(eid) for eid in card.evidence.evidence_ids
        )

        if cluster is not None:
            member_items = {
                eid: item
                for eid in cluster.member_evidence_ids
                if (item := ctx.store.get_evidence_item(eid))
            }
            inputs = gather_independence_inputs(
                cluster.member_evidence_ids, member_items, duplicate_links_by_id
            )
            canonical_classifications = [
                c
                for item in inputs.canonical_members
                if (c := ctx.store.get_classification(item.id))
            ]
            problem_evidence_count = sum(
                1
                for c in canonical_classifications
                if c.signals.pain > 0
                or any(s.supports in ("problem", "pain") for s in c.evidence_spans)
            )
            workaround_evidence_count = sum(
                1 for c in canonical_classifications if c.current_workarounds
            )
            dominant_single_duplicate_group = (
                len(inputs.canonical_members) <= 1 and len(inputs.duplicate_group_ids) >= 1
            )
            independence = compute_independence(inputs)
            unique_authors = independence.unique_authors
            source_families = independence.source_families
        else:
            problem_evidence_count = 0
            workaround_evidence_count = 0
            dominant_single_duplicate_group = False
            unique_authors = card.evidence.unique_authors
            source_families = card.evidence.source_families

        judge_inputs = JudgeInputs(
            unique_authors=unique_authors,
            source_families=source_families,
            problem_evidence_count=problem_evidence_count,
            workaround_evidence_count=workaround_evidence_count,
            canonical_member_count=unique_authors,
            dominant_single_duplicate_group=dominant_single_duplicate_group,
            schema_valid=True,  # Pydantic already enforced this at assembly time
            evidence_refs_valid=evidence_refs_valid,
            critic_verdict=critic_verdict,
            critic_agent_ok=critic_verdict is not None,
        )
        result = judge_opportunity(judge_inputs, thresholds)
        updated = card.model_copy(
            update={
                "status": result.status,
                "rejection_reasons": result.reasons or None,
            }
        )
        ctx.store.upsert_opportunity_card(state["run_id"], updated)

    return {}


def render_report(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    clusters = ctx.store.list_problem_clusters_for_run(state["run_id"])
    opportunities = ctx.store.list_opportunity_cards_for_run(state["run_id"])
    critic_verdicts = {
        card.id: v for card in opportunities if (v := ctx.store.get_critic_verdict(card.id))
    }

    evidence_ids: set[str] = set()
    for c in clusters:
        evidence_ids.update(c.member_evidence_ids)
    for o in opportunities:
        evidence_ids.update(o.evidence.evidence_ids)
    evidence_by_id = {
        eid: item for eid in evidence_ids if (item := ctx.store.get_evidence_item(eid))
    }

    duplicate_links = ctx.store.list_duplicate_links(state["product"])
    duplicate_group_count = len({link.duplicate_group_id for link in duplicate_links})

    all_product_evidence = ctx.store.list_evidence_items(state["product"])
    blind_spots = [
        q
        for q in ctx.product.problem_queries
        if not any(item.collection.query_id == slugify(q) for item in all_product_evidence)
    ]

    analyst_produced_any = bool(state["classifications"]) or bool(state["opportunity_ids"])
    # Every opportunity must have a verdict for critic_status to read PASS --
    # not just one. This matters most for --critic human (Phase 2C): a
    # partial imported-review set must not read as a completed critic pass
    # (see demand-radar review import's "critic_status != PASS until every
    # opportunity in the run has a valid imported review"). It also
    # corrects a latent imprecision for the agent-critic path: if
    # critic_review breaks off after reviewing only some opportunities
    # non-systemically, that is honestly "incomplete", not "PASS".
    critic_produced_any = bool(opportunities) and len(critic_verdicts) == len(opportunities)
    pre_checks = compute_checks(
        store=ctx.store,
        product=state["product"],
        opportunities=opportunities,
        schemas_dir=ctx.schemas_dir,
        thresholds=ctx.product.thresholds,
        errors=state["errors"],
        analyst_produced_any=analyst_produced_any,
        critic_produced_any=critic_produced_any,
        report_generated=True,
    )

    report_ctx = ReportContext(
        run_id=state["run_id"],
        product=state["product"],
        since=state.get("since"),
        clusters=clusters,
        opportunities=opportunities,
        critic_verdicts=critic_verdicts,
        evidence_by_id=evidence_by_id,
        total_evidence_count=len(state["input_evidence_ids"]),
        canonical_evidence_count=len(state["accepted_evidence_ids"]),
        duplicate_group_count=duplicate_group_count,
        pre_verification_checks=pre_checks,
        collection_blind_spots=blind_spots,
    )
    markdown = render_report_markdown(report_ctx)

    outputs_dir = ctx.run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "report.md").write_text(markdown, encoding="utf-8")

    classifications = [
        cl for eid in state["classifications"] if (cl := ctx.store.get_classification(eid))
    ]
    write_classifications_jsonl(outputs_dir / "classifications.jsonl", classifications)
    write_clusters_json(outputs_dir / "clusters.json", clusters)
    write_opportunities_json(outputs_dir / "opportunities.json", opportunities)
    write_critic_verdicts_json(outputs_dir / "critic-verdicts.json", list(critic_verdicts.values()))

    return {}


def verify_run(state: DemandState, config: dict[str, Any]) -> dict[str, Any]:
    ctx = get_ctx(config)
    opportunities = ctx.store.list_opportunity_cards_for_run(state["run_id"])
    # See render_report's identical fix above: every opportunity, not just
    # one, must have a verdict for critic_status to read PASS.
    critic_verdicts_present = bool(opportunities) and all(
        ctx.store.get_critic_verdict(card.id) is not None for card in opportunities
    )

    analyst_produced_any = bool(state["classifications"]) or bool(state["opportunity_ids"])
    checks = compute_checks(
        store=ctx.store,
        product=state["product"],
        opportunities=opportunities,
        schemas_dir=ctx.schemas_dir,
        thresholds=ctx.product.thresholds,
        errors=state["errors"],
        analyst_produced_any=analyst_produced_any,
        critic_produced_any=critic_verdicts_present,
        report_generated=(ctx.run_dir / "outputs" / "report.md").is_file(),
    )
    verdict = overall_verdict(checks)

    outputs_dir = ctx.run_dir / "outputs"
    input_manifest_path = ctx.run_dir / "input-manifest.json"
    manifest = {
        "run_id": state["run_id"],
        "product": state["product"],
        "since": state.get("since"),
        "input_evidence_ids": sorted(state["input_evidence_ids"]),
    }
    input_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    verification = Verification(
        run_id=state["run_id"],
        verdict=verdict,  # type: ignore[arg-type]
        checks=VerificationChecks.model_validate(checks),
        artifacts=VerificationArtifacts(
            input_manifest_hash=hash_json_manifest(manifest),
            opportunities_hash=hash_file(outputs_dir / "opportunities.json"),
            report_hash=hash_file(outputs_dir / "report.md"),
        ),
    )
    (ctx.run_dir / "verification.json").write_text(
        verification.model_dump_json(by_alias=True, indent=2), encoding="utf-8"
    )
    ctx.store.update_run_status(state["run_id"], status="completed", verdict=verdict)

    return {"verdict": verdict}
