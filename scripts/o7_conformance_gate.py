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
# A real, non-empty input fixture -- exercises --input-manifest / input_hashes
# on both call paths rather than passing input_paths=[] and leaving that
# whole code path unchecked.
INPUT_CONTENT = "conformance-gate input fixture -- not read into the prompt, only hashed.\n"
O7_BINARY = os.environ.get("O7_BINARY", "o7")
INDEPENDENT_PROMPT_HASH = sha256_text(PROMPT)
INDEPENDENT_INPUT_HASH = sha256_text(INPUT_CONTENT)


def _read_structured(meta: dict) -> object | None:
    path_str = meta.get("structured_output_path")
    if not path_str:
        return None
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def call_o7_invoke_directly(engine: str, tmp: Path) -> dict[str, object]:
    tmp.mkdir(parents=True, exist_ok=True)
    prompt_file = tmp / "prompt.txt"
    prompt_file.write_text(PROMPT, encoding="utf-8")
    schema_file = tmp / "schema.json"
    schema_file.write_text(json.dumps(SCHEMA), encoding="utf-8")
    input_file = tmp / "input.txt"
    input_file.write_text(INPUT_CONTENT, encoding="utf-8")
    manifest_file = tmp / "input-manifest.json"
    manifest_file.write_text(json.dumps({"input_paths": [str(input_file)]}), encoding="utf-8")
    out_dir = tmp / "out"
    argv = [
        O7_BINARY,
        "invoke",
        "--engine",
        engine,
        "--prompt-file",
        str(prompt_file),
        "--input-manifest",
        str(manifest_file),
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
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=70)
    except FileNotFoundError:
        return {
            "status": "BLOCKED_NOT_INSTALLED",
            "schema_valid": False,
            "error_kind": "o7_not_installed",
            "structured": None,
            "prompt_hash": None,
            "input_hashes": None,
            "provider": None,
            "model": None,
            "exit_code": None,
        }
    meta_path = out_dir / "meta.json"
    if not meta_path.is_file():
        return {
            "status": "FAIL_INVALID_OUTPUT",
            "schema_valid": False,
            "error_kind": "no_meta_json",
            "structured": None,
            "prompt_hash": None,
            "input_hashes": None,
            "provider": None,
            "model": None,
            "exit_code": proc.returncode,
        }
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "status": meta["status"],
        "schema_valid": meta["schema_valid"],
        "error_kind": meta.get("error_kind"),
        "structured": _read_structured(meta),
        "prompt_hash": meta["prompt_hash"],
        "input_hashes": meta.get("input_hashes"),
        "provider": meta["provider"],
        "model": meta.get("model"),
        "exit_code": meta.get("exit_code"),
    }


def call_via_runner(engine: str, tmp: Path) -> dict[str, object]:
    tmp.mkdir(parents=True, exist_ok=True)
    schema_file = tmp / "schema.json"
    schema_file.write_text(json.dumps(SCHEMA), encoding="utf-8")
    input_file = tmp / "input.txt"
    input_file.write_text(INPUT_CONTENT, encoding="utf-8")
    runner = O7InvokeRunner(engine=engine, o7_binary=O7_BINARY, timeout_seconds=60.0)
    result = runner.run(
        task_id="conformance",
        prompt=PROMPT,
        input_paths=[input_file],
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
        "input_hashes": result.input_hashes,
        "provider": result.provider,
        "model": result.model,
        "exit_code": result.exit_code,
    }


# Fields that must match exactly between the direct call and the wrapped
# call. All of these come from `o7 invoke`'s own `meta.json` on both sides
# (`O7InvokeRunner` relays them verbatim rather than re-deriving them) --
# a mismatch means the translation layer dropped or altered something
# meta.json actually said, not a difference in what o7 itself decided.
EXACT_MATCH_FIELDS = (
    "status",
    "schema_valid",
    "error_kind",
    "structured",
    "provider",
    "exit_code",
)


def check_engine(engine: str, tmp_root: Path) -> list[str]:
    problems: list[str] = []
    direct = call_o7_invoke_directly(engine, tmp_root / "direct")
    wrapped = call_via_runner(engine, tmp_root / "wrapper")

    print(
        f"[{engine}] direct:  status={direct['status']} schema_valid={direct['schema_valid']} "
        f"error_kind={direct['error_kind']} provider={direct['provider']} "
        f"exit_code={direct['exit_code']}"
    )
    print(
        f"[{engine}] wrapped: status={wrapped['status']} schema_valid={wrapped['schema_valid']} "
        f"error_kind={wrapped['error_kind']} provider={wrapped['provider']} "
        f"exit_code={wrapped['exit_code']}"
    )

    for field in EXACT_MATCH_FIELDS:
        if direct[field] != wrapped[field]:
            d, w = direct[field], wrapped[field]
            problems.append(f"{engine}: {field} direct={d!r} wrapped={w!r}")

    # `model` is only meaningful when the call actually ran (both sides pass
    # no --model here, so o7/the runner should agree on whatever default --
    # currently None/absent for both, which is itself worth catching if it
    # ever silently starts differing).
    if direct["model"] != wrapped["model"]:
        d, w = direct["model"], wrapped["model"]
        problems.append(f"{engine}: model direct={d!r} wrapped={w!r}")

    # input_hashes: both sides hash the SAME input.txt content independently
    # (Rust for `direct`, Python's hash_input_paths for `wrapped`'s success
    # path or o7's own report). Compare the two reported lists to each other
    # AND to a third, independent computation here.
    d_hashes, w_hashes = direct.get("input_hashes"), wrapped.get("input_hashes")
    if d_hashes is not None and w_hashes is not None and d_hashes != w_hashes:
        problems.append(f"{engine}: input_hashes direct={d_hashes!r} wrapped={w_hashes!r}")
    for label, hashes in (("direct", d_hashes), ("wrapped", w_hashes)):
        if hashes and hashes != [INDEPENDENT_INPUT_HASH]:
            problems.append(
                f"{engine}: {label} input_hashes {hashes!r} != independently computed "
                f"{[INDEPENDENT_INPUT_HASH]!r}"
            )

    # Both sides hash the identical PROMPT text; each independently (Rust's
    # o7 for `direct`, `O7InvokeRunner`'s own report for `wrapped`) should
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
