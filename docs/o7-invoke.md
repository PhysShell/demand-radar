# `o7 invoke` — the real implementation, and what changed

`o7 invoke`, wrapped by `O7InvokeRunner`, is now the **reference
implementation** of [`docs/runner-contract.md`](runner-contract.md) — the
normative `AgentRunner` Protocol any runner adapter must satisfy, not
something specific to 007. `--runner o7` is that contract's default value,
not its only legal one. The historical record below — how `o7 invoke` came
to exist, what it does, and what changed relative to the original proposal
— is unchanged by that reframing; it describes this one runner, not the
contract in general.

This is the follow-up to [`docs/o7-bridge-proposal.md`](o7-bridge-proposal.md)
(kept as the historical record of the original sketch). `o7 invoke` is now
real: `007/src/invoke.rs`, dispatched from `007/src/main.rs`, with Demand
Radar's `agents/o7_invoke.py::O7InvokeRunner` as its one client. This is a
genuine cross-repo dependency now, not a "someday" note — Demand Radar's
`run` command shells out to the `o7` binary for the `claude` engine (the
only one `--analyst`/`--critic` currently permit — see
`docs/trust-boundaries.md`: Codex's closed-world posture is unverified
against a live install and is refused for real evidence processing until
that changes), and no longer contains its own closed-world CLI-flag
knowledge at all for either engine.

## What `o7 invoke` actually looks like

```bash
o7 invoke
  --engine claude|codex
  --prompt-file prompt.txt
  --input-manifest input-manifest.json   # optional
  --schema output.schema.json
  --capability-profile read-only-data
  --out run-dir
  --model <alias>                        # optional
  --timeout-secs 120                     # optional, default 120
```

Writes `<run-dir>/{prompt.txt, stdout.raw, stderr.log, result.json (if any),
meta.json}`. `meta.json` mirrors `demand_radar.models.AgentResult`
field-for-field (`007/src/invoke.rs::InvokeMeta`) so `O7InvokeRunner` is a
direct structural translation, not an interpretation. Exit code `0` on
`PASS`, `1` otherwise — including every `BLOCKED_*`/`FAIL_*` status, so a
caller must read `meta.json`, not just the exit code, to distinguish
"schema violation" from "codex not installed."

## What differs from the original proposal

