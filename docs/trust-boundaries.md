# Trust boundaries

All ingested content is external, untrusted text. It is analyzed; it is
never executed, and it is never capable of granting itself capability.
`fixtures/prompt-injection-signals.jsonl` (6 records, several distinct
techniques) and `fixtures/mixed-demand-signals.jsonl`'s one embedded
injection item exist specifically to exercise this, and every claim below
has a corresponding test in `tests/unit/test_prompt_injection.py`.

## The three zones (spec section 17)

```text
Zone 1: ingestion (ingest/jsonl.py, ingest/rss.py)
  accepts external content
  no agent execution, no shell execution
  computes hashes/normalization -- pure functions of the input text

Zone 2: extraction and critique (classify, generate_opportunities, critic_review)
  read-only from the agent's perspective: no filesystem tool, no shell,
  no network the agent can direct beyond the CLI's own model-API traffic
  strict JSON Schema output, re-validated with Pydantic independent of
  whatever validation the CLI itself claims to have done
  no write access outside the run directory, and even that is written
  by *this pipeline's own code*, never by the agent process itself

Zone 3: deterministic judge (deterministic_judge, render_report, verify_run)
  receives normalized records and already-validated typed objects
  never interprets raw evidence text as an instruction
  makes zero LLM calls
```

## What "read-only" means concretely here

