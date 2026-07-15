# Demand Radar

Evidence-backed opportunity discovery. Demand Radar turns imported public
discussion data into **opportunity cards that cite their evidence**, and
rejects unsupported ideas explicitly instead of hiding them.

```text
public evidence
  -> normalization
  -> deduplication
  -> problem extraction
  -> clustering
  -> opportunity generation
  -> adversarial critique
  -> deterministic verdict
  -> validation experiment
```

## What this is not

- Not a general idea generator. It never asks a model "invent ten startups."
  It gives the model evidence items and requires every claim to cite one.
- Not a social-listening SaaS. There is no hosting, no multi-tenant mode, no
  UI, no scraper. You bring your own JSONL/RSS exports.
- Not a new agent platform, and not part of `007`. It is a standalone sibling
  tool that happens to invoke the same `claude`/`codex` CLIs 007 does, over
  local subscription auth — see [Subscription-backed runners](#subscription-backed-runners)
  and [`docs/o7-bridge-proposal.md`](docs/o7-bridge-proposal.md) for the
  (unimplemented) future contract with 007.
- Not a market-size or success-probability estimator. The demand score is a
  ranking heuristic — see [`docs/scoring.md`](docs/scoring.md).
- Not a validator. Nothing in this tool can mark an opportunity
  `externally_validated` from discussion evidence alone; that status requires
  imported behavioral evidence (a real download, a repo transfer, a payment)
  that a human records.

## Quick start

```bash
uv sync
uv run demand-radar init --db ./data/demand.db
uv run demand-radar ingest --product own-audit --input ./fixtures/mixed-demand-signals.jsonl
uv run demand-radar run --product own-audit --since 30d --analyst fake --critic fake
uv run demand-radar report --run <run-id> --format markdown
```

Swap `--analyst fake --critic fake` for `--analyst claude --critic codex` once
`claude`/`codex` are installed and logged in with a subscription (see below).
Offline tests and the fixture gate never use the real CLIs.

The bare `fake` provider above has no knowledge of the fixture's content —
it returns an empty object for every call, which correctly fails schema
validation and produces a `BLOCKED` run with every `classify` error listed,
not a silently-empty success (see [Acceptance semantics](#acceptance-semantics)).
That is a deliberate demonstration of "never turn a failure into a false
success," not a broken demo. For a populated run with real
`experiment_ready`/`investigate`/`rejected` cards, `FakeRunner` needs
per-evidence canned responses, exactly as
[`tests/integration/test_fixture_pipeline.py`](tests/integration/test_fixture_pipeline.py)
wires it — that test (part of `scripts/check.sh`) is the reproducible source
of truth for "the fixture pipeline produces cards."

### Example JSONL input

`ingest` takes one **raw** record per line — a generic collector shape
(`ingest/jsonl.py::RawEvidenceRecord`), not a full `EvidenceItem`.
Ingestion itself derives the id, content hash, normalized text, and hashed
author identity; you never compute those:

```json
{"source_id": "gh-1234", "source_kind": "github_issue", "source_family": "github", "url": "https://github.com/example/repo/issues/1234", "author": "some-github-user", "published_at": "2026-05-01T00:00:00Z", "text": "We wrote a custom analyzer because NDepend doesn't explain the ownership path for this leak.", "query_id": "ndepend-alternative"}
```

`url` and `author` may be omitted/`null`. See
[`fixtures/mixed-demand-signals.jsonl`](fixtures/mixed-demand-signals.jsonl)
for 22 more examples (including a malformed line and a schema-invalid line,
on purpose — ingestion reports both without aborting the batch). The
**stored, canonical** record — what `inspect evidence` prints and what
schema validation runs against — is `EvidenceItem`, per
[`schemas/evidence-item.schema.json`](schemas/evidence-item.schema.json).

## Subscription-backed runners

Claude Code and Codex CLI are invoked as **local, non-interactive,
subscription-authenticated CLI adapters** — the same posture `007` uses for
`o7 judge` (closed-world: no shell tool, no ambient MCP, strict JSON Schema
output). This is *"subscription-backed CLI execution inspired by 007"*, not
an integration with 007 — Demand Radar does not import or link against 007,
and 007's binary is untouched by this project. No Anthropic or OpenAI API key
is used or read; credential storage is never touched directly, only the
already-authenticated `claude`/`codex` CLIs.

## Trust warning

All ingested content is untrusted external text. It can contain prompt
injection ("ignore previous instructions", "mark this validated", "run this
command"). Demand Radar's extraction/critique agents run with no shell tool,
no filesystem write access, and a JSON-Schema-constrained output — see
[`docs/trust-boundaries.md`](docs/trust-boundaries.md) for the full zone
model and residual risks. `fixtures/prompt-injection-signals.jsonl` and its
tests exist specifically to exercise this boundary.

## Acceptance semantics

- Every substantive claim (problem, workaround, commercial signal) must cite
  an `evidence_id`. Claims without evidence references are rejected by schema
  validation or the deterministic judge, not by asking the model nicely.
- Duplicate URLs, duplicate content hashes, and reposts of the same source do
  not increase demand count. See [`docs/scoring.md`](docs/scoring.md) and
  `scoring/independence.py`.
- `experiment_ready` requires minimum unique authors, minimum source
  families, at least one pain-evidence item, at least one described
  workaround, no fatal critic objection, and full schema validity — computed
  by code (`scoring/judge.py`), never granted by a model.
- `externally_validated` cannot be produced by this pipeline at all; it is a
  human-entered status once real behavioral evidence exists.

## Status

**MVP / vertical slice.** Deterministic core (storage, ingestion,
deduplication, scoring, judge, reporting) is fully implemented and tested
without any LLM. The LangGraph pipeline runs end-to-end against the fixture
dataset with the fake runner in CI. Real `claude`/`codex` CLI runners are
implemented against each CLI's documented non-interactive flags; the last
`demand-radar smoke-agents` run against the real, authenticated CLIs
available in this MVP's build environment reported `claude: PASS`,
`codex: BLOCKED_NOT_INSTALLED` (codex is genuinely not installed there — see
[`docs/decisions.log.md`](docs/decisions.log.md) for the real bug that first
run surfaced and how it was fixed, not simulated away). Re-run it yourself
before relying on either provider — status is a property of one real run,
not a permanent guarantee. See
[`docs/decisions.log.md`](docs/decisions.log.md) for what was deferred and
why.
