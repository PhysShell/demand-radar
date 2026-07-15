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

The analyst and critic are never given a filesystem-read tool at all
(`--tools ""` for Claude; no MCP servers, no shell). Evidence text is not
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
  no shell tool via `-c features.shell_tool=false`, and no opt-in
  `web_search` config passed — see the residual risk below for the one
  gap this doesn't close).

## Auth storage

Neither runner reads, copies, or logs Claude's or Codex's credential
storage. Both shell out to the CLI the user already authenticated
interactively (`claude`, `codex`) and rely on whatever the CLI's own login
state already is. No `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`CODEX_API_KEY`
is read by this codebase; `agents/codex_cli.py::_clean_env` actively
**strips** `OPENAI_API_KEY`/`CODEX_API_KEY` from the subprocess environment
before every call, so a key present in the parent environment for an
unrelated reason can never silently substitute for the ChatGPT-subscription
login this is meant to exercise.

## Filesystem

Zone 2 processes write nothing themselves; `run_dir` is created and
populated entirely by our own code (`agents/base.py::persist_call_artifacts`,
the runners' own `stdout.jsonl`/`stderr.log`/`result.json` writes, which are
paths **we** pass in and open, not paths the model chooses). `--tools ""`
means Claude has no write tool at all; Codex's `-s read-only` denies writes
at the sandbox level as a second layer.

## Network

CLI subprocess calls themselves need network to reach Anthropic's/OpenAI's
own API (that is the point — subscription-backed inference). Beyond that:
Claude has no tool that could reach any other host. Codex's `-s read-only`
sandbox does **not** disable network (inherited residual risk from 007's
own `judge/README.md`: "codex has no one-flag equivalent" to Claude's
`--tools ""`) — this is why the analyst-facing extraction/critique work
should prefer the Claude runner when a choice exists, and it is called out
explicitly rather than glossed over. No `web_search` config is enabled for
Codex, so no live web tool exists either way in this MVP's actual
invocations.

## Subprocess

Prompts are passed via **stdin**, not argv (`subprocess.run(..., input=prompt)`),
matching 007's own documented rationale (`007/docs/security-layers.md`):
argv is world-readable via `/proc/*/cmdline` and shell history, and has a
size limit an evidence-laden prompt could hit. Both runners run with an
explicit `timeout`; Python's `subprocess.run(..., timeout=...)` kills the
process on expiry as part of raising `TimeoutExpired`, which both runners
catch and turn into `BLOCKED_TIMEOUT`.

## Logs

`stdout.jsonl`/`stderr.log` capture exactly what the CLI printed — no
environment dump, no credential material (there is none to capture, since
neither runner ever reads or forwards a credential file). `meta.json`
(the serialized `AgentResult`) records `command_version`, `model`,
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
  installed in the environment this MVP was built in, so
  `agents/codex_cli.py`'s flags (`-a never exec - --json --output-schema
  <file> -s read-only -c features.shell_tool=false --skip-git-repo-check`)
  were checked against public documentation and cross-referenced with
  007's own `judge/README.md`, not against `codex --help` directly, as the
  task instructs when possible. Re-verify before first real use — see
  `docs/decisions.log.md`. `demand-radar smoke-agents` will surface
  `BLOCKED_NOT_INSTALLED` honestly rather than assume success.
- **Codex `-s read-only` does not close network egress** (see above) —
  inherited, documented, not solved here.
- **No syscall-level sandbox (Landlock/seccomp/microVM) wraps either
  runner's subprocess.** Considered and **not adopted for this MVP**: the
  sibling `sandboy` (Own.NET, Landlock+seccomp wrap-the-child) exists and
  wraps arbitrary subprocess commands, so `sandboy run --policy ... --
  claude ...` could wrap these calls the same way a `.007/gate.toml` step
  would — but sandboy itself is unverified-built in this environment (its
  own README notes it was "authored in a network-restricted sandbox," not
  compiled), and the closed-world CLI flags here already remove the tool
  surface an OS sandbox would otherwise need to contain. Adding an unproven
  dependency to defend against a threat with no observed instance (an
  agent escaping its own `--tools ""` restriction) is exactly the
  "building for a need that hasn't appeared" pattern 007's own
  `docs/workflow-scripting.md` warns against. Revisit if a future Demand
  Radar agent zone needs actual shell/filesystem tool access.
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
