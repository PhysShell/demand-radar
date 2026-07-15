import pytest

from demand_radar.ingest.similarity import group_by_similarity, pairwise_similarity

DAVE_ORIGINAL = (
    "Our WPF app leaks memory in production. dotMemory shows retained "
    "objects but not why they're retained or which subscription is "
    "holding the reference. I ended up writing a custom Roslyn analyzer "
    "to walk the ownership graph myself because nothing on the market "
    "does this."
)
DAVE_REWORD = (
    "My WPF app is leaking memory in prod. dotMemory shows me retained "
    "objects but never why they're retained, or which subscription is "
    "holding the reference. So I wrote my own Roslyn-based analyzer to "
    "trace ownership, since nothing on the market does that."
)
UNRELATED = ".NET 10 Preview 3 is now available with performance improvements to the GC."


def test_identical_text_scores_one() -> None:
    matrix = pairwise_similarity([DAVE_ORIGINAL, DAVE_ORIGINAL])
    assert matrix[0][1] == pytest.approx(1.0)


def test_paraphrase_scores_above_unrelated() -> None:
    matrix = pairwise_similarity([DAVE_ORIGINAL, DAVE_REWORD, UNRELATED])
    paraphrase_score = matrix[0][1]
    unrelated_score = matrix[0][2]
    assert paraphrase_score > 0.5
    assert unrelated_score < 0.2
    assert paraphrase_score > unrelated_score * 2


def test_group_by_similarity_groups_paraphrase_not_unrelated() -> None:
    groups = group_by_similarity([DAVE_ORIGINAL, DAVE_REWORD, UNRELATED], threshold=0.5)
    groups_as_sets = [set(g) for g in groups]
    assert {0, 1} in groups_as_sets
    assert {2} in groups_as_sets


def test_empty_and_single_text_do_not_crash() -> None:
    assert pairwise_similarity([]) == []
    assert pairwise_similarity(["only one"]) == [[1.0]]


def test_similarity_is_symmetric() -> None:
    matrix = pairwise_similarity([DAVE_ORIGINAL, DAVE_REWORD, UNRELATED])
    for i in range(3):
        for j in range(3):
            assert matrix[i][j] == matrix[j][i]
