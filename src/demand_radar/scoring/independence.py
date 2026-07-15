"""Independence stats — spec section 18: engagement never substitutes for
unique authors. unique_authors and source_families are always computed over
*canonical* (non-duplicate) members only, so a repost — even one crossposted
by a different account to a different platform — cannot inflate either
count. This is the enforcement point for "one viral item can't create
experiment_ready alone" (rule 7); rule 4 (same author/topic/short-interval
isn't independent) falls out for free because it collapses to one canonical
member with no special-cased code.
"""

from __future__ import annotations

from dataclasses import dataclass

from demand_radar.models import ClusterIndependence, DuplicateLink, EvidenceItem


@dataclass
class IndependenceInputs:
    """Precomputed per-member facts needed by compute_independence, so the
    function itself stays a pure computation over plain data."""

    canonical_members: list[EvidenceItem]
    non_canonical_count: int
    duplicate_group_ids: set[str]


def gather_independence_inputs(
    member_evidence_ids: list[str],
    evidence_by_id: dict[str, EvidenceItem],
    duplicate_links_by_evidence_id: dict[str, DuplicateLink],
) -> IndependenceInputs:
    canonical_members = []
    non_canonical_count = 0
    duplicate_group_ids: set[str] = set()

    for eid in member_evidence_ids:
        link = duplicate_links_by_evidence_id.get(eid)
        if link is None:
            canonical_members.append(evidence_by_id[eid])
        else:
            non_canonical_count += 1
            duplicate_group_ids.add(link.duplicate_group_id)

    return IndependenceInputs(
        canonical_members=canonical_members,
        non_canonical_count=non_canonical_count,
        duplicate_group_ids=duplicate_group_ids,
    )


def compute_independence(inputs: IndependenceInputs) -> ClusterIndependence:
    unique_authors = len({item.author.stable_hash for item in inputs.canonical_members})
    source_families = len({item.source.source_family for item in inputs.canonical_members})
    return ClusterIndependence(
        unique_authors=unique_authors,
        source_families=source_families,
        duplicate_groups=len(inputs.duplicate_group_ids),
    )


def duplication_ratio(inputs: IndependenceInputs) -> float:
    total = len(inputs.canonical_members) + inputs.non_canonical_count
    if total == 0:
        return 0.0
    return inputs.non_canonical_count / total
