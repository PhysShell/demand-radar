"""Scoring weights, acceptance thresholds, and product configuration.

Weights and thresholds live here (or in a product's YAML), never inside a
prompt string — see docs/scoring.md. Defaults below are the *real-mode*
defaults from the task brief section 16; there is deliberately no separate
"test mode" that silently lowers them. A product config may override them,
but every override is an explicit, reviewable YAML value, not a hidden
constant swapped in for CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import Field

from demand_radar.models import StrictModel

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_DOMAIN_REPLACEMENTS = [
    (re.compile(r"\.net\b", re.IGNORECASE), "dotnet"),
    (re.compile(r"c#", re.IGNORECASE), "csharp"),
]


def slugify(text: str) -> str:
    """Collector convention: query_id should equal slugify(problem_query),
    which is what fixtures/mixed-demand-signals.jsonl follows by hand. Used
    by reporting to flag problem_queries with zero matching evidence
    ("collection blind spots") — see reporting/markdown.py.

    Domain replacements run before generic stripping so ".NET"/"C#" keep
    their meaning (dotnet/csharp) instead of collapsing to "net"/"c".
    """
    working = text.strip()
    for pattern, replacement in _DOMAIN_REPLACEMENTS:
        working = pattern.sub(replacement, working)
    return _SLUG_STRIP_RE.sub("-", working.lower()).strip("-")


class ScoringWeights(StrictModel):
    """Coefficients for scoring/demand_score.py. See docs/scoring.md."""

    frequency: float = 1.0
    recent_growth: float = 1.5
    pain: float = 2.0
    urgency: float = 1.5
    commercial_intent: float = 2.5
    commitment: float = 4.0
    product_fit: float = 2.0
    source_diversity: float = 2.0
    saturation: float = -1.5
    duplication: float = -2.0


class AcceptanceThresholds(StrictModel):
    """Deterministic experiment_ready gate — scoring/judge.py. Section 16."""

    minimum_unique_authors: int = Field(default=3, ge=1)
    minimum_source_families: int = Field(default=2, ge=1)
    minimum_problem_evidence: int = Field(default=3, ge=1)
    minimum_workaround_evidence: int = Field(default=1, ge=0)


class DeduplicationConfig(StrictModel):
    """ingest/deduplicate.py similarity-candidate threshold.

    0.5 is calibrated against ingest/similarity.py's TF-IDF cosine measure on
    short evidence-item text: a genuine paraphrase-repost of the same post
    scores ~0.55-0.6, while distinct posts on the same topic top out ~0.2 (see
    tests/unit/test_similarity.py). Retune only against that measurement, not
    by feel.
    """

    similarity_threshold: float = Field(default=0.5, ge=0, le=1)


class ProductConfig(StrictModel):
    """One products/*.yaml file — spec section 11."""

    config_version: int = 1
    product: str = Field(pattern=r"^[a-z0-9-]+$")
    display_name: str
    problem_queries: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)
    positive_examples: list[str] = Field(default_factory=list)
    negative_examples: list[str] = Field(default_factory=list)
    thresholds: AcceptanceThresholds = Field(default_factory=AcceptanceThresholds)
    deduplication: DeduplicationConfig = Field(default_factory=DeduplicationConfig)
    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)


class ProductNotFoundError(FileNotFoundError):
    pass


def load_product_config(path: Path) -> ProductConfig:
    """Load and validate one product YAML file. Raises on missing/malformed input."""
    if not path.is_file():
        raise ProductNotFoundError(f"product config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"product config must be a YAML mapping: {path}")
    return ProductConfig.model_validate(raw)


def load_product_config_by_name(name: str, products_dir: Path | None = None) -> ProductConfig:
    products_dir = products_dir or default_products_dir()
    return load_product_config(products_dir / f"{name}.yaml")


def default_products_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "products"
