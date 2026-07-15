"""outputs/*.json(l) writers. Pure serialization — no computation, no LLM
calls (spec acceptance 23.7: report generation never calls a model after
normalized outputs are saved).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from demand_radar.models import Classification, CriticVerdict, OpportunityCard, ProblemCluster


def _sha256_hex(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def write_classifications_jsonl(path: Path, classifications: list[Classification]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [c.model_dump_json(by_alias=True, exclude_none=True) for c in classifications]
    content = "\n".join(lines) + ("\n" if lines else "")
    path.write_text(content, encoding="utf-8")
    return _sha256_hex(content.encode("utf-8"))


def write_clusters_json(path: Path, clusters: list[ProblemCluster]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [json.loads(c.model_dump_json(by_alias=True, exclude_none=True)) for c in clusters]
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8")
    return _sha256_hex(content.encode("utf-8"))


def write_opportunities_json(path: Path, opportunities: list[OpportunityCard]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        json.loads(o.model_dump_json(by_alias=True, exclude_none=True)) for o in opportunities
    ]
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8")
    return _sha256_hex(content.encode("utf-8"))


def write_critic_verdicts_json(path: Path, verdicts: list[CriticVerdict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [json.loads(v.model_dump_json(by_alias=True, exclude_none=True)) for v in verdicts]
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8")
    return _sha256_hex(content.encode("utf-8"))


def hash_file(path: Path) -> str:
    return _sha256_hex(path.read_bytes())


def hash_json_manifest(manifest: Mapping[str, object]) -> str:
    content = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    return _sha256_hex(content)
