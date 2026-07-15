# `o7 invoke` — a future read-only agent primitive for 007

Status: **superseded — implemented.** This proposal shipped:
`007/src/invoke.rs` implements `o7 invoke`, and Demand Radar's
`agents/o7_invoke.py::O7InvokeRunner` is its client, replacing the
`ClaudeCodeRunner`/`CodexCliRunner` this proposal originally imagined
Demand Radar keeping. See [`docs/o7-invoke.md`](o7-invoke.md) for the actual
implementation, what differs from this original sketch, and the cross-repo
conformance gate. This file is kept as the historical record of the
proposal, not edited to match what was actually built — read it as "here is
what was proposed," not "here is what exists."

---

**Original status (superseded above): proposal only, not implemented.**
Demand Radar does not import, link against, or depend on 007 in any way;
this note exists solely to record what a minimal future contract could look
like, so that if 007 ever grows a subscription-backed read-only agent
primitive, Demand Radar's own runner interface (`agents/base.py::AgentRunner`)
is a plausible client of it without a rewrite. Nothing here is scheduled or
committed.

## Why this is a proposal, not code

The task brief is explicit that Demand Radar must not add a Python/LangGraph
runtime inside the `o7` Rust binary, must not change `o7 run` or `o7 judge`,
and must not implement a general `o7 workflow`, a DAG engine inside 007,
Claude/Codex consensus inside 007, a new O7Plan runtime, a universal
provider framework, or a memory layer. This document respects all of that:
it is read-only documentation of a *shape*, evaluated against 007's actual
present-day constraints (see `docs/decisions.log.md`'s Step-1 findings), not
a shipped feature.

## What 007 actually has today, that this would build on

- **`o7 judge`** is the one already-working, closed-world agent invocation
  in 007: `--tools ""` + `--strict-mcp-config` for its Claude backend, prompt
  via stdin, structured overlay output, a documented (if narrower) codex
  path with an acknowledged network-egress gap. `o7 invoke` would be this
  pattern generalized, not a new design.
- **Claude-first status.** `judge --provider claude` is the default and the
  only backend with the full closed-world guarantee; `--provider codex`
  exists but is explicitly weaker (network not disabled). Any `o7 invoke`
  proposal inherits this asymmetry rather than pretending both backends are
  equivalent.
- **Deferred Codex path.** `TODO.md`'s own backlog defers Claude+Codex
  consensus to "design with real run records." `o7 invoke` should not bundle
  multi-provider orchestration; it should let the *caller* (Demand Radar,
  or anything else) run one engine at a time and combine results itself,
  exactly as Demand Radar's own `classify`/`critic_review` split already
  does outside of 007.
- **No general multi-provider IR.** 007's `docs/workflow-scripting.md` ADR
  explicitly rejects baking a `provider: "claude" | "codex"` choice into a
  new IR now, calling it "an abstraction with one real implementation."
  `o7 invoke --engine claude|codex` should stay a flag on a single-shot
  command, not the seed of a provider abstraction layer.
- **Existing run artifacts.** 007 already has a canonical run-record shape
  (`task.md`, `meta.json`, `agent.stdout`, `diff.patch`, `gate/*.log` for
  `run`; a judge-specific run record for `judge`). `o7 invoke` should emit
  into that same family, not a bespoke shape Demand Radar would have to
  translate.
- **Script-proposes/host-enforces.** The one idea `docs/workflow-scripting.md`
  says is load-bearing and worth keeping: a caller never gets `exec`/`fs`/
  network directly — it calls a named primitive, and the Rust host decides
  whether to run it. `o7 invoke` is exactly this shape applied to "run one
  agent, read-only, against one task spec."
- **Fail-closed, not fail-open.** `docs/security-layers.md`'s own framing
  of the still-open gap in `run`/gate (`sandbox_policy` on `GateStep` must
  fail closed when it lands, not silently run unconfined if a policy is
  missing) applies here too: `o7 invoke` must refuse to run rather than
  silently downgrade capability if a requested `--capability-profile` isn't
  actually enforceable on the host.
- **Sandbox is not solved by capability config alone.** `docs/workflow-scripting.md`
  says this plainly about the (also proposed, also unbuilt)
  `policy.capabilities({...})` idea: it constrains what a *script* can
  call, not what the *agent* does once it's running. The real sandbox slot
  — a syscall boundary — is a separate, still-open problem
  (`docs/security-layers.md`, the `sandboy` direction). `o7 invoke`
  specifying `--capability-profile read-only-data` documents *intent*; it
  does not by itself close that gap, and should never be described as if
  it does.

## Illustrative shape

```text
o7 invoke
  --engine claude|codex
  --task task.yaml
  --input input-manifest.json
  --schema output.schema.json
  --capability-profile read-only-data
  --out run-dir
```

Mapped onto what already exists in this repo:

- `task.yaml` — same idea as `graph/state.py`'s per-run parameters, rendered
  once before the run (see `cli.py::_write_task_yaml`).
- `input-manifest.json` — same idea as `verify_run`'s
  `input_manifest_hash` input: the hashed set of inputs this call's output
  is reproducible from.
- `output.schema.json` — literally reusable as-is: `schemas/classification.schema.json`,
  the `OPPORTUNITY_CANDIDATE_SCHEMA` subset, or `schemas/critic-verdict.schema.json`
  could each be handed to `--schema` unchanged.
- `--capability-profile read-only-data` — the same label
  `agents/base.py::READ_ONLY_DATA_PROFILE` already uses; this MVP defines
  exactly one profile and expects 007 would need to define its own
  enforcement for whatever profiles it recognizes, not adopt this repo's
  label as a security boundary by name alone.
- `--out run-dir` — would emit the same `AgentResult` shape
  (`models.py::AgentResult`) this repo already standardizes on: provider,
  command version, model, timestamps, exit code, status, stdout/stderr/
  structured-output paths, `schema_valid`, `prompt_hash`, `input_hashes`,
  `error_kind`.

## What this explicitly does not propose

- A DAG or multi-step workflow inside 007 built around `o7 invoke` — that
  is a separate, larger, unproven idea (`o7 workflow`), not this.
- A shared provider abstraction layer spanning `run`/`judge`/`invoke`.
- Any claim that `o7 invoke` would make Demand Radar "integrated with 007."
  Even if built, Demand Radar would remain a sibling project that could
  *optionally* call it instead of shelling out to `claude`/`codex` directly
  — the rest of this codebase's contracts (schemas, `AgentRunner`,
  `AgentResult`) would not change shape because of it.
- Implementation. Nothing in this file is code, and nothing in this task's
  scope adds `invoke` to 007's actual command surface.
