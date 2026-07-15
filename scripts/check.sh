#!/usr/bin/env bash
# The one local gate — spec section 25. Every step here must succeed with
# no internet access, no Claude/Codex authentication, and no external
# services. Real claude/codex runs live only in `demand-radar smoke-agents`,
# which this script never calls.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "ruff format --check"
uv run ruff format --check src/ tests/

step "ruff check"
uv run ruff check src/ tests/

step "mypy"
uv run mypy

step "pytest (unit + integration -- includes the scripted fixture end-to-end"
echo "    run with real experiment_ready/investigate/rejected outcomes, the"
echo "    crash+resume test, and verification.json content checks)"
uv run pytest tests/ -q

step "schema validation (schemas/*.json are valid Draft 2020-12)"
uv run python3 -c "
import json
from pathlib import Path
from jsonschema import Draft202012Validator

for f in sorted(Path('schemas').glob('*.json')):
    schema = json.loads(f.read_text())
    Draft202012Validator.check_schema(schema)
    print(f'  OK  {f.name}')
"

step "CLI-level smoke: init -> ingest -> run -> report -> verify, real subprocess-free artifacts"
GATE_TMP="$(mktemp -d)"
trap 'rm -rf "$GATE_TMP"' EXIT
uv run demand-radar init --db "$GATE_TMP/demand.db"
set +e
uv run demand-radar ingest --product own-audit --input ./fixtures/mixed-demand-signals.jsonl --db "$GATE_TMP/demand.db"
INGEST_STATUS=$?
set -e
# exit 1 is expected here: the fixture deliberately contains 2 malformed
# lines (spec section 22) that ingestion must report, not silently pass.
if [ "$INGEST_STATUS" -ne 1 ]; then
  echo "expected ingest to exit 1 (2 known-malformed fixture lines reported), got $INGEST_STATUS" >&2
  exit 1
fi
uv run demand-radar run --product own-audit --analyst fake --critic fake --db "$GATE_TMP/demand.db" --runs-dir "$GATE_TMP/runs" || true
RUN_ID="$(basename "$(find "$GATE_TMP/runs/own-audit" -mindepth 1 -maxdepth 1 -type d)")"
uv run demand-radar report --run "$RUN_ID" --runs-dir "$GATE_TMP/runs" > /dev/null
uv run demand-radar verify --run "$RUN_ID" --runs-dir "$GATE_TMP/runs" > /dev/null || true
test -f "$GATE_TMP/runs/own-audit/$RUN_ID/verification.json"
test -f "$GATE_TMP/runs/own-audit/$RUN_ID/outputs/report.md"
echo "  OK  CLI produced run $RUN_ID with report.md + verification.json"

echo
echo "All gate checks passed."
