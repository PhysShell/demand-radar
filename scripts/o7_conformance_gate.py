#!/usr/bin/env python3
"""Cross-repo conformance gate — spec: same prompt / same input / same
schema / same capability profile through `o7 invoke` (direct subprocess)
versus `O7InvokeRunner` (this repo's own client of the same primitive) must
agree on status, schema_valid, structured output, and failure
classification. See docs/o7-invoke.md.

Requires a real `o7` binary on PATH (or $O7_BINARY) built from the sibling
007 repo -- NOT part of the offline gate (scripts/check.sh), since it
exercises whatever real `claude`/`codex` CLIs are actually available in the
current environment (honestly reporting BLOCKED_NOT_INSTALLED for any that
aren't, exactly like `demand-radar smoke-agents`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from demand_radar.agents.base import READ_ONLY_DATA_PROFILE, sha256_text  # noqa: E402
from demand_radar.agents.o7_invoke import O7InvokeRunner  # noqa: E402

PROMPT = (
    "You have no tools. Respond with exactly one JSON object matching the "
    'provided schema and nothing else: {"acknowledged": true}'
)
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["acknowledged"],
    "properties": {"acknowledged": {"type": "boolean"}},
}
O7_BINARY = os.environ.get("O7_BINARY", "o7")
INDEPENDENT_PROMPT_HASH = sha256_text(PROMPT)


def _read_structured(meta: dict, base_stdout_dir: Path | None = None) -> object | None:
    path_str = meta.get("structured_output_path")
    if not path_str:
        return None
    path = Path(path_str)
    if not path.is_file() and base_stdout_dir is not None:
        path = base_stdout_dir / path.name
    return json.loads(path.read_text(encoding="utf-8"))


def call_o7_invoke_directly(engine: str, tmp: Path) -> dict[str, object]:
    tmp.mkdir(parents=True, exist_ok=True)
    prompt_file = tmp / "prompt.txt"
    prompt_file.write_text(PROMPT, encoding="utf-8")
    schema_file = tmp / "schema.json"
    schema_file.write_text(json.dumps(SCHEMA), encoding="utf-8")
    out_dir = tmp / "out"
    argv = [
        O7_BINARY,
        "invoke",
        "--engine",
        engine,
        "--prompt-file",
        str(prompt_file),
        "--schema",
        str(schema_file),
        "--capability-profile",
        READ_ONLY_DATA_PROFILE,
        "--out",
        str(out_dir),
        "--timeout-secs",
        "60",
    ]
    try:
        subprocess.run(argv, capture_output=True, text=True, timeout=70)
    except FileNotFoundError:
        return {
            "status": "BLOCKED_NOT_INSTALLED",
            "schema_valid": False,
            "error_kind": "o7_not_installed",
            "structured": None,
            "prompt_hash": None,
        }
    meta_path = out_dir / "meta.json"
    if not meta_path.is_file():
        return {
            "status": "FAIL_INVALID_OUTPUT",
            "schema_valid": False,
            "error_kind": "no_meta_json",
            "structured": None,
            "prompt_hash": None,
        }
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "status": meta["status"],
        "schema_valid": meta["schema_valid"],
        "error_kind": meta.get("error_kind"),
        "structured": _read_structured(meta),
        "prompt_hash": meta["prompt_hash"],
    }


def call_via_runner(engine: str, tmp: Path) -> dict[str, object]:
    tmp.mkdir(parents=True, exist_ok=True)
    schema_file = tmp / "schema.json"
    schema_file.write_text(json.dumps(SCHEMA), encoding="utf-8")
    runner = O7InvokeRunner(engine=engine, o7_binary=O7_BINARY, timeout_seconds=60.0)
    result = runner.run(
        task_id="conformance",
        prompt=PROMPT,
        input_paths=[],
        output_schema=schema_file,
        capability_profile=READ_ONLY_DATA_PROFILE,
        run_dir=tmp / "run",
    )
    structured = None
    if result.structured_output_path:
        structured = json.loads(Path(result.structured_output_path).read_text(encoding="utf-8"))
    return {
        "status": result.status,
        "schema_valid": result.schema_valid,
        "error_kind": result.error_kind,
        "structured": structured,
        "prompt_hash": result.prompt_hash,
    }


def check_engine(engine: str, tmp_root: Path) -> list[str]:
    problems: list[str] = []
    direct = call_o7_invoke_directly(engine, tmp_root / "direct")
    wrapped = call_via_runner(engine, tmp_root / "wrapper")

    print(
        f"[{engine}] direct:  status={direct['status']} "
        f"schema_valid={direct['schema_valid']} error_kind={direct['error_kind']}"
    )
    print(
        f"[{engine}] wrapped: status={wrapped['status']} "
        f"schema_valid={wrapped['schema_valid']} error_kind={wrapped['error_kind']}"
    )

    if direct["status"] != wrapped["status"]:
        problems.append(
            f"{engine}: status direct={direct['status']!r} wrapped={wrapped['status']!r}"
        )
    if direct["schema_valid"] != wrapped["schema_valid"]:
        d, w = direct["schema_valid"], wrapped["schema_valid"]
        problems.append(f"{engine}: schema_valid direct={d!r} wrapped={w!r}")
    if direct["error_kind"] != wrapped["error_kind"]:
        d, w = direct["error_kind"], wrapped["error_kind"]
        problems.append(f"{engine}: error_kind direct={d!r} wrapped={w!r}")
    if direct["structured"] != wrapped["structured"]:
        d, w = direct["structured"], wrapped["structured"]
        problems.append(f"{engine}: structured output direct={d!r} wrapped={w!r}")
    # Both sides hash the identical PROMPT text; each independently (Rust's
    # o7 for `direct`, `O7InvokeRunner`'s own local sha256_text for the
    # blocked-path fallback, or o7's own report again when PASS/FAIL) should
    # match a THIRD, independent computation done here in Python. A mismatch
    # would mean the two languages' sha256+UTF-8 handling disagree on
    # identical input, or `--prompt-file` wasn't written byte-for-byte what
    # was asked -- either way, a real bug, not a translation nuance.
    for label, result in (("direct", direct), ("wrapped", wrapped)):
        reported = result.get("prompt_hash")
        if reported is not None and reported != INDEPENDENT_PROMPT_HASH:
            problems.append(
                f"{engine}: {label} prompt_hash {reported!r} != independently computed "
                f"{INDEPENDENT_PROMPT_HASH!r}"
            )
    return problems


def main() -> int:
    all_problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="o7-conformance-") as tmp:
        tmp_root = Path(tmp)
        for engine in ("claude", "codex"):
            all_problems += check_engine(engine, tmp_root / engine)

    if all_problems:
        print("\nCONFORMANCE GATE: FAIL")
        for p in all_problems:
            print(f"  - {p}")
        return 1
    print("\nCONFORMANCE GATE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
