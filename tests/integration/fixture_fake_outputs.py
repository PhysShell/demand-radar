"""Canned Classification / OpportunityCandidate / CriticVerdict outputs for
the FakeRunner, matched to fixtures/mixed-demand-signals.jsonl by evidence
id. Shared between the fixture end-to-end integration test and the CLI's
own `demand-radar run --analyst fake --critic fake` path, so both exercise
the exact same scripted pipeline outcome.

Building this table requires knowing each fixture record's deterministic
evidence_id (product + source_kind + source_id, see
ingest/normalize.py::evidence_id_for) — computed once here rather than
hand-copied, so it can never drift from the real ids ingestion produces.
"""

from __future__ import annotations

from demand_radar.agents.fake import FakeScenario
from demand_radar.ingest.normalize import evidence_id_for

PRODUCT = "own-audit"

STRONG_PROBLEM_KEY = "missing-memory-ownership-path"
WEAK_PROBLEM_KEY = "propertychanged-perf-blind-spot"
VIRAL_PROBLEM_KEY = "venting-about-leaks"
EDUCATIONAL_PROBLEM_KEY = "cs0246-namespace-not-found"


def eid(source_kind: str, source_id: str) -> str:
    return evidence_id_for(PRODUCT, source_kind, source_id)


# id -> (relevant, persona, situation, problem_statement, problem_key,
#        desired_outcome, workarounds, pain, urgency, commercial_intent,
#        commitment, mentioned_tools)
_CLASSIFICATION_TABLE: dict[str, tuple] = {
    eid("reddit_post", "reddit-dave-1"): (
        True,
        "legacy-dotnet-maintainer",
        "maintaining a production WPF application",
        "existing tools show retained objects but not the ownership path",
        STRONG_PROBLEM_KEY,
        "an actionable ownership trace instead of a raw retained-object dump",
        ["wrote a custom Roslyn analyzer to trace ownership manually"],
        0.8,
        0.5,
        0.3,
        0.0,
        ["dotMemory"],
    ),
    eid("reddit_post", "reddit-maria-1"): (
        True,
        "wpf-developer",
        "evaluating static-analysis tools for a large WPF app",
        "no tool traces WPF event-subscription leaks end-to-end",
        STRONG_PROBLEM_KEY,
        "a tool that explains why a window will not be collected",
        [],
        0.6,
        0.4,
        0.8,
        0.0,
        ["NDepend"],
    ),
    eid("forum_post", "forum-kim-1"): (
        True,
        "technical-lead",
        "maintaining a 15-year-old WPF codebase",
        "leak tools show the object graph but never the fix",
        STRONG_PROBLEM_KEY,
        "an automated ownership trace instead of days of manual tracing",
        ["manually traces ownership by hand for every leak"],
        0.9,
        0.7,
        0.2,
        0.0,
        ["ANTS", "dotMemory"],
    ),
    eid("github_issue", "gh-issue-alex-1"): (
        True,
        "software-modernization-consultant",
        "auditing a 400k-LOC WPF Framework app",
        "no tool reports the ownership path behind a retained object",
        STRONG_PROBLEM_KEY,
        "the tool itself reporting who holds the reference",
        ["wrote internal scripts to bisect ownership manually"],
        0.7,
        0.5,
        0.3,
        0.0,
        [],
    ),
    eid("forum_post", "forum-r-consultant-1"): (
        True,
        "software-modernization-consultant",
        "advising a client on legacy .NET tooling",
        "no Roslyn-based analyzer points to root cause for leaks",
        STRONG_PROBLEM_KEY,
        "a root-cause-capable architecture analyzer the client would pay for",
        [],
        0.5,
        0.4,
        0.9,
        0.0,
        ["Roslyn"],
    ),
    eid("forum_post", "forum-greg-1"): (
        True,
        "wpf-developer",
        "long-time sufferer of unexplained WPF leaks",
        "leak tools have never explained ownership chains",
        STRONG_PROBLEM_KEY,
        "does not believe a fix is coming",
        [],
        0.6,
        0.2,
        0.0,
        0.0,
        ["dotMemory"],
    ),
    eid("review", "review-lee-1"): (
        True,
        "engineering-manager",
        "budgeting tooling spend for the quarter",
        "no out-of-the-box tool does ownership-path leak tracing for WPF",
        STRONG_PROBLEM_KEY,
        "a tool worth signing a contract for this month",
        [],
        0.7,
        0.8,
        1.0,
        1.0,
        [],
    ),
    eid("chat_export", "discord-priya-1"): (
        True,
        "technical-lead",
        "QA on a team that keeps reintroducing the same WPF leak",
        "no tool identifies which subscription to remove before release",
        STRONG_PROBLEM_KEY,
        "an automatic pre-release ownership check",
        ["built an internal manual subscription-review checklist"],
        0.7,
        0.6,
        0.2,
        0.0,
        [],
    ),
    eid("review", "review-kate-1"): (
        True,
        "engineering-manager",
        "evaluating a .NET technical-debt reporting tool",
        "no reporting tool traces WPF memory ownership specifically",
        STRONG_PROBLEM_KEY,
        "ownership-aware reporting, not just retained-object flags",
        [],
        0.4,
        0.3,
        0.8,
        0.0,
        [],
    ),
    eid("forum_post", "forum-injection-1"): (
        # Prompt-injection bait wrapped in a superficially on-topic WPF complaint --
        # classified honestly as weak, low-signal evidence. Nothing about how this
        # item is scored depends on the embedded instructions; see
        # docs/trust-boundaries.md and tests/unit/test_prompt_injection.py for the
        # structural guarantee (no tool surface, no status field the model can set).
        True,
        "wpf-developer",
        "reports a WPF memory leak",
        "generic unexplained WPF memory leak complaint",
        STRONG_PROBLEM_KEY,
        "the leak fixed",
        [],
        0.3,
        0.1,
        0.0,
        0.0,
        [],
    ),
    eid("reddit_post", "reddit-nina-1"): (
        True,
        "wpf-developer",
        "WPF app with >5000 bound properties on one view",
        "no way to profile which PropertyChanged bindings are the hot path",
        WEAK_PROBLEM_KEY,
        "a profiler that ranks bindings by cost",
        [],
        0.6,
        0.5,
        0.2,
        0.0,
        [],
    ),
    eid("forum_post", "forum-omar-1"): (
        True,
        "wpf-developer",
        "PropertyChanged storms slow down grid rendering",
        "no built-in way to find the worst-offending bindings",
        WEAK_PROBLEM_KEY,
        "the worst offenders surfaced automatically",
        ["hacked together a stopwatch-based logger per setter"],
        0.6,
        0.4,
        0.1,
        0.0,
        [],
    ),
    eid("hn_comment", "hn-zed-1"): (
        True,
        "wpf-developer",
        "jokes about WPF leaks as a pastime",
        "venting about WPF memory leaks with no real ask",
        VIRAL_PROBLEM_KEY,
        "n/a -- venting, not a request",
        [],
        0.2,
        0.0,
        0.0,
        0.0,
        ["dotMemory"],
    ),
    eid("reddit_post", "reddit-praise-1"): (
        False,
        "wpf-developer",
        "not applicable",
        "not applicable",
        "not-applicable-praise",
        "not applicable",
        [],
        0.0,
        0.0,
        0.0,
        0.0,
        [],
    ),
    eid("forum_post", "forum-praise-2"): (
        False,
        "wpf-developer",
        "not applicable",
        "not applicable",
        "not-applicable-praise",
        "not applicable",
        [],
        0.0,
        0.0,
        0.0,
        0.0,
        [],
    ),
    eid("rss_entry", "rss-news-1"): (
        False,
        "wpf-developer",
        "not applicable",
        "not applicable",
        "not-applicable-news",
        "not applicable",
        [],
        0.0,
        0.0,
        0.0,
        0.0,
        [],
    ),
    eid("forum_post", "forum-interview-1"): (
        False,
        "wpf-developer",
        "not applicable",
        "not applicable",
        "not-applicable-tutorial",
        "not applicable",
        [],
        0.0,
        0.0,
        0.0,
        0.0,
        [],
    ),
    eid("forum_post", "forum-student-alex2"): (
        # Relevant (on-topic, real pain) but genuinely educational -- a beginner
        # build error, not a gap a product would be built to fill. Exercises
        # critic objection `educational_not_commercial` as a *second*, distinct
        # rejection reason from the viral cluster's `single_viral_source_dominant`.
        True,
        "wpf-developer",
        "hit a common beginner build error in a legacy WPF project",
        "namespace-not-found build error after a dependency change",
        EDUCATIONAL_PROBLEM_KEY,
        "understand why the error happens",
        [],
        0.4,
        0.3,
        0.0,
        0.0,
        [],
    ),
    eid("reddit_post", "reddit-student-priya2"): (
        True,
        "wpf-developer",
        "hit the same build error after upgrading packages",
        "namespace-not-found build error, explicitly wants to learn not buy a tool",
        EDUCATIONAL_PROBLEM_KEY,
        "a clear tutorial explaining the cause",
        [],
        0.3,
        0.2,
        0.0,
        0.0,
        [],
    ),
}


