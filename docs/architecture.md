# Architecture

## Data flow

```text
public evidence (JSONL / RSS)
  -> ingest/jsonl.py, ingest/rss.py      (Zone 1 — no agent, no shell)
  -> normalize_text + content_hash        (deterministic, at ingest time)
  -> SQLite evidence store                (storage/sqlite.py)
  -> LangGraph pipeline (graph/build.py):
       load_run -> load_new_evidence -> normalize -> deduplicate
       -> classify -> propose_clusters -> verify_clusters -> score_clusters
       -> generate_opportunities -> critic_review -> deterministic_judge
       -> render_report -> verify_run
  -> runs/<product>/<run-id>/            (canonical run artifacts)
```

Everything up through `deduplicate` is pure/deterministic and requires no
model (spec Step 3). `classify`, `generate_opportunities`, and
`critic_review` are the only three nodes that call an agent
(`propose_clusters` optionally calls one for labeling, with a fully
deterministic fallback — see [Clustering](#clustering) below).
`deterministic_judge`, `render_report`, and `verify_run` are deterministic
again: nothing after `critic_review` ever calls a model.

## LangGraph state

`graph/state.py::DemandState` is the spec's field set verbatim:

```python
class DemandState(TypedDict):
    run_id: str
    product: str
    since: str | None
    input_evidence_ids: list[str]
    accepted_evidence_ids: list[str]
    duplicate_evidence_ids: list[str]
    classifications: list[str]
    cluster_ids: list[str]
    opportunity_ids: list[str]
    analyst_run_id: str | None
    critic_run_id: str | None
    verdict: str | None
    errors: Annotated[list[dict[str, Any]], operator.add]
```

Every field except `errors` is written by exactly one node (plain replace
semantics); `errors` accumulates across nodes via LangGraph's `operator.add`
reducer, so one node's failures never clobber another's.

Everything a node needs that must **not** be part of the checkpointed state
— the live `Store` connection, the analyst/critic `AgentRunner` instances,
the resolved `ProductConfig`, the run's artifact directory, the run's
captured `now` — travels separately as `RunContext`, passed through
`config["configurable"]["ctx"]`. None of it is JSON-serializable or safe to
replay from a checkpoint; conflating it with `DemandState` would make the
checkpoint itself un-replayable across process restarts.

### A real bug this surfaced (worth knowing before touching state fields)

LangGraph's `LastValue` channel (the default, non-reducer channel type)
special-cases deserialization: `from_checkpoint` only restores a value if
the persisted checkpoint value `is not None` — a channel whose current value
is exactly `None` deserializes as **unset**, not as `None`. Concretely: a
run with `--since` omitted keeps `state["since"] = None` for the entire run;
after a crash-and-resume, `state["since"]` is **absent** from the
dict handed to later nodes, and a bare `state["since"]` subscript raises
`KeyError`. This bit `since`, `analyst_run_id`, `critic_run_id`, and
`verdict` (initial `initial_state()` may set the first three to `None`).
The fix used throughout: read these fields with `state.get(...)`, never
`state[...]`, in any node that might run after a resume. See
`tests/integration/test_resume.py` for the regression test and
`docs/decisions.log.md` for when this was found.

## Why LangGraph (spec section 12.3)

- **Persistent state** — every node's output is checkpointed to SQLite
  (`langgraph-checkpoint-sqlite`), not held only in a Python variable.
- **Resumability** — `graph/build.py::run_or_resume` checks
  `compiled.get_state(config).values`; if truthy, it resumes with
  `invoke(None, config)` and LangGraph restores exactly the fields already
  written, skipping every already-completed node. If empty, it's a fresh
  `invoke(initial_state, config)`. Proven in
  `tests/integration/test_resume.py`: a node is made to raise on its first
  call, the run crashes, and a **second run with entirely new `Store`/
  `AgentRunner` instances** (simulating a real process restart, not just
  continuing in the same Python objects) picks up from the checkpoint and
  reaches the same correct final result.
- **Explicit transitions** — the 13 nodes are wired as one straight line
  (`graph/build.py::NODE_ORDER`), no hidden control flow.
- **Deterministic/agentic separation** — the boundary is structural, not a
  convention: only `classify`, `generate_opportunities`, `critic_review`,
  and (optionally, with a deterministic fallback) `propose_clusters` import
  an `AgentRunner`; every other node file imports none.
- **Errors as run state** — `state["errors"]` is inspectable at any point
  (mid-run from a checkpoint, or after completion) rather than living only
  in a log line, and it feeds directly into `verification.py`'s
  `analyst_status`/`critic_status` derivation.

This is a straight line, not a DAG, because no branch has been justified by
an actual run yet — the same reasoning 007's own
`docs/workflow-scripting.md` ADR uses to scope its (proposed, unbuilt)
`o7 workflow` down to a flat step list: "building a DAG for a need that
hasn't appeared yet is speculative generality." If a future case needs
conditional routing, add it then, against a real run record.

## Deterministic/agentic boundary

| Layer | Nodes / modules | Calls a model? |
|---|---|---|
| Ingestion | `ingest/jsonl.py`, `ingest/rss.py`, `ingest/normalize.py` | No |
| Pipeline: structural | `load_run`, `load_new_evidence`, `normalize`, `deduplicate` | No |
| Pipeline: clustering | `propose_clusters` (naming/merge only), `verify_clusters`, `score_clusters` | `propose_clusters` optionally; deterministic fallback always available |
| Pipeline: agentic | `classify`, `generate_opportunities`, `critic_review` | Yes |
| Pipeline: final | `deterministic_judge`, `render_report`, `verify_run` | No |
| Scoring | `scoring/demand_score.py`, `scoring/independence.py`, `scoring/judge.py` | No |
| Reporting | `reporting/markdown.py`, `reporting/json_report.py` | No |

### The candidate-to-card assembly boundary

The analyst is never asked for, and structurally cannot return, a `status`,
`demand_score`, `confidence`, or evidence count. `agents/prompts/
opportunity.py::OPPORTUNITY_CANDIDATE_SCHEMA` is a deliberate **subset** of
`schemas/opportunity-card.schema.json` — problem/persona/context/
workarounds/substitutes/wedges/risks/cheapest_experiment only.
`graph/nodes/agents.py::_assemble_opportunity_card` builds the real
`OpportunityCard` by combining that candidate dict with code-computed
fields (evidence stats from the cluster, `demand_score` from
`scoring/demand_score.py`, `confidence`, and `status="observed"` as a
placeholder) — it reads named fields out of the candidate dict, so even a
candidate dict that somehow carried an extra `status` key would have it
ignored, not merged in (see
`tests/unit/test_prompt_injection.py::test_assembled_card_ignores_any_status_the_candidate_dict_might_carry`).
`scoring/judge.py::judge_opportunity` is the **only** place that sets the
final `status`, after `critic_review` has run.

Similarly, `graph/nodes/clustering.py::propose_clusters` asks the analyst
only to label and possibly merge **problem_key strings** it already
extracted deterministically — the model never sees or returns an
`evidence_id` in that call, so it cannot fabricate or drop one. Membership
is only ever expanded from the real `candidate_groups` mapping built before
the call.

## Clustering

Order matches spec section 19's preferred sequence:

1. **Deterministic candidate grouping** — canonical (`relevant=true`)
   classifications are grouped by exact `problem_key` match.
2. **Agent-assisted naming/merge proposal** — one call (not per-cluster) to
   the analyst, given each candidate group's representative problem
   statements, asking it to label each group and optionally propose merging
   groups that describe the literal same problem. If this call fails for
   any reason (`BLOCKED_*`, malformed JSON, references an unknown
   `problem_key`), `propose_clusters` falls back to one cluster per
   `problem_key` with a slug-derived label — clustering never blocks on the
   model.
3. **Deterministic membership validation** — `verify_clusters` expands each
   cluster to include duplicate-linked members of its canonical evidence
   (looked up from `duplicate_links`, never from the model), drops any
   member id that doesn't exist, and recomputes `independence` from the
   corrected membership.

`score_clusters` then computes `ClusterSignals` from classifications alone:
`frequency` (canonical member count, capped — see `docs/scoring.md`),
`growth` (fraction of canonical members published in the more recent half
of the whole accepted-evidence date range), `pain`/`urgency`/
`commercial_intent`/`product_fit` (means across canonical members),
`commitment` (max — a single explicit signal shouldn't be diluted by
averaging with members who show none), `saturation` (distinct
`mentioned_tools` count, capped).

## Storage

One SQLite file (`storage/migrations.py` for schema, `storage/sqlite.py`
for the `Store` class). Every row keeps its full canonical JSON in a `data`
column; flat columns exist only for indexing/joins (product, hashes,
foreign keys). Tables: `evidence_items`, `duplicate_links`, `runs`,
`classifications`, `problem_clusters` + `cluster_members`,
`opportunity_cards` + `opportunity_clusters`, `critic_verdicts`,
`schema_migrations`. `evidence_items` is keyed so that
`(product, source_kind, source_id)` — and therefore the deterministically
derived `id` — collides on re-import (`INSERT OR IGNORE`), which is the
whole idempotency guarantee in acceptance 23.1.

LangGraph's own checkpoint state lives in a **separate** SQLite file,
`runs/<product>/<run-id>/checkpoints.sqlite` — a deliberate, minor deviation
from the spec's illustrative run-artifact tree (section 20 doesn't name it,
but "структура может быть скорректирована" as long as boundaries stay
explicit). `state.json` in the same directory is a human-readable dump of
the *final* `DemandState`, written once by the CLI after the run completes
— it is not itself the resumability mechanism.

## Runner interface

`agents/base.py::AgentRunner` is a `Protocol` (exact spec section 13
signature). Four implementations share it:

- `agents/fake.py::FakeRunner` — required for all tests (spec 13.3);
  scenarios keyed by `task_id` simulate success, timeout, auth failure,
  usage-limit, malformed JSON, and schema violation.
- `agents/claude_cli.py::ClaudeCodeRunner` — shells out to the
  already-authenticated `claude` CLI, closed-world (`--tools ""`,
  `--strict-mcp-config`, empty `--setting-sources`, `--no-session-persistence`),
  prompt via stdin.
- `agents/codex_cli.py::CodexCliRunner` — shells out to `codex exec`,
  `-s read-only` + `-c features.shell_tool=false`, `OPENAI_API_KEY`/
  `CODEX_API_KEY` stripped from the subprocess environment. Flags
  unverified against a real install (`codex` is not installed in the
  environment this was built in) — see `docs/decisions.log.md` and
  `docs/trust-boundaries.md`.

Every call goes through `agents/base.py::call_agent_with_retry`, which
retries **only** on `BLOCKED_TIMEOUT` (spec 12.2: "malformed structured
output is not a transient error"), up to `max_retries` (default 2) extra
attempts, same `task_id` every attempt (so a FakeRunner scenario lookup
still matches) but a fresh `run_dir/attempt-N` subdirectory per attempt.
`persist_call_artifacts` writes `prompt.txt` + `meta.json` (the full
`AgentResult`) alongside whatever the runner itself wrote to
`stdout.jsonl`/`stderr.log`/`result.json`.

A `BLOCKED_AUTH`/`BLOCKED_USAGE` result aborts the **rest of that node's**
remaining calls immediately (no point burning more calls against broken
auth); any other failure is scoped to just that one item, and the loop
continues — partial results are real results, recorded in `state["errors"]`,
never hidden.

## Run artifacts

```text
runs/<product>/<run-id>/
  task.yaml                 # run parameters, written before the graph starts
  state.json                # final DemandState, written after it completes
  input-manifest.json        # sorted input_evidence_ids + hash inputs
  checkpoints.sqlite          # LangGraph's own checkpoint store
  evidence/
    accepted.jsonl
    duplicates.jsonl
  agents/
    analyst/
      classify/<evidence_id>/attempt-N/{prompt.txt,stdout.jsonl,stderr.log,result.json,meta.json}
      generate_opportunities/<cluster_id>/attempt-N/...
      propose_clusters/attempt-N/...
    critic/
      critic_review/<opportunity_id>/attempt-N/...
  outputs/
    classifications.jsonl
    clusters.json
    opportunities.json
    critic-verdicts.json
    report.md
  verification.json
```

The nested per-call subdirectories under `agents/` are the one other
deliberate deviation from spec section 20's illustration, which shows a
single flat `agents/analyst/` directory — that shape implicitly assumes one
analyst call per run. This pipeline calls the analyst once per evidence
item (`classify`) and once per cluster (`generate_opportunities`), and the
critic once per opportunity, each independently retryable and each
deserving its own full provenance record; flattening them into one
directory would mean later calls overwrite earlier ones' `stdout.jsonl`.
Documented here and in `docs/decisions.log.md` rather than silently
diverging.
