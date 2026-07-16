"""Pydantic mirrors of schemas/*.json.

These are the Python-side source of truth used by application code; the JSON
Schema files under schemas/ are the cross-language / agent-facing contract.
`tests/unit/test_schema_parity.py` keeps the two in sync by validating shared
golden fixtures against both.

Every model forbids extra fields (`extra="forbid"`), mirroring
`additionalProperties: false` in the JSON schemas: unknown fields from an
agent response are a validation failure, not silently ignored data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Sha256Hash = str  # "sha256:<64 hex chars>" — validated via pattern= at each use site.

_EVIDENCE_ID = r"^ev_[a-zA-Z0-9_-]+$"
_CLUSTER_ID = r"^cluster_[a-zA-Z0-9_-]+$"
_OPPORTUNITY_ID = r"^opp_[a-zA-Z0-9_-]+$"
_SHA256 = r"^sha256:[0-9a-f]{64}$"
_SLUG = r"^[a-z0-9-]+$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------
# EvidenceItem
# --------------------------------------------------------------------------


class EvidenceSource(StrictModel):
    kind: str = Field(min_length=1, max_length=60)
    provider: str = Field(min_length=1, max_length=60)
    source_id: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=2000)
    source_family: str = Field(min_length=1, max_length=60)


class EvidenceAuthor(StrictModel):
    stable_hash: str = Field(pattern=_SHA256)
    display_name: str | None = Field(default=None, max_length=200)


class EvidenceTimestamps(StrictModel):
    published_at: datetime
    collected_at: datetime


class EvidenceContent(StrictModel):
    raw_text: str = Field(min_length=1, max_length=20000)
    normalized_text: str = Field(max_length=20000)
    language: str = Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    content_hash: str = Field(pattern=_SHA256)


class EvidenceCollection(StrictModel):
    query_id: str = Field(min_length=1, max_length=120)
    collector_version: str = Field(min_length=1, max_length=60)
    import_batch_id: str = Field(min_length=1, max_length=120)


class EvidenceTrust(StrictModel):
    untrusted_external_content: Literal[True] = True


class EvidenceItem(StrictModel):
    schema_: Literal["demand-radar.evidence-item/1"] = Field(
        default="demand-radar.evidence-item/1", alias="schema"
    )
    id: str = Field(pattern=_EVIDENCE_ID)
    product: str = Field(min_length=1, max_length=60, pattern=_SLUG)
    source: EvidenceSource
    author: EvidenceAuthor
    timestamps: EvidenceTimestamps
    content: EvidenceContent
    collection: EvidenceCollection
    trust: EvidenceTrust

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

EvidenceSpanSupports = Literal[
    "persona",
    "situation",
    "problem",
    "desired_outcome",
    "workaround",
    "commercial_intent",
    "urgency",
    "pain",
]


class Relevance(StrictModel):
    product_fit: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    relevant: bool


class Persona(StrictModel):
    label: str = Field(min_length=1, max_length=80)


class Situation(StrictModel):
    statement: str = Field(min_length=1, max_length=400)


class ProblemStatement(StrictModel):
    statement: str = Field(min_length=1, max_length=400)
    problem_key: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=80)


class DesiredOutcome(StrictModel):
    statement: str = Field(min_length=1, max_length=400)


class ClassificationSignals(StrictModel):
    pain: float = Field(ge=0, le=1)
    urgency: float = Field(ge=0, le=1)
    commercial_intent: float = Field(ge=0, le=1)
    commitment: float = Field(ge=0, le=1)


class EvidenceSpan(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    supports: EvidenceSpanSupports


class Classification(StrictModel):
    schema_: Literal["demand-radar.classification/1"] = Field(
        default="demand-radar.classification/1", alias="schema"
    )
    evidence_id: str = Field(pattern=_EVIDENCE_ID)
    relevance: Relevance
    persona: Persona
    situation: Situation
    problem: ProblemStatement
    desired_outcome: DesiredOutcome
    current_workarounds: list[str] = Field(default_factory=list, max_length=10)
    signals: ClassificationSignals
    mentioned_tools: list[str] = Field(default_factory=list, max_length=20)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list, max_length=20)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# ProblemCluster
# --------------------------------------------------------------------------


class ClusterIndependence(StrictModel):
    unique_authors: int = Field(ge=0)
    source_families: int = Field(ge=0)
    duplicate_groups: int = Field(ge=0)


class ClusterSignals(StrictModel):
    frequency: float = Field(ge=0, le=1)
    growth: float = Field(ge=0, le=1)
    pain: float = Field(ge=0, le=1)
    urgency: float = Field(ge=0, le=1)
    commercial_intent: float = Field(ge=0, le=1)
    commitment: float = Field(ge=0, le=1)
    product_fit: float = Field(ge=0, le=1)
    saturation: float = Field(ge=0, le=1)


class ProblemCluster(StrictModel):
    schema_: Literal["demand-radar.problem-cluster/1"] = Field(
        default="demand-radar.problem-cluster/1", alias="schema"
    )
    id: str = Field(pattern=_CLUSTER_ID)
    product: str = Field(min_length=1, max_length=60, pattern=_SLUG)
    label: str = Field(min_length=1, max_length=100)
    canonical_problem: str = Field(min_length=1, max_length=400)
    member_evidence_ids: list[str] = Field(min_length=1)
    independence: ClusterIndependence
    signals: ClusterSignals
    first_seen: datetime
    last_seen: datetime

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# OpportunityCard
# --------------------------------------------------------------------------

OpportunityStatus = Literal[
    "observed", "investigate", "experiment_ready", "rejected", "externally_validated"
]
WedgeType = Literal[
    "concierge_service", "cli", "desktop_tool", "browser_extension", "plugin", "saas", "other"
]


class OpportunityProblem(StrictModel):
    statement: str = Field(min_length=1, max_length=500)


class OpportunityPersona(StrictModel):
    primary: str = Field(min_length=1, max_length=120)


class OpportunityContext(StrictModel):
    situation: str = Field(min_length=1, max_length=500)


class OpportunityEvidence(StrictModel):
    evidence_ids: list[str] = Field(min_length=1)
    unique_authors: int = Field(ge=0)
    source_families: int = Field(ge=0)


class OpportunitySignals(StrictModel):
    demand_score: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)


class Wedge(StrictModel):
    type: WedgeType
    offer: str = Field(min_length=1, max_length=300)


class CheapestExperiment(StrictModel):
    hypothesis: str = Field(min_length=1, max_length=400)
    input: str = Field(min_length=1, max_length=300)
    output: str = Field(min_length=1, max_length=300)
    commitment_event: str = Field(min_length=1, max_length=300)
    success_threshold: str = Field(min_length=1, max_length=300)
    failure_threshold: str = Field(min_length=1, max_length=300)


class OpportunityCard(StrictModel):
    schema_: Literal["demand-radar.opportunity-card/1"] = Field(
        default="demand-radar.opportunity-card/1", alias="schema"
    )
    id: str = Field(pattern=_OPPORTUNITY_ID)
    product: str = Field(min_length=1, max_length=60, pattern=_SLUG)
    cluster_ids: list[str] = Field(min_length=1)
    problem: OpportunityProblem
    persona: OpportunityPersona
    context: OpportunityContext
    evidence: OpportunityEvidence
    signals: OpportunitySignals
    current_workarounds: list[str] = Field(default_factory=list, max_length=15)
    existing_substitutes: list[str] = Field(default_factory=list, max_length=15)
    possible_wedges: list[Wedge] = Field(default_factory=list, max_length=8)
    risks: list[str] = Field(default_factory=list, max_length=15)
    cheapest_experiment: CheapestExperiment
    status: OpportunityStatus
    rejection_reasons: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Populated whenever status is investigate or rejected; omitted otherwise.",
    )

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# CriticVerdict
# --------------------------------------------------------------------------

ObjectionCode = Literal[
    "complaint_not_purchase_intent",
    "single_viral_source_dominant",
    "duplicate_or_copied_content",
    "audience_lacks_budget",
    "free_substitute_sufficient",
    "educational_not_commercial",
    "removes_enjoyable_activity",
    "required_input_unavailable",
    "implementation_cost_dominates",
    "evidence_supports_different_wedge",
    "overclaims_evidence",
    "other",
]
RecommendedStatus = Literal["experiment_ready", "investigate", "rejected"]


class Objection(StrictModel):
    code: ObjectionCode
    statement: str = Field(min_length=1, max_length=400)
    evidence_ids: list[str] = Field(default_factory=list)
    fatal: bool


class OverclaimCheck(StrictModel):
    overclaims: bool
    statement: str = Field(default="", max_length=400)


class CriticVerdict(StrictModel):
    schema_: Literal["demand-radar.critic-verdict/1"] = Field(
        default="demand-radar.critic-verdict/1", alias="schema"
    )
    opportunity_id: str = Field(pattern=_OPPORTUNITY_ID)
    recommended_status: RecommendedStatus
    objections: list[Objection] = Field(default_factory=list, max_length=20)
    overclaim_check: OverclaimCheck
    notes: str = Field(default="", max_length=500)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# ReviewEnvelope — Phase 2C human review. An externally-authored, structured
# review of one opportunity candidate, imported after analyst execution has
# already completed and been hashed. "Independent" means independent of
# analyst execution -- not generated by the analyst provider, not a second
# same-provider critique, not produced by the worker agent that ran the
# trial -- not a claim of independence from the product owner or from
# commercial interest. The nested verdict reuses CriticVerdict unchanged.
# --------------------------------------------------------------------------


class Reviewer(StrictModel):
    kind: Literal["human"]
    id: str = Field(
        min_length=1,
        max_length=120,
        description="A stable identifier for this reviewer that does not itself carry PII "
        "(e.g. a pseudonymous handle, not an email address).",
    )
    conflict: str = Field(
        min_length=1,
        max_length=120,
        description="Explicit conflict-of-interest disclosure, e.g. 'none' or 'product_owner'. "
        "Required on every review -- never silently omitted. A product-owner review is not "
        "market validation.",
    )


class ReviewAttestation(StrictModel):
    reviewed_primary_evidence: bool
    review_not_generated_by_analyst_provider: bool


class ReviewEnvelope(StrictModel):
    schema_: Literal["demand-radar.review-envelope/1"] = Field(
        default="demand-radar.review-envelope/1", alias="schema"
    )
    run_id: str = Field(min_length=1, max_length=120)
    opportunity_id: str = Field(pattern=_OPPORTUNITY_ID)
    reviewer: Reviewer
    reviewed_at: datetime
    opportunity_hash: str = Field(pattern=_SHA256)
    evidence_manifest_hash: str = Field(pattern=_SHA256)
    attestation: ReviewAttestation
    verdict: CriticVerdict

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# ReviewFixtureEnvelope — Phase 2C-SMOKE. A synthetic, machine-generated
# stand-in for a ReviewEnvelope used ONLY to exercise the export -> import ->
# finalize pipeline's mechanics (hashing, schema validation, atomic import,
# deterministic finalize). It is not a review, not independent, and not
# market validation -- fixture.kind and .substantive_review_performed are
# fixed Literals so a fixture can never be constructed claiming otherwise.
# review.py's import path additionally requires --allow-test-fixture and a
# run explicitly marked test_fixture=true before accepting one of these; a
# real ReviewEnvelope never needs or accepts that flag. The nested verdict
# reuses CriticVerdict unchanged, same as ReviewEnvelope.
# --------------------------------------------------------------------------


class ReviewFixtureMetadata(StrictModel):
    kind: Literal["test_fixture"]
    generator: str = Field(
        min_length=1,
        max_length=120,
        description="Identifies what produced this fixture, e.g. "
        "'demand-radar-review-smoke' (the CLI's own `review generate-fixtures` command).",
    )
    purpose: Literal["pipeline_mechanics_only"]
    substantive_review_performed: Literal[False]


class ReviewFixtureEnvelope(StrictModel):
    schema_: Literal["demand-radar.review-fixture-envelope/1"] = Field(
        default="demand-radar.review-fixture-envelope/1", alias="schema"
    )
    run_id: str = Field(min_length=1, max_length=120)
    opportunity_id: str = Field(pattern=_OPPORTUNITY_ID)
    fixture: ReviewFixtureMetadata
    generated_at: datetime
    opportunity_hash: str = Field(pattern=_SHA256)
    evidence_manifest_hash: str = Field(pattern=_SHA256)
    verdict: CriticVerdict

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

CheckStatus = Literal["PASS", "FAIL"]
AgentRunStatus = Literal[
    "PASS",
    "BLOCKED_AUTH",
    "BLOCKED_USAGE",
    "BLOCKED_TIMEOUT",
    "BLOCKED_NOT_INSTALLED",
    "FAIL_INVALID_OUTPUT",
    "FAIL_SCHEMA",
    "NOT_RUN",
]


class VerificationChecks(StrictModel):
    schemas_valid: CheckStatus
    evidence_refs_valid: CheckStatus
    duplicate_inflation_absent: CheckStatus
    deterministic_rules_passed: CheckStatus
    report_generated: CheckStatus
    analyst_status: AgentRunStatus
    critic_status: AgentRunStatus


class VerificationArtifacts(StrictModel):
    input_manifest_hash: str = Field(pattern=_SHA256)
    opportunities_hash: str = Field(pattern=_SHA256)
    report_hash: str = Field(pattern=_SHA256)


class Verification(StrictModel):
    schema_: Literal["demand-radar.verification/1"] = Field(
        default="demand-radar.verification/1", alias="schema"
    )
    run_id: str = Field(min_length=1, max_length=120)
    verdict: Literal["PASS", "FAIL", "BLOCKED"]
    checks: VerificationChecks
    artifacts: VerificationArtifacts

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# Internal models (not published as schemas/*.json — storage-layer / runtime
# constructs referenced by spec sections 13 and 18).
# --------------------------------------------------------------------------


class DuplicateLink(StrictModel):
    """One evidence item's duplicate relationship. Stored, not schema-published."""

    evidence_id: str = Field(pattern=_EVIDENCE_ID)
    duplicate_group_id: str
    canonical_evidence_id: str = Field(pattern=_EVIDENCE_ID)
    duplicate_reason: Literal["same_url", "same_content_hash", "similar_text"]
    similarity_score: float = Field(ge=0, le=1)


class DuplicateItem(StrictModel):
    """One duplicate group, for evidence/duplicates.jsonl run artifact."""

    duplicate_group_id: str
    canonical_evidence_id: str = Field(pattern=_EVIDENCE_ID)
    duplicate_evidence_ids: list[str] = Field(min_length=1)


class AgentResult(StrictModel):
    """Contract for AgentRunner.run() — spec section 13."""

    provider: str
    command_version: str | None = None
    model: str | None = None
    started_at: datetime
    finished_at: datetime
    exit_code: int | None = None
    status: AgentRunStatus
    stdout_path: str
    stderr_path: str
    structured_output_path: str | None = None
    schema_valid: bool
    prompt_hash: str = Field(pattern=_SHA256)
    input_hashes: list[str] = Field(default_factory=list)
    error_kind: str | None = None
