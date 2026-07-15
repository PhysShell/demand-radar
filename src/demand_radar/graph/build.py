"""Assembles the 13-node pipeline as one straight line — spec section 12 and
docs/architecture.md's "why a straight line" note. Resumability comes from
compiling with a SqliteSaver checkpointer and always invoking with the same
thread_id (= run_id): a crash mid-run leaves the last completed node's
output checkpointed, and `run_or_resume` continues from exactly there
instead of re-running finished nodes — see tests/integration/test_resume.py.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from demand_radar.graph.nodes.agents import classify, critic_review, generate_opportunities
from demand_radar.graph.nodes.clustering import propose_clusters, score_clusters, verify_clusters
from demand_radar.graph.nodes.finalize import deterministic_judge, render_report, verify_run
from demand_radar.graph.nodes.ingestion import deduplicate, load_new_evidence, load_run, normalize
from demand_radar.graph.state import DemandState

NODE_ORDER: list[tuple[str, Any]] = [
    ("load_run", load_run),
    ("load_new_evidence", load_new_evidence),
    ("normalize", normalize),
    ("deduplicate", deduplicate),
    ("classify", classify),
    ("propose_clusters", propose_clusters),
    ("verify_clusters", verify_clusters),
    ("score_clusters", score_clusters),
    ("generate_opportunities", generate_opportunities),
    ("critic_review", critic_review),
    ("deterministic_judge", deterministic_judge),
    ("render_report", render_report),
    ("verify_run", verify_run),
]


def build_graph(checkpointer: SqliteSaver) -> CompiledStateGraph:
    graph = StateGraph(DemandState)
    for name, fn in NODE_ORDER:
        graph.add_node(name, fn)

    graph.add_edge(START, NODE_ORDER[0][0])
    for (a, _), (b, _) in pairwise(NODE_ORDER):
        graph.add_edge(a, b)
    graph.add_edge(NODE_ORDER[-1][0], END)

    return graph.compile(checkpointer=checkpointer)


@contextmanager
def open_checkpointer(db_path: Path) -> Iterator[SqliteSaver]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        yield saver
    finally:
        conn.close()


def run_or_resume(
    compiled: CompiledStateGraph, *, initial_state: DemandState, config: RunnableConfig
) -> DemandState:
    """Fresh run if this thread_id has no checkpoint yet; otherwise resume
    from the last completed node. Same call either way from the caller's
    perspective — see graph/build.py module docstring."""
    existing = compiled.get_state(config)
    if existing.values:
        result = compiled.invoke(None, config)
    else:
        result = compiled.invoke(initial_state, config)
    return cast(DemandState, result)