def classification_output(evidence_id: str) -> dict:
    (
        relevant,
        persona,
        situation,
        problem_statement,
        problem_key,
        desired_outcome,
        workarounds,
        pain,
        urgency,
        commercial_intent,
        commitment,
        tools,
    ) = _CLASSIFICATION_TABLE[evidence_id]
    return {
        "schema": "demand-radar.classification/1",
        "evidence_id": evidence_id,
        "relevance": {
            "product_fit": 0.9 if relevant else 0.1,
            "confidence": 0.85,
            "relevant": relevant,
        },
        "persona": {"label": persona},
        "situation": {"statement": situation},
        "problem": {"statement": problem_statement, "problem_key": problem_key},
        "desired_outcome": {"statement": desired_outcome},
        "current_workarounds": workarounds,
        "signals": {
            "pain": pain,
            "urgency": urgency,
            "commercial_intent": commercial_intent,
            "commitment": commitment,
        },
        "mentioned_tools": tools,
        "evidence_spans": [{"start": 0, "end": 10, "supports": "problem"}],
    }


def classify_scenarios() -> dict[str, FakeScenario]:
    return {
        f"classify:{evidence_id}": FakeScenario(
            kind="success", output=classification_output(evidence_id)
        )
        for evidence_id in _CLASSIFICATION_TABLE
    }


