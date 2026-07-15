from pathlib import Path

import pytest

from demand_radar.config import (
    ProductNotFoundError,
    load_product_config,
    load_product_config_by_name,
    slugify,
)

PRODUCTS_DIR = Path(__file__).resolve().parents[2] / "products"


def test_slugify_basic() -> None:
    assert slugify("WPF high memory usage") == "wpf-high-memory-usage"


def test_slugify_preserves_dotnet_and_csharp_meaning() -> None:
    assert slugify("legacy .NET code audit") == "legacy-dotnet-code-audit"
    assert slugify("C# technical debt report") == "csharp-technical-debt-report"


def test_load_own_audit_product_config() -> None:
    cfg = load_product_config_by_name("own-audit", products_dir=PRODUCTS_DIR)
    assert cfg.product == "own-audit"
    assert len(cfg.problem_queries) == 10
    assert len(cfg.personas) == 5
    assert cfg.thresholds.minimum_unique_authors == 3


def test_load_griff_product_config() -> None:
    cfg = load_product_config_by_name("griff", products_dir=PRODUCTS_DIR)
    assert cfg.product == "griff"
    assert len(cfg.problem_queries) == 10


def test_query_ids_in_fixture_match_slugified_problem_queries() -> None:
    """Guards the collector convention reporting/markdown.py's collection
    blind-spot detection relies on."""
    cfg = load_product_config_by_name("own-audit", products_dir=PRODUCTS_DIR)
    slugs = {slugify(q) for q in cfg.problem_queries}
    assert "wpf-high-memory-usage" in slugs
    assert "ndepend-alternative" in slugs


def test_missing_product_raises_clear_error() -> None:
    with pytest.raises(ProductNotFoundError):
        load_product_config(PRODUCTS_DIR / "does-not-exist.yaml")


def test_real_mode_thresholds_are_not_silently_lowered() -> None:
    """Spec section 16/29: no hidden threshold weakening for tests."""
    own_audit = load_product_config_by_name("own-audit", products_dir=PRODUCTS_DIR)
    griff = load_product_config_by_name("griff", products_dir=PRODUCTS_DIR)
    for cfg in (own_audit, griff):
        assert cfg.thresholds.minimum_unique_authors == 3
        assert cfg.thresholds.minimum_source_families == 2
        assert cfg.thresholds.minimum_problem_evidence == 3
        assert cfg.thresholds.minimum_workaround_evidence == 1