- **Codex has no assumed `--output-schema` flag.** The original proposal
  (and Demand Radar's own, now-deleted `codex_cli.py`) assumed one existed,
  unverified. `invoke.rs::call_codex` does not repeat that assumption: the
  schema is appended to the prompt as an instruction, and `o7 invoke`
  independently validates the returned JSON against it
  (`jsonschema::validator_for`) — the same "host re-validates, never just
  trusts the backend's claim" property the claude path gets from
  `--json-schema` *plus* the same re-validation. This is more conservative
  than the original sketch implied, not less capable.
- **Codex's final-answer extraction reuses `judge.rs`'s proven pattern**
  (`--output-last-message <file>`, stdout discarded), not a fresh
  implementation — `judge.rs` already solved "don't scrape a JSONL event
  stream for the answer" correctly; `invoke.rs` does not re-invent it.
- **`claude --json-schema` rejects a schema carrying `$schema`.** Found by
  running the real smoke test against this environment's live `claude`
  (v2.1.210), not assumed: `Error: --json-schema is not a valid JSON Schema:
  no schema with key or ref "https://json-schema.org/draft/2020-12/schema"`.
  `$id` alone does not trigger it. Fixed once, in `invoke.rs::strip_dollar_schema`
  — every caller of `o7 invoke` gets the fix automatically; it does not need
  reimplementing per language. Demand Radar's own Python copy of this exact
  fix (`agents/base.py::strip_dollar_schema`) predates this and was deleted
  along with `claude_cli.py` itself — its history is in
  `docs/decisions.log.md`, not a leftover unused function in the tree.
- **Provider API keys are stripped for both engines, not just Codex.**
  Demand Radar's old `codex_cli.py` stripped `OPENAI_API_KEY`/`CODEX_API_KEY`;
  007's own `judge.rs` strips neither. `invoke.rs::strip_provider_api_keys`
  strips `ANTHROPIC_API_KEY`/`CLAUDE_API_KEY`/`OPENAI_API_KEY`/`CODEX_API_KEY`
  before *every* call, regardless of `--engine` — a deliberate hardening
  found while writing this doc's trust-boundary comparison, not carried
  over from either prior implementation by default.
- **Capability profile is fail-closed.** An unrecognized
  `--capability-profile` value refuses to run at all, before any process
  spawns, rather than falling back to some default posture.
- **Timestamps are `"epoch:<seconds>"`, not RFC3339.** No date-formatting
  dependency for one field; `O7InvokeRunner::_parse_epoch_tag` parses it on
  the Python side, where it's a one-line `datetime.fromtimestamp` call.

## The migration in Demand Radar

- `agents/claude_cli.py` and `agents/codex_cli.py` are **deleted**, not
  deprecated-in-place. Their closed-world flag knowledge doesn't need a
  Python copy any more.
- `agents/o7_invoke.py::O7InvokeRunner` is the new (and only non-fake)
  `AgentRunner` implementation. It knows nothing about `claude`/`codex`
  flags — only how to build `o7 invoke`'s argv and translate its
  `meta.json` back into `AgentResult`, trusting `meta.json`'s own
  `status`/`schema_valid`/`error_kind` rather than re-deriving them.
- `cli.py::get_runner("claude"|"codex")` and `smoke.py` both construct
  `O7InvokeRunner(engine=...)` now; the `--analyst`/`--critic` CLI
  vocabulary users type is unchanged.
- `FakeRunner` is untouched — offline tests never call `o7` at all.
- `tests/unit/test_cli_runner_schema_wire.py` (which tested the deleted
  runners' own `$schema`-stripping) is replaced by
  `tests/unit/test_o7_invoke_runner.py` (tests `O7InvokeRunner`'s own
  translation logic with a monkeypatched `subprocess.run` — no real `o7`
  binary needed) and `007`'s own `cargo test` (`invoke.rs`'s unit tests,
  which now own the `$schema`-stripping coverage).

## Live verification (this environment)

`claude` is installed and authenticated here; `codex` is not.

- `o7 invoke --engine claude ...` directly: `PASS`, real
  `command_version: "2.1.210 (Claude Code)"`, schema-valid structured
  output.
- `o7 invoke --engine codex ...` directly: `BLOCKED_NOT_INSTALLED` —
  honest, not simulated.
- `demand-radar smoke-agents` (the full Python → `o7` subprocess → `claude`
  round trip): `claude: PASS`, `codex: BLOCKED_NOT_INSTALLED -- not_installed`
  — same result as the direct `o7 invoke` calls, confirming `O7InvokeRunner`
  doesn't lose or reclassify anything in translation.

## Cross-repo conformance gate

`scripts/o7_conformance_gate.py` runs a fixed prompt + schema through
**both** `o7 invoke` directly (subprocess) and `O7InvokeRunner.run(...)`
(this repo's own client of the same primitive), for each of `claude` and
`codex`, then asserts `status`, `schema_valid`, `error_kind`, and structured
output content all agree between the two call paths. It also independently
recomputes `sha256_text(PROMPT)` a *third* time, in the gate script itself,
and checks it against whatever `prompt_hash` either path reports — a
mismatch there would mean Python's and Rust's SHA-256+UTF-8 handling
disagree on byte-identical input (or `--prompt-file` wasn't written
byte-for-byte what was asked), a real bug either way, not a translation
nuance. Exercises whichever real engine(s) are actually available in the
running environment rather than a fixture double — `codex` legitimately
reports `BLOCKED_NOT_INSTALLED` on both sides here, and the gate asserts
that *agreement*, not a fake `PASS`. Requires a built `o7` binary on PATH;
not part of the offline gate (`scripts/check.sh`), which must run with no
external dependency at all.
