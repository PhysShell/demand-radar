#!/usr/bin/env python3
"""One-off experiment script -- Phase 2A historical replay, NOT a new
execution mode. Does not touch cli.py / graph/build.py / the production
LangGraph pipeline.

Why this exists: the corpus census (export_existing_corpus.py) found 0
problem_evidence records and 43 corpus census observations -- NOT 43
uniform "technical findings": a mix of confirmed_tp, false_positive,
review_pending, one aggregate_validation rerun summary, ci_run_metadata
(a workflow ran, nothing more), and cited_origin_unverified entries (see
docs/trials/historical-corpus-replay.md). The production `demand-radar run`
graph has nothing to cluster (0 EvidenceItems), and its judge caps every
card at "investigate" without a real second-provider critic (Codex is
forbidden for untrusted content per the Codex-freeze). Per the Phase 2A
instruction -- "если текущий contract требует независимого critic... и
такого режима нет, остановись после analyst output и сформируй PARTIAL
report; не добавляй новый execution mode" -- this script is that stop: a
single real Claude call (via the same O7InvokeRunner/`o7 invoke` primitive
Demand Radar's production analyst uses, engine=claude only, never codex)
over the corpus-observations manifest, asking whether real, evidence-backed
problem clusters emerge from the confirmed_tp observations specifically --
while being graded on whether it confuses a technical finding with
commercial demand, which is the whole point of this replay.

Output is deliberately NOT the `demand-radar.opportunity-card/1` or
`demand-radar.problem-cluster/1` schema (schemas/opportunity-card.schema.json,
problem-cluster.schema.json) -- a different, one-off shape below, so it can
never be mistaken for a real card or a finished verdict. Nothing here is
"experiment_ready"; the schema does not even have that field.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from demand_radar.agents.base import READ_ONLY_DATA_PROFILE  # noqa: E402
from demand_radar.agents.o7_invoke import O7InvokeRunner  # noqa: E402
from demand_radar.agents.prompts.trust import TRUST_PREAMBLE, wrap_untrusted  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "data" / "live" / "historical-replay-corpus-observations.json"
RUN_DIR = REPO_ROOT / "runs" / "historical-replay" / "technical-prevalence-extraction"

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "historical-replay-preliminary-technical-cluster-review (NOT a production schema)",
    "type": "object",
    "additionalProperties": False,
    "required": ["preliminary_clusters", "extraction_quality_self_check"],
    "properties": {
        "preliminary_clusters": {
            "type": "array",
            "maxItems": 15,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "label",
                    "member_repos",
                    "technical_pattern",
                    "commercial_status",
                    "reasoning",
                ],
                "properties": {
                    "label": {"type": "string", "maxLength": 120},
                    "member_repos": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "technical_pattern": {"type": "string", "maxLength": 600},
                    "commercial_status": {
                        "type": "string",
                        "enum": [
                            "no_commercial_signal_available",
                            "would_require_independent_problem_evidence",
                        ],
                        "description": (
                            "Never 'experiment_ready' or similar -- this replay has no "
                            "problem_evidence, only technical_prevalence, so no cluster "
                            "here can honestly claim commercial signal."
                        ),
                    },
                    "reasoning": {"type": "string", "maxLength": 800},
                },
            },
        },
        "extraction_quality_self_check": {
            "type": "object",
            "additionalProperties": False,
            "required": ["did_not_overclaim", "notes"],
            "properties": {
                "did_not_overclaim": {
                    "type": "boolean",
                    "description": (
                        "true only if every cluster's commercial_status is honestly one "
                        "of the two allowed values and reasoning never treats "
                        "analyzer-finding prevalence as user demand."
                    ),
                },
                "notes": {"type": "string", "maxLength": 1000},
            },
        },
    },
}

TASK_INSTRUCTIONS = """You are reviewing a corpus census for Demand Radar's Phase 2A \
"Historical External Evidence Replay". The fenced block below is the full JSON \
manifest.

