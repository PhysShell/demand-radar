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
- Not a new agent platform, and its own pipeline/scoring/storage code is not
  part of `007`. It *does* depend at runtime on a formal runner contract
  (`docs/runner-contract.md`) for the one thing a runner does better than a
  second copy of that logic would: turning `(prompt, schema,
  capability_profile)` into an `AgentResult`. `--runner o7` — `agents/
  o7_invoke.py` shelling out to 007's `o7 invoke` for both `claude` and
  `codex` — is the *default*, not the only legal implementation: any
  adapter that implements the `AgentRunner` Protocol and passes
  `tests/contract/test_runner_contract.py` is a legitimate `--runner`. See
  [Runners](#runners) and [`docs/o7-invoke.md`](docs/o7-invoke.md) for the
  reference implementation and the cross-repo conformance gate that keeps
  it and 007 honest about it.
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

Swap `--analyst fake --critic fake` for `--analyst claude --critic human`
(deferred human review) once `claude` is installed and logged in with a
subscription, **and** a built `o7` binary is on `PATH` (sibling `007` repo,
`cargo build`, or the pinned nix dev shell — see
[`docs/nix-dev-shell.md`](docs/nix-dev-shell.md)). `--critic codex` is
currently refused outright — its isolation profile is unverified, see
[Runners](#runners) and the trust warning below. Offline tests and the
fixture gate never use the real CLIs or require `o7` at all.

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

## Runners

`demand-radar run` has two independent axes: **engine**
(`--analyst`/`--critic`: `fake`/`claude`/`codex` — which model generates or
critiques a card) and **runner** (`--runner`: `o7`, default — how that
engine is actually invoked). The pipeline's real dependency is the
`AgentRunner` contract those runners implement, not any one binary — see
[`docs/runner-contract.md`](docs/runner-contract.md) for the normative
Protocol, the status taxonomy, and how a new runner gets added and
verified. `007` is the default because it is the one implementation this
project has actually built and exercised against live CLIs, not because
the contract requires it.

The default `o7` runner invokes Claude Code and Codex CLI as **local,
non-interactive, subscription-authenticated CLI adapters**, via 007's
`o7 invoke` primitive (`007/src/invoke.rs`) — the same closed-world posture
`007` uses for `o7 judge` (no shell tool, no ambient MCP, schema-constrained
output), generalized to an arbitrary prompt/schema instead of judge's own
hardcoded verdict shape. This *is* a real runtime dependency on 007 for
that runner (a built `o7` binary must be on `PATH`), not merely "inspired
by" it — `agents/o7_invoke.py` shells out to `o7 invoke` and holds no
closed-world flag knowledge of its own; that all lives in 007 now
(`007/src/invoke.rs`, not Python). See [`docs/o7-invoke.md`](docs/o7-invoke.md)
for what changed and why, and [`docs/nix-dev-shell.md`](docs/nix-dev-shell.md)
for getting a pinned `o7` build via the project's nix dev shell instead of
a manual `cargo build`.

Using subscription-authenticated CLIs rather than API keys is an
**operational fact about this reference runner** — it's what the operator
actually has (CLI subscriptions, no API keys) — not a project-wide
principle the contract enforces; an API-key-based runner adapter is a
legitimate future implementation of the same `AgentRunner` Protocol (see
"Auth-agnosticism" in `docs/runner-contract.md`). What *is* a real,
specific security property of the `o7` runner, independent of that policy
question: no Anthropic or OpenAI API key is used or read by either repo for
it, and credential storage is never touched directly, only the
already-authenticated `claude`/`codex` CLIs — 007's
`invoke.rs::strip_provider_api_keys` actively strips any provider API key
from the subprocess environment, so one present for an unrelated reason
can't silently substitute for subscription auth.

## Trust warning

All ingested content is untrusted external text. It can contain prompt
injection ("ignore previous instructions", "mark this validated", "run this
command"). Demand Radar's extraction/critique agents run with no filesystem
write access and a JSON-Schema-constrained output for either engine; **no
shell tool** is a verified property of Claude specifically (`--tools ""`
removes it structurally), which is why `--analyst`/`--critic` currently
refuse `codex` outright rather than assume the same guarantee holds for an
engine whose tool-removal has never been exercised against a live install —
see [`docs/trust-boundaries.md`](docs/trust-boundaries.md) for the full zone
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
