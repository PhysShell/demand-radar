"""DemandState — spec section 12.1, verbatim field set. `errors` is the one
field multiple nodes append to across the run (LangGraph's `operator.add`
reducer), so a later node's errors never clobber an earlier node's; every
other field is written by exactly one node and uses plain replace semantics.

RunContext carries everything a node needs that must NOT be part of the
checkpointed state: live DB connection, agent runners, product config, the
run's artifact directory. None of it is JSON-serializable or safe to replay
from a checkpoint, so it travels via `config["configurable"]["ctx"]` instead
of the graph state itself -- see graph/build.py.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypedDict

from demand_radar.agents.base import AgentRunner
from demand_radar.config import ProductConfig
from demand_radar.storage.sqlite import Store


class DemandState(TypedDict):
    run_id: str
    product: str
    since: str | None

    input_evidence_ids: list[str]
    accepted_evidence_ids: list[str]
    duplicate_evidence_ids: list[str]

    classifications: list[str]
    cluster_ids: list[str]
    opportunity_ids: list[str]

    analyst_run_id: str | None
    critic_run_id: str | None

    verdict: str | None
    errors: Annotated[list[dict[str, Any]], operator.add]


def initial_state(*, run_id: str, product: str, since: str | None) -> DemandState:
    return DemandState(
        run_id=run_id,
        product=product,
        since=since,
        input_evidence_ids=[],
        accepted_evidence_ids=[],
        duplicate_evidence_ids=[],
        classifications=[],
        cluster_ids=[],
        opportunity_ids=[],
        analyst_run_id=None,
        critic_run_id=None,
        verdict=None,
        errors=[],
    )


@dataclass
class RunContext:
    store: Store
    analyst_runner: AgentRunner
    critic_runner: AgentRunner
    analyst_name: str
    critic_name: str
    product: ProductConfig
    run_dir: Path
    schemas_dir: Path
    now: datetime
    max_agent_retries: int = 2


def get_ctx(config: dict[str, Any]) -> RunContext:
    ctx = config["configurable"]["ctx"]
    assert isinstance(ctx, RunContext)
    return ctx


def error(node: str, message: str, evidence_id: str | None = None) -> dict[str, Any]:
    return {"node": node, "message": message, "evidence_id": evidence_id}