_OPPORTUNITY_CANDIDATES: dict[str, dict] = {
    STRONG_PROBLEM_KEY: {
        "problem": {
            "statement": "Legacy WPF/.NET teams can't get an ownership-path trace for memory "
            "leaks -- every tool shows retained objects, never who holds the reference."
        },
        "persona": {"primary": "legacy .NET / WPF maintainer"},
        "context": {"situation": "maintaining a large, long-lived WPF/.NET Framework codebase"},
        "current_workarounds": [
            "custom Roslyn analyzer to trace ownership manually",
            "manual ownership tracing by hand, taking days",
            "internal scripts to bisect ownership",
            "manual pre-release subscription-review checklist",
        ],
        "existing_substitutes": ["dotMemory", "ANTS", "NDepend", "Roslyn"],
        "possible_wedges": [
            {
                "type": "cli",
                "offer": "a static analyzer that reports the ownership path, not just "
                "retained objects",
            },
            {
                "type": "concierge_service",
                "offer": "a paid audit that manually traces ownership chains for a "
                "client's WPF app",
            },
        ],
        "risks": [
            "small sample skews toward maintainers already vocal online",
            "unclear how many would pay vs. keep using free manual workarounds",
        ],
        "cheapest_experiment": {
            "hypothesis": "WPF maintainers will pay for an ownership-path report on a real leak",
            "input": "one real WPF solution with a known leak",
            "output": "a written ownership-path report delivered manually (no tool built yet)",
            "commitment_event": "the maintainer pays for the report or commits to a pilot",
            "success_threshold": "2 of 5 contacted maintainers pay for a report",
            "failure_threshold": "0 of 5 will pay anything",
        },
    },
    WEAK_PROBLEM_KEY: {
        "problem": {
            "statement": "Large WPF views with many bound properties have no way to profile "
            "which PropertyChanged bindings are the performance hot path."
        },
        "persona": {"primary": "WPF developer on a data-heavy view"},
        "context": {"situation": "a WPF grid/view with thousands of bound properties"},
        "current_workarounds": ["stopwatch-based logger wrapped around each property setter"],
        "existing_substitutes": [],
        "possible_wedges": [
            {
                "type": "plugin",
                "offer": "a profiler overlay ranking PropertyChanged bindings by cost",
            },
        ],
        "risks": [
            "only two independent reports so far",
            "workaround may be good enough for most teams",
        ],
        "cheapest_experiment": {
            "hypothesis": "WPF developers want a ranked view of expensive bindings",
            "input": "a sample WPF app with a known PropertyChanged bottleneck",
            "output": "a one-page ranked list of the costliest bindings",
            "commitment_event": "a developer asks to run it on their own app",
            "success_threshold": "1 of 3 asks to run it on their own codebase",
            "failure_threshold": "none ask",
        },
    },
    VIRAL_PROBLEM_KEY: {
        "problem": {"statement": "People joke about WPF memory leaks online."},
        "persona": {"primary": "WPF developer venting online"},
        "context": {"situation": "a single popular, humorous complaint post"},
        "current_workarounds": [],
        "existing_substitutes": ["dotMemory"],
        "possible_wedges": [{"type": "other", "offer": "none -- no real ask in the evidence"}],
        "risks": ["single source", "no expressed willingness to pay or even a concrete ask"],
        "cheapest_experiment": {
            "hypothesis": "this is entertainment, not a commercial opportunity",
            "input": "n/a",
            "output": "n/a",
            "commitment_event": "n/a",
            "success_threshold": "n/a",
            "failure_threshold": "n/a",
        },
    },
    EDUCATIONAL_PROBLEM_KEY: {
        "problem": {
            "statement": "Developers hit a namespace-not-found build error in legacy WPF "
            "projects and want to understand the cause."
        },
        "persona": {"primary": "developer learning a legacy WPF codebase"},
        "context": {"situation": "a build breaks after a dependency/package change"},
        "current_workarounds": [],
        "existing_substitutes": ["general C# tutorials and forum search"],
        "possible_wedges": [{"type": "other", "offer": "a tutorial/explainer, not a paid tool"}],
        "risks": ["explicitly educational intent stated by both sources, not a commercial ask"],
        "cheapest_experiment": {
            "hypothesis": "this is a learning need, not a commercial opportunity",
            "input": "n/a",
            "output": "n/a",
            "commitment_event": "n/a",
            "success_threshold": "n/a",
            "failure_threshold": "n/a",
        },
    },
}