Every entry in `corpus_observations` is a finding from Own.NET's own static \
analyzer running against real, named, public open-source .NET/C# repositories \
(or a corpus regression case citing a real external issue/PR as a pattern's \
origin). NONE of these are a person voicing a problem, filing a feature \
request, or expressing willingness to pay -- at most they prove a technical \
pattern is real and recurring across real codebases.

Each entry's `category` field means exactly one of:
- confirmed_tp: a human independently verified this against the real source --
  the strongest evidence tier here.
- false_positive: Own.NET's own reviewer determined this was NOT a real bug.
  Do not cite these as evidence a pattern is real; they are evidence of the
  analyzer's error rate, nothing else.
- review_pending: flagged but never independently confirmed either way --
  weaker than confirmed_tp, do not treat as settled.
- aggregate_validation: a rerun re-confirming earlier rows on repos already
  represented elsewhere in the manifest -- not a new observation about a new
  repo.
- ci_run_metadata: proves only that a CI workflow executed against that repo
  name -- carries no finding-count or verdict information at all.
- cited_origin_unverified: a real external issue/PR URL that this replay
  never re-fetched -- treat the citation as real but the paraphrase as
  secondhand.

Task: propose preliminary problem clusters ONLY from confirmed_tp entries,
optionally with review_pending as supporting color you label as unconfirmed --
NEVER build a cluster's technical claim on false_positive, ci_run_metadata, or
aggregate_validation entries, since those do not establish the pattern is
real. Propose a cluster ONLY if the technical pattern is specific and \
recurring enough that a human might reasonably want to \
investigate it further as a possible product angle -- NOT because the pattern \
exists (it does, that's a given for confirmed_tp), but because the SHAPE of \
the evidence (e.g. repeated across independent repos/maintainers, non-trivial \
to fix, already causing real triage/PR effort) makes it worth \
a human's time to go look for actual commercial signal (real complaints, \
real willingness to pay) elsewhere.

Every cluster's `commercial_status` MUST be either \
"no_commercial_signal_available" or "would_require_independent_problem_evidence" \
-- never invent a stronger claim. This manifest cannot support "experiment_ready" \
or anything resembling it: there is no user complaint anywhere in it, only \
analyzer output. If you cannot honestly find any cluster worth flagging, \
return an empty preliminary_clusters array -- that is a valid, useful answer, \
not a failure.

Finish with an honest self-check: did you avoid treating "this bug pattern is \
real and recurring" as if it were "customers want a product for this"? Those \
are different claims and only the corpus can speak to the first one.
"""


def build_prompt(manifest_text: str) -> str:
    return (
        TRUST_PREAMBLE
        + "\n\n"
        + TASK_INSTRUCTIONS
        + "\n"
        + wrap_untrusted("historical-replay-corpus-observations-manifest", manifest_text)
    )


def main() -> int:
    if not MANIFEST_PATH.is_file():
        print(
            f"missing {MANIFEST_PATH} -- run scripts/export_existing_corpus.py first",
            file=sys.stderr,
        )
        return 1

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    schema_path = RUN_DIR / "output-schema.json"
    schema_path.write_text(json.dumps(OUTPUT_SCHEMA, indent=2), encoding="utf-8")

    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    prompt = build_prompt(manifest_text)

    runner = O7InvokeRunner(
        engine="claude"
    )  # engine=claude only -- codex stays forbidden for untrusted content
    result = runner.run(
        task_id="historical-replay-technical-prevalence-extraction",
        prompt=prompt,
        # input_paths is a hash-only provenance record in this architecture (o7 invoke
        # hashes the file, it does not attach its content) -- the manifest content itself
        # is embedded directly in `prompt` above via wrap_untrusted, same as every
        # production analyst/critic node in graph/nodes/agents.py.
        input_paths=[MANIFEST_PATH],
        output_schema=schema_path,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=RUN_DIR,
    )

    result_path = RUN_DIR / "result.json"
    result_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        f"status={result.status} schema_valid={result.schema_valid} error_kind={result.error_kind}"
    )
    print(f"wrote: {result_path}")
    if result.structured_output_path:
        print(f"structured output: {result.structured_output_path}")
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
