"""Local, dependency-free near-duplicate text similarity (TF-IDF + cosine).

No external embeddings API and no heavy ML dependency — spec section 19
explicitly allows "TF-IDF + cosine similarity" as a first-class local option.
This module is deliberately small: it is used to find *near-duplicate*
reposts (deduplicate.py), not to cluster by topic (clustering groups by the
classification's problem_key instead — see graph/nodes/propose_clusters.py).
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    tokenized = [_tokenize(t) for t in texts]
    doc_freq: Counter[str] = Counter()
    for tokens in tokenized:
        doc_freq.update(set(tokens))

    n_docs = len(texts)
    vectors: list[dict[str, float]] = []
    for tokens in tokenized:
        term_freq = Counter(tokens)
        total = sum(term_freq.values()) or 1
        vector = {}
        for term, count in term_freq.items():
            idf = math.log((1 + n_docs) / (1 + doc_freq[term])) + 1.0
            vector[term] = (count / total) * idf
        vectors.append(vector)
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = a.keys() & b.keys()
    dot = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def pairwise_similarity(texts: list[str]) -> list[list[float]]:
    """Symmetric NxN cosine-similarity matrix over TF-IDF vectors of texts."""
    vectors = _tfidf_vectors(texts)
    n = len(texts)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            sim = _cosine(vectors[i], vectors[j])
            matrix[i][j] = matrix[j][i] = sim
    return matrix


def group_by_similarity(texts: list[str], threshold: float) -> list[list[int]]:
    """Union-find grouping of indices whose pairwise similarity >= threshold."""
    n = len(texts)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    matrix = pairwise_similarity(texts)
    for i in range(n):
        for j in range(i + 1, n):
            if matrix[i][j] >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())