The analyst and critic are never given a filesystem-read tool at all. As of
the `O7InvokeRunner` migration (`docs/o7-invoke.md`), the actual closed-world
flags (`--tools ""`/`--strict-mcp-config` for Claude, `--sandbox read-only`
for Codex) are enforced by **007's `o7 invoke`**, not by code in this
repository — this codebase only shells out to `o7 invoke` and translates its
`meta.json` into `AgentResult`. The `read-only-data` capability-profile label
is still this codebase's vocabulary (`agents/base.py::READ_ONLY_DATA_PROFILE`);
what it maps to is now 007's responsibility to enforce, and 007 refuses to
run at all on an unrecognized profile name rather than silently narrowing or
widening it (007's own `docs/security-layers.md`). Evidence text is not
something the model goes and reads from a path — it is embedded directly
into the prompt string by our own code
(`agents/prompts/*.py`), inside a labeled fence:

```text
=== UNTRUSTED EVIDENCE DATA: ev_xxxx ===
<raw_text>
=== END UNTRUSTED EVIDENCE DATA: ev_xxxx ===
```

`agents/prompts/trust.py::TRUST_PREAMBLE` (prepended to every analyst/critic
prompt) explicitly tells the model that content inside this fence is data to
analyze, is never an instruction, and that neither status fields nor tool
access exist for it to invoke even if evidence text asks it to. This is a
**second, defense-in-depth layer** — the load-bearing guarantee is that the
model has no tool through which an embedded instruction could take effect,
regardless of whether it "obeys" the bait text or not:

- It cannot set `OpportunityCard.status` — the field doesn't exist in
  `OPPORTUNITY_CANDIDATE_SCHEMA`, the schema the analyst actually returns
  against (a deliberate subset of the full card — see
  `docs/architecture.md`). Even if a candidate dict somehow carried a
  `status` key, `_assemble_opportunity_card` builds the card from named
  fields and never reads one.
- `externally_validated` cannot be requested by the critic either — it is
  not in `CriticVerdict.recommended_status`'s enum, and even
  `recommended_status` is explicitly non-authoritative: `scoring/judge.py`
  is the only code path that ever writes `OpportunityCard.status`.
- It has no shell, so "run this command" in evidence text has nowhere to
  go.
- It has no network tool of its own (Claude: none, structurally; Codex:
  no `web_search` opt-in — see the residual risk below for the one gap
  this doesn't close). Enforced by 007's `invoke.rs`, not this repo.

## Auth storage

Neither this codebase nor `O7InvokeRunner` reads, copies, or logs Claude's
or Codex's credential storage — `O7InvokeRunner` shells out to `o7 invoke`
(007), which itself shells out to the CLI the user already authenticated
interactively (`claude`, `codex`), relying on whatever the CLI's own login
state already is. No `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`CODEX_API_KEY`/
`CLAUDE_API_KEY` is read by either codebase; 007's
`invoke.rs::strip_provider_api_keys` actively **strips** all four from the
subprocess environment before every call (both engines, not just Codex —
see `007/docs/decisions.log.md` for why this was added rather than assumed
from `judge.rs`, which does not strip them), so a key present in the parent
environment for an unrelated reason can never silently substitute for the
subscription login this is meant to exercise.

## Filesystem

Zone 2 processes write nothing themselves. `O7InvokeRunner` creates and
populates its own `run_dir` (`prompt.txt`, `input-manifest.json` if any);
007's `o7 invoke` separately creates and populates its own `--out` directory
(`stdout.raw`, `stderr.log`, `result.json`, `meta.json`) — paths **we** (or
007, on our behalf) pass in and open, never paths the model chooses.
`--tools ""` means Claude has no write tool at all; Codex's
`--sandbox read-only` denies writes at the sandbox level as a second layer
— both enforced inside 007, not here.

## Network

CLI subprocess calls themselves need network to reach Anthropic's/OpenAI's
own API (that is the point — subscription-backed inference). Beyond that:
Claude has no tool that could reach any other host. Codex's
`--sandbox read-only` does **not** disable network (inherited residual risk,
documented identically in both repos: 007's `judge.rs`/`docs/security-layers.md`
and this doc) — this is why the analyst-facing extraction/critique work
should prefer the Claude engine when a choice exists, and it is called out
explicitly rather than glossed over. No `web_search` config is enabled for
Codex, so no live web tool exists either way in this MVP's actual
invocations.

## Subprocess

Prompts are passed via **stdin**, not argv, both at this repo's boundary
(`O7InvokeRunner` writes `prompt.txt` and hands 007 its path, never the
text as an argv token) and inside 007 itself (`invoke.rs::spawn_with_timeout`
writes the prompt to the child's stdin) — matching 007's own documented
rationale (`007/docs/security-layers.md`): argv is world-readable via
`/proc/*/cmdline` and shell history, and has a size limit an evidence-laden
prompt could hit. `o7 invoke` runs with an explicit `--timeout-secs`,
polling and killing the child on expiry (`invoke.rs::spawn_with_timeout`);
`O7InvokeRunner` itself also passes a `timeout` to the `o7` subprocess call
one layer up, so a hang in `o7` itself (not just the backend CLI) is still
bounded. Either layer's timeout surfaces as `BLOCKED_TIMEOUT`.

## Logs

`stdout.raw`/`stderr.log` (written by 007) capture exactly what the backend
CLI printed — no environment dump, no credential material (there is none to
capture, since nothing in either codebase ever reads or forwards a
credential file). `meta.json` (007's own record, translated into this
repo's `AgentResult` by `O7InvokeRunner`) records `command_version`, `model`,
timestamps, exit code, and hashes — never the prompt's or the environment's
full content beyond the `prompt.txt` written alongside it (which is,
itself, evidence text plus our own fixed template — never a secret).

## Unknown fields

Every Pydantic model in `models.py` sets `extra="forbid"`, mirroring every
schema's `additionalProperties: false`. An agent response with an
unexpected field fails validation outright
(`tests/unit/test_schema_parity.py::test_unknown_field_rejected_by_model`),
it is not silently dropped or ignored.

## Accepted residual risks

- **Codex flags are unverified against a real install.** `codex` is not
  installed in the environment either this MVP or 007's `invoke.rs` was
  built in, so 007's codex flags (`-a never exec - --json --sandbox
  read-only --skip-git-repo-check --ephemeral --output-last-message <file>`
  — matched from `judge.rs`'s own already-more-verified pattern, see
  `007/docs/decisions.log.md`) are checked against public documentation and
  `judge.rs`'s existing behavior, not against `codex --help` directly.
  Re-verify before first real use. `demand-radar smoke-agents` will surface
  `BLOCKED_NOT_INSTALLED` honestly rather than assume success — confirmed
  live in this environment (`claude: PASS`, `codex: BLOCKED_NOT_INSTALLED`).
- **Codex `--sandbox read-only` does not close network egress** (see
  above) — inherited, documented in both repos, not solved here.
- **This project no longer owns the closed-world enforcement code at
  all — it owns the translation layer.** `O7InvokeRunner` trusts 007's
  `meta.json` as the source of truth for `status`/`schema_valid`/
  `error_kind`; it does not re-derive these from `o7`'s own stdout/exit
  code. This is a deliberate trust boundary, not an oversight: re-deriving
  the same classification logic in two languages is exactly the kind of
  drift the cross-repo conformance gate (`docs/o7-invoke.md`) exists to
  catch if it ever happens, rather than silently diverging. If 007's own
  classification has a bug, this repo inherits it — which is the intended
  trade for not maintaining the closed-world flags twice.
- **No syscall-level sandbox (Landlock/seccomp/microVM) wraps `o7 invoke`'s
  subprocess.** Considered and **not adopted**, for the same reason 007's
  own `docs/security-layers.md` gives: the sibling `sandboy` (Own.NET) is
  unverified-built in this environment, and the closed-world CLI flags
  already remove the tool surface an OS sandbox would otherwise need to
  contain. Revisit if a future zone needs actual shell/filesystem tool
  access. This is now entirely 007's decision to make, not this repo's —
  tracked in `007/docs/security-layers.md`, not duplicated here.
- **No cryptographic chain-of-custody.** `verification.json`'s hashes are
  plain `sha256` of the named artifact — a reproducibility record, proving
  "this file has this content," not a signed or tamper-evident chain. Never
  claim more than that.
- **Behavioral guarantees, not proven behavior.** Everything above is a
  structural claim about what the model *can* do (no tool, no field, no
  network path) — it is not a claim about what a live model *will* do with
  adversarial input. `tests/unit/test_prompt_injection.py` checks the
  structural guarantees (fencing, absent fields, schema constraints)
  exhaustively; it cannot and does not simulate a live model's behavior.
