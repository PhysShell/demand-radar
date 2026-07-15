from demand_radar.ingest.normalize import (
    canonical_url,
    content_hash,
    evidence_id_for,
    normalize_text,
    stable_author_hash,
)


def test_normalize_text_collapses_whitespace_and_case() -> None:
    assert normalize_text("  Hello   World\n\n") == "hello world"


def test_normalize_text_is_deterministic() -> None:
    text = "Our WPF app leaks memory in production."
    assert normalize_text(text) == normalize_text(text)


def test_content_hash_stable_across_calls() -> None:
    normalized = normalize_text("same text")
    assert content_hash(normalized) == content_hash(normalized)
    assert content_hash(normalized).startswith("sha256:")


def test_content_hash_same_text_different_whitespace_matches() -> None:
    a = content_hash(normalize_text("Hello   World"))
    b = content_hash(normalize_text("hello world"))
    assert a == b


def test_content_hash_different_text_differs() -> None:
    a = content_hash(normalize_text("hello world"))
    b = content_hash(normalize_text("goodbye world"))
    assert a != b


def test_canonical_url_strips_tracking_params_and_trailing_slash() -> None:
    a = canonical_url("https://Example.com/posts/1/?utm_source=reddit&ref=abc")
    b = canonical_url("https://example.com/posts/1")
    assert a == b


def test_canonical_url_none_stays_none() -> None:
    assert canonical_url(None) is None


def test_canonical_url_keeps_meaningful_query_params() -> None:
    result = canonical_url("https://example.com/search?q=wpf+leak&utm_source=x")
    assert "q=wpf" in result
    assert "utm_source" not in result


def test_stable_author_hash_deterministic_and_never_raw() -> None:
    h1 = stable_author_hash("some_username", fallback_seed="ev1")
    h2 = stable_author_hash("some_username", fallback_seed="ev1")
    assert h1 == h2
    assert "some_username" not in h1
    assert h1.startswith("sha256:")


def test_stable_author_hash_anonymous_uses_fallback_seed() -> None:
    h1 = stable_author_hash(None, fallback_seed="ev-a")
    h2 = stable_author_hash(None, fallback_seed="ev-b")
    assert h1 != h2


def test_evidence_id_for_deterministic() -> None:
    a = evidence_id_for("own-audit", "reddit_post", "abc-123")
    b = evidence_id_for("own-audit", "reddit_post", "abc-123")
    assert a == b
    assert a.startswith("ev_")


def test_evidence_id_for_differs_by_product() -> None:
    a = evidence_id_for("own-audit", "reddit_post", "abc-123")
    b = evidence_id_for("griff", "reddit_post", "abc-123")
    assert a != b
