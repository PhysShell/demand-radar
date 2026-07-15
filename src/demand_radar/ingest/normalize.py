"""Deterministic text/URL normalization. No LLM involved anywhere here.

Two different normalizations, deliberately not conflated:

- `normalize_text` produces the form used for hashing and similarity — it is
  never shown to a human and never sent to a model as "the" content.
- `canonical_url` produces the form used for URL-identity dedup (rule 1 in
  spec section 18) — stripped of tracking params, not a display URL.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_WHITESPACE_RE = re.compile(r"\s+")

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "ref_src",
    "igshid",
    "fbclid",
    "gclid",
}


def normalize_text(raw_text: str) -> str:
    """Case/whitespace/unicode-form-collapsed text, for hashing and similarity."""
    text = unicodedata.normalize("NFKC", raw_text)
    text = text.strip().lower()
    text = _WHITESPACE_RE.sub(" ", text)
    return text


def content_hash(normalized_text: str) -> str:
    digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def canonical_url(url: str | None) -> str | None:
    """Strip tracking params and trailing slash; lowercase scheme/host only."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return url.strip()
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    kept_params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(kept_params))
    return urlunsplit((scheme, netloc, path, query, ""))


def stable_author_hash(raw_author: str | None, fallback_seed: str) -> str:
    """Salted, one-way hash of the raw author identity. Raw identity is never stored."""
    basis = raw_author if raw_author else f"anonymous:{fallback_seed}"
    digest = hashlib.sha256(f"demand-radar-author-salt:{basis}".encode()).hexdigest()
    return f"sha256:{digest}"


def evidence_id_for(product: str, source_kind: str, source_id: str) -> str:
    """Deterministic id: re-importing the same raw record yields the same id."""
    digest = hashlib.sha256(f"{product}:{source_kind}:{source_id}".encode()).hexdigest()
    return f"ev_{digest[:16]}"