def opportunity_scenarios_by_cluster_id(
    cluster_id_for_key: dict[str, str],
) -> dict[str, FakeScenario]:
    return {
        f"generate_opportunity:{cluster_id_for_key[key]}": FakeScenario(
            kind="success", output=candidate
        )
        for key, candidate in _OPPORTUNITY_CANDIDATES.items()
        if key in cluster_id_for_key
    }


def critic_scenario_for(opportunity_id: str, *, problem_key_hint: str) -> FakeScenario:
    if problem_key_hint == STRONG_PROBLEM_KEY:
        return FakeScenario(
            kind="success",
            output={
                "schema": "demand-radar.critic-verdict/1",
                "opportunity_id": opportunity_id,
                "recommended_status": "experiment_ready",
                "objections": [
                    {
                        "code": "implementation_cost_dominates",
                        "statement": "a full ownership-tracing analyzer is a substantial build",
                        "evidence_ids": [],
                        "fatal": False,
                    }
                ],
                "overclaim_check": {
                    "overclaims": False,
                    "statement": "claims match the cited evidence",
                },
                "notes": "nine independent sources across five source families, one explicit "
                "commitment signal; non-fatal cost concern only",
            },
        )
    if problem_key_hint == WEAK_PROBLEM_KEY:
        return FakeScenario(
            kind="success",
            output={
                "schema": "demand-radar.critic-verdict/1",
                "opportunity_id": opportunity_id,
                "recommended_status": "investigate",
                "objections": [
                    {
                        "code": "single_viral_source_dominant",
                        "statement": "only two independent reports so far",
                        "evidence_ids": [],
                        "fatal": False,
                    }
                ],
                "overclaim_check": {
                    "overclaims": False,
                    "statement": "modest claims, matches evidence",
                },
                "notes": "real signal but below independence thresholds today",
            },
        )
    if problem_key_hint == VIRAL_PROBLEM_KEY:
        return FakeScenario(
            kind="success",
            output={
                "schema": "demand-radar.critic-verdict/1",
                "opportunity_id": opportunity_id,
                "recommended_status": "rejected",
                "objections": [
                    {
                        "code": "single_viral_source_dominant",
                        "statement": "the entire cluster is one popular post with no "
                        "independent corroboration",
                        "evidence_ids": [],
                        "fatal": True,
                    },
                    {
                        "code": "complaint_not_purchase_intent",
                        "statement": "the post is humorous venting, not a request or "
                        "purchase signal",
                        "evidence_ids": [],
                        "fatal": True,
                    },
                ],
                "overclaim_check": {
                    "overclaims": True,
                    "statement": "no real product ask exists in the evidence",
                },
                "notes": "reject -- single source, no commercial intent",
            },
        )
    # EDUCATIONAL_PROBLEM_KEY: rejected for a distinct reason from the viral
    # cluster above -- both sources explicitly say they want to learn, not buy.
    return FakeScenario(
        kind="success",
        output={
            "schema": "demand-radar.critic-verdict/1",
            "opportunity_id": opportunity_id,
            "recommended_status": "rejected",
            "objections": [
                {
                    "code": "educational_not_commercial",
                    "statement": "both sources explicitly ask to understand the error, "
                    "not to buy a fix",
                    "evidence_ids": [],
                    "fatal": True,
                },
            ],
            "overclaim_check": {
                "overclaims": False,
                "statement": "candidate does not overclaim, it is simply not commercial",
            },
            "notes": "reject -- educational intent stated directly by the evidence, "
            "not a product gap",
        },
    )
