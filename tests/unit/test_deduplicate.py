from datetime import UTC, datetime

from demand_radar.ingest.deduplicate import (
    canonical_evidence_ids,
    deduplicate_evidence,
    group_duplicates,
)
from demand_radar.ingest.normalize import canonical_url
from demand_radar.scoring.independence import (
    compute_independence,
    duplication_ratio,
    gather_independence_inputs,
)
from tests.factories import make_evidence_item

T0 = datetime(2026, 6, 1, tzinfo=UTC)


def test_same_content_hash_different_url_is_duplicate() -> None:
    a = make_evidence_item(
        source_id="a", url="https://example.com/1", published_at=T0, raw_text="identical text here"
    )
    b = make_evidence_item(
        source_id="b", url="https://example.com/2", published_at=T0, raw_text="identical text here"
    )
    links = deduplicate_evidence([a, b], similarity_threshold=0.5)
    assert len(links) == 1
    assert links[0].duplicate_reason == "same_content_hash"
    # published_at ties, so the canonical pick is an arbitrary-but-deterministic
    # tie-break on id (a hash -- not correlated with "a"/"b" alphabetically);
    # what matters is the pairing is self-consistent, one canonical one not.
    assert {links[0].canonical_evidence_id, links[0].evidence_id} == {a.id, b.id}
    assert links[0].canonical_evidence_id != links[0].evidence_id


def test_same_canonical_url_different_query_params_is_duplicate() -> None:
    a = make_evidence_item(
        source_id="a",
        url="https://example.com/post?utm_source=x",
        published_at=T0,
        raw_text="text one",
    )
    b = make_evidence_item(
        source_id="b",
        url="https://example.com/post?utm_source=y",
        published_at=T0,
        raw_text="text two, unrelated",
    )
    assert canonical_url(a.source.url) == canonical_url(b.source.url)
    links = deduplicate_evidence([a, b], similarity_threshold=0.99)
    assert len(links) == 1
    assert links[0].duplicate_reason == "same_url"


def test_repost_does_not_increase_unique_author_count() -> None:
    original = make_evidence_item(
        source_id="orig",
        author="dave",
        published_at=T0,
        raw_text="Our WPF app leaks memory badly today",
    )
    repost = make_evidence_item(
        source_id="repost",
        author="dave",
        published_at=T0,
        raw_text="Our WPF app leaks memory badly today",
    )
    other_author = make_evidence_item(
        source_id="other",
        author="maria",
        published_at=T0,
        raw_text="Totally unrelated NDepend question here",
    )
    items = [original, repost, other_author]
    links = deduplicate_evidence(items, similarity_threshold=0.5)

    evidence_by_id = {i.id: i for i in items}
    inputs = gather_independence_inputs(
        [i.id for i in items], evidence_by_id, {link.evidence_id: link for link in links}
    )
    independence = compute_independence(inputs)
    assert independence.unique_authors == 2  # dave (once) + maria, not 3


def test_viral_single_author_stays_one_independent_source_even_with_reposts() -> None:
    zed1 = make_evidence_item(
        source_id="z1", author="zed", published_at=T0, raw_text="lol memory leaks are fun"
    )
    zed2 = make_evidence_item(
        source_id="z2", author="zed", published_at=T0, raw_text="lol memory leaks are fun"
    )
    links = deduplicate_evidence([zed1, zed2], similarity_threshold=0.5)
    canonical = canonical_evidence_ids([zed1.id, zed2.id], links)
    assert len(canonical) == 1


def test_duplication_ratio_reflects_repost_fraction() -> None:
    original = make_evidence_item(
        source_id="a", published_at=T0, raw_text="same text again and again"
    )
    repost = make_evidence_item(
        source_id="b", published_at=T0, raw_text="same text again and again"
    )
    items = [original, repost]
    links = deduplicate_evidence(items, similarity_threshold=0.5)
    evidence_by_id = {i.id: i for i in items}
    inputs = gather_independence_inputs(
        [i.id for i in items], evidence_by_id, {link.evidence_id: link for link in links}
    )
    assert duplication_ratio(inputs) == 0.5


def test_group_duplicates_preserves_provenance() -> None:
    a = make_evidence_item(source_id="a", published_at=T0, raw_text="same text for grouping")
    b = make_evidence_item(source_id="b", published_at=T0, raw_text="same text for grouping")
    links = deduplicate_evidence([a, b], similarity_threshold=0.5)
    groups = group_duplicates(links)
    assert len(groups) == 1
    assert groups[0].canonical_evidence_id in {a.id, b.id}
    other = b.id if groups[0].canonical_evidence_id == a.id else a.id
    assert set(groups[0].duplicate_evidence_ids) == {other}


def test_no_duplicates_when_all_distinct() -> None:
    a = make_evidence_item(
        source_id="a", published_at=T0, raw_text="a completely unique statement about leaks"
    )
    b = make_evidence_item(
        source_id="b", published_at=T0, raw_text="an entirely different question about NDepend"
    )
    links = deduplicate_evidence([a, b], similarity_threshold=0.5)
    assert links == []
