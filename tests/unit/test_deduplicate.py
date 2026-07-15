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


# --- cross-repo false-dedup regression -----------------------------------
#
# A live GitHub run (docs/trials/github-live-issues-trial.md) found a real
# false collapse: EWSoftware/VSSpellChecker#30 (a missing menu item) and
# NuGet/Home#3474 (NuGet Package Manager freezing VS) scored 0.594 under
# the shared similarity_threshold=0.5 -- two unrelated issues in unrelated
# repos, both using generic GitHub issue-template boilerplate ("Bug report",
# "To Reproduce", "Steps to reproduce the behavior", "Desktop (please
# complete the following information)"). The text below reproduces that
# shape and scores in the same 0.5-0.75 band (0.636), without quoting the
# real issue bodies verbatim.

_SPELLCHECKER_LIKE_TEXT = """Bug report

Describe the bug: Spell Options menu item missing from Tools menu entirely.

To Reproduce
Steps to reproduce the behavior:
1. Go to Tools menu
2. Look for Spell Options
3. Item is absent

Expected behavior: A clear and concise description of what you expected to happen.

Desktop (please complete the following information):
- OS: Windows 10
- Version: 2015.1
"""

_NUGET_LIKE_TEXT = """Bug report

Describe the bug: Nuget Package Manager froze VS 2015 completely on open.

To Reproduce
Steps to reproduce the behavior:
1. Go to Tools menu
2. Open NuGet Package Manager
3. Application freezes

Expected behavior: A clear and concise description of what you expected to happen.

Desktop (please complete the following information):
- OS: Windows 10
- Version: 2015.1
"""


def test_cross_repo_similar_boilerplate_is_not_collapsed_with_stricter_threshold() -> None:
    a = make_evidence_item(
        source_kind="github_issue",
        source_id="github_issue:EWSoftware/VSSpellChecker#30",
        source_family="github",
        published_at=T0,
        raw_text=_SPELLCHECKER_LIKE_TEXT,
    )
    b = make_evidence_item(
        source_kind="github_issue",
        source_id="github_issue:NuGet/Home#3474",
        source_family="github",
        published_at=T0,
        raw_text=_NUGET_LIKE_TEXT,
    )
    # Sanity check this fixture actually reproduces the observed shape: above
    # the same-repo threshold (which is why the old single-threshold code
    # collapsed it) but below the new cross-repo threshold.
    links_old_behavior = deduplicate_evidence([a, b], similarity_threshold=0.5)
    assert len(links_old_behavior) == 1, "fixture must score >= 0.5 to reproduce the original bug"

    links = deduplicate_evidence(
        [a, b], similarity_threshold=0.5, cross_repo_similarity_threshold=0.75
    )
    assert links == [], "different repos, generic shared boilerplate -- must not collapse"


def test_same_repo_similar_boilerplate_still_collapses_with_stricter_cross_repo_threshold() -> None:
    """Same text pair, same repo this time -- proves the fix is scoped to
    cross-repo pairs only and does not blunt same-repo sensitivity."""
    a = make_evidence_item(
        source_kind="github_issue",
        source_id="github_issue:EWSoftware/VSSpellChecker#30",
        source_family="github",
        published_at=T0,
        raw_text=_SPELLCHECKER_LIKE_TEXT,
    )
    b = make_evidence_item(
        source_kind="github_issue",
        source_id="github_issue:EWSoftware/VSSpellChecker#31",
        source_family="github",
        published_at=T0,
        raw_text=_NUGET_LIKE_TEXT,
    )
    links = deduplicate_evidence(
        [a, b], similarity_threshold=0.5, cross_repo_similarity_threshold=0.75
    )
    assert len(links) == 1, "same repo must still use the lenient same-repo threshold"
    assert links[0].duplicate_reason == "similar_text"


def test_cross_repo_threshold_omitted_reproduces_prior_single_threshold_behavior() -> None:
    """No cross_repo_similarity_threshold passed -> identical to every
    pre-existing call site and test in this file; confirms the new parameter
    is opt-in, not a silent behavior change for existing callers."""
    a = make_evidence_item(
        source_kind="github_issue",
        source_id="github_issue:EWSoftware/VSSpellChecker#30",
        source_family="github",
        published_at=T0,
        raw_text=_SPELLCHECKER_LIKE_TEXT,
    )
    b = make_evidence_item(
        source_kind="github_issue",
        source_id="github_issue:NuGet/Home#3474",
        source_family="github",
        published_at=T0,
        raw_text=_NUGET_LIKE_TEXT,
    )
    links = deduplicate_evidence([a, b], similarity_threshold=0.5)
    assert len(links) == 1
