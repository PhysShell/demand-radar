"""Deterministic deduplication — spec section 18.

Three signals feed one union-find grouping: identical canonical URL,
identical content hash, and near-duplicate text (TF-IDF cosine, see
similarity.py). The earliest-published member of each resulting group is the
canonical evidence item; every other member gets a DuplicateLink pointing at
it. Independence counting (unique_authors, source_families) always happens
over *canonical* members only — see scoring/independence.py — so a repost
never inflates demand, and rule 4 (same author/topic/short-interval isn't
fully independent) falls out for free: same-author reposts collapse to one
canonical member by construction, without any special-cased code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from demand_radar.ingest.similarity import pairwise_similarity
from demand_radar.models import DuplicateItem, DuplicateLink, EvidenceItem

DuplicateReason = Literal["same_url", "same_content_hash", "similar_text"]

_REASON_PRIORITY = {"same_url": 3, "same_content_hash": 2, "similar_text": 1}


@dataclass
class _Edge:
    reason: DuplicateReason
    score: float


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _repo_key(item: EvidenceItem) -> str | None:
    """'owner/repo' for a github_issue source_id ('github_issue:owner/repo#123'),
    else None. None means "boundary unknown" -- callers treat that as a
    cross-repo pair (the strict threshold), never as same-repo leniency by
    default, since a false "same project" assumption is the riskier error.
    """
    if item.source.source_family != "github":
        return None
    source_id = item.source.source_id
    if ":" not in source_id:
        return None
    _, rest = source_id.split(":", 1)
    if "#" not in rest:
        return None
    repo, _ = rest.rsplit("#", 1)
    return repo or None


def deduplicate_evidence(
    items: list[EvidenceItem],
    *,
    similarity_threshold: float,
    cross_repo_similarity_threshold: float | None = None,
) -> list[DuplicateLink]:
    """items must all belong to one product. Returns one link per
    *non-canonical* member of every group with more than one member.

    `cross_repo_similarity_threshold`, when given, applies only to pairs
    whose derived repo keys differ (or are unknown) -- same-repo pairs
    keep `similarity_threshold` exactly as before. This exists because a
    live GitHub run found a real false-positive collapse (EWSoftware/
    VSSpellChecker#30 with NuGet/Home#3474 at score 0.594, two unrelated
    issues in unrelated repos, both using generic issue-template language
    that inflates cross-repo cosine similarity) at a score the base
    threshold was never calibrated to reject cross-repo, only same-repo
    (see DeduplicationConfig's docstring). Omitting the argument (the
    default) reproduces the exact prior single-threshold behavior.
    """
    n = len(items)
    if n < 2:
        return []

    uf = _UnionFind(n)
    edges: dict[frozenset[int], _Edge] = {}

    def record_edge(i: int, j: int, reason: DuplicateReason, score: float) -> None:
        key = frozenset((i, j))
        existing = edges.get(key)
        if existing is None or _REASON_PRIORITY[reason] > _REASON_PRIORITY[existing.reason]:
            edges[key] = _Edge(reason, score)
        uf.union(i, j)

    for i in range(n):
        for j in range(i + 1, n):
            url_i, url_j = items[i].source.url, items[j].source.url
            if url_i is not None and url_j is not None and url_i == url_j:
                record_edge(i, j, "same_url", 1.0)
            if items[i].content.content_hash == items[j].content.content_hash:
                record_edge(i, j, "same_content_hash", 1.0)

    repo_keys = [_repo_key(it) for it in items]
    similarity = pairwise_similarity([it.content.normalized_text for it in items])
    for i in range(n):
        for j in range(i + 1, n):
            score = similarity[i][j]
            same_repo = repo_keys[i] is not None and repo_keys[i] == repo_keys[j]
            threshold = (
                similarity_threshold
                if same_repo or cross_repo_similarity_threshold is None
                else cross_repo_similarity_threshold
            )
            if score >= threshold:
                record_edge(i, j, "similar_text", score)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    links: list[DuplicateLink] = []
    for member_indices in groups.values():
        if len(member_indices) < 2:
            continue
        canonical_idx = min(
            member_indices,
            key=lambda idx: (items[idx].timestamps.published_at, items[idx].id),
        )
        canonical_id = items[canonical_idx].id
        group_id = f"dupgrp_{canonical_id}"

        for idx in member_indices:
            if idx == canonical_idx:
                continue
            reason, score = _best_edge_for(idx, canonical_idx, member_indices, edges)
            links.append(
                DuplicateLink(
                    evidence_id=items[idx].id,
                    duplicate_group_id=group_id,
                    canonical_evidence_id=canonical_id,
                    duplicate_reason=reason,
                    similarity_score=score,
                )
            )
    return links


def _best_edge_for(
    idx: int, canonical_idx: int, group: list[int], edges: dict[frozenset[int], _Edge]
) -> tuple[DuplicateReason, float]:
    direct = edges.get(frozenset((idx, canonical_idx)))
    if direct is not None:
        return direct.reason, direct.score

    best: _Edge | None = None
    for other in group:
        if other == idx:
            continue
        edge = edges.get(frozenset((idx, other)))
        if edge is None:
            continue
        if best is None or _REASON_PRIORITY[edge.reason] > _REASON_PRIORITY[best.reason]:
            best = edge
    assert best is not None, "every non-canonical member must have at least one recorded edge"
    return best.reason, best.score


def canonical_evidence_ids(all_ids: list[str], duplicate_links: list[DuplicateLink]) -> list[str]:
    """All_ids with duplicate (non-canonical) members removed."""
    duplicate_ids = {link.evidence_id for link in duplicate_links}
    return [eid for eid in all_ids if eid not in duplicate_ids]


def group_duplicates(duplicate_links: list[DuplicateLink]) -> list[DuplicateItem]:
    """One DuplicateItem per duplicate group, for evidence/duplicates.jsonl."""
    groups: dict[str, list[DuplicateLink]] = {}
    for link in duplicate_links:
        groups.setdefault(link.duplicate_group_id, []).append(link)
    return [
        DuplicateItem(
            duplicate_group_id=group_id,
            canonical_evidence_id=members[0].canonical_evidence_id,
            duplicate_evidence_ids=[m.evidence_id for m in members],
        )
        for group_id, members in groups.items()
    ]
