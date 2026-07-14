"""Structural verification: 9 pricing fetchers + 2 FileClaimRegistry wirings are
no longer stubs.

These tests mechanically assert that:
1. TODO(integration) markers have been removed from sources.py and planner.py.
2. Each of the 9 primary pricing source classes has non-trivial fetch methods.
3. planner.py imports and uses FileClaimRegistry.
4. all_sources() wraps live-price fetchers in CachedSource.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

from general_ludd.pricing_intel.models import ComputePrice, ModelPrice
from general_ludd.pricing_intel.sources import (
    AnthropicSource,
    AWSSource,
    CachedSource,
    GCPSource,
    HuggingFaceSource,
    LambdaLabsSource,
    OpenAISource,
    OpenRouterSource,
    RunPodSource,
    ZAISource,
    all_sources,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SOURCES_PY = _REPO_ROOT / "src" / "general_ludd" / "pricing_intel" / "sources.py"
PLANNER_PY = _REPO_ROOT / "src" / "general_ludd" / "scheduling" / "planner.py"

# ---------------------------------------------------------------------------
# 1. TODO(integration) markers GONE from sources.py and planner.py
# ---------------------------------------------------------------------------


def test_no_todo_integration_in_sources() -> None:
    """sources.py must contain zero # TODO(integration) markers."""
    content = SOURCES_PY.read_text()
    assert "TODO(integration)" not in content, (
        "sources.py still contains TODO(integration) — "
        "live-pricing implementations are not yet complete"
    )


def test_no_todo_integration_in_planner() -> None:
    """planner.py must contain zero # TODO(integration) markers."""
    content = PLANNER_PY.read_text()
    assert "TODO(integration)" not in content, (
        "planner.py still contains TODO(integration) — "
        "FileClaimRegistry wiring is not yet complete"
    )


# ---------------------------------------------------------------------------
# 2. All 9 primary providers have non-trivial fetch methods (no stubs)
# ---------------------------------------------------------------------------

# Each entry: (class, has_model_prices: bool, has_compute_prices: bool)
_PROVIDERS: list[tuple[type[Any], bool, bool]] = [
    (OpenRouterSource, True, False),
    (AnthropicSource, True, False),
    (OpenAISource, True, False),
    (RunPodSource, False, True),
    (LambdaLabsSource, False, True),
    (AWSSource, False, True),
    (GCPSource, False, True),
    (HuggingFaceSource, False, True),
    (ZAISource, True, False),
]


def test_nine_providers_registered() -> None:
    """Exactly 9 primary provider classes are enumerated in _PROVIDERS."""
    assert len(_PROVIDERS) == 9, f"Expected 9 providers, got {len(_PROVIDERS)}"


def test_all_providers_have_provider_slug() -> None:
    """Every provider returns a non-empty string slug."""
    for cls, _, _ in _PROVIDERS:
        slug = cls().provider_slug()
        assert isinstance(slug, str) and slug, (
            f"{cls.__name__}.provider_slug() returned empty/falsy value"
        )


def test_all_providers_have_billing() -> None:
    """Every provider returns a valid billing object."""
    for cls, _, _ in _PROVIDERS:
        billing = cls().billing()
        assert billing.provider, f"{cls.__name__}.billing() has empty provider"


def test_model_providers_return_non_empty_prices() -> None:
    """Providers that offer models must return non-empty ModelPrice lists."""
    for cls, has_models, _ in _PROVIDERS:
        if not has_models:
            continue
        prices = cls().fetch_model_prices()
        assert isinstance(prices, list), (
            f"{cls.__name__}.fetch_model_prices() returned non-list"
        )
        assert len(prices) > 0, (
            f"{cls.__name__}.fetch_model_prices() returned empty list — stub detected"
        )
        assert all(isinstance(p, ModelPrice) for p in prices), (
            f"{cls.__name__}.fetch_model_prices() returned non-ModelPrice items"
        )


def test_compute_providers_return_non_empty_prices() -> None:
    """Providers that offer compute must return non-empty ComputePrice lists."""
    for cls, _, has_compute in _PROVIDERS:
        if not has_compute:
            continue
        prices = cls().fetch_compute_prices()
        assert isinstance(prices, list), (
            f"{cls.__name__}.fetch_compute_prices() returned non-list"
        )
        assert len(prices) > 0, (
            f"{cls.__name__}.fetch_compute_prices() returned empty list — stub detected"
        )
        assert all(isinstance(p, ComputePrice) for p in prices), (
            f"{cls.__name__}.fetch_compute_prices() returned non-ComputePrice items"
        )


def test_non_model_providers_return_empty_model_prices() -> None:
    """Compute-only providers must return [] from fetch_model_prices."""
    for cls, has_models, _ in _PROVIDERS:
        if has_models:
            continue
        prices = cls().fetch_model_prices()
        assert prices == [], (
            f"{cls.__name__}.fetch_model_prices() should return [] "
            f"(compute-only provider)"
        )


def test_non_compute_providers_return_empty_compute_prices() -> None:
    """Model-only providers must return [] from fetch_compute_prices."""
    for cls, _, has_compute in _PROVIDERS:
        if has_compute:
            continue
        prices = cls().fetch_compute_prices()
        assert prices == [], (
            f"{cls.__name__}.fetch_compute_prices() should return [] "
            f"(model-only provider)"
        )


# ---------------------------------------------------------------------------
# 3. FileClaimRegistry imported AND used in planner.py
# ---------------------------------------------------------------------------


def test_planner_imports_file_claim_registry() -> None:
    """planner.py must contain an import of FileClaimRegistry."""
    content = PLANNER_PY.read_text()
    has_import = (
        "from general_ludd.coordination.file_claims import FileClaimRegistry" in content
        or "from general_ludd.coordination import FileClaimRegistry" in content
    )
    assert has_import, (
        "planner.py does NOT import FileClaimRegistry — "
        "live file-claim integration is not wired"
    )


def test_planner_uses_file_claim_registry() -> None:
    """planner.py must use FileClaimRegistry (not just import it).

    'Use' means one of:
      - Instantiated: FileClaimRegistry(...)
      - Passed as parameter: __init__(self, registry: FileClaimRegistry, ...)
      - Referenced in a method body.
    """
    content = PLANNER_PY.read_text()

    # Parse the AST to check for usage beyond the import statement itself.
    tree = ast.parse(content)
    import_line_nos: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_line_nos.add(node.lineno)

    # Count "FileClaimRegistry" occurrences outside import lines.
    lines = content.splitlines()
    non_import_occurrences = 0
    for i, line in enumerate(lines, start=1):
        if i in import_line_nos:
            continue
        if "FileClaimRegistry" in line:
            non_import_occurrences += 1

    assert (
        non_import_occurrences >= 1
    ), (
        "planner.py imports FileClaimRegistry but never uses it — "
        "the parameter/instantiation wiring is missing"
    )


# ---------------------------------------------------------------------------
# 4. all_sources() wraps live-price fetchers in CachedSource
# ---------------------------------------------------------------------------


def test_all_sources_includes_cachedsource_instances() -> None:
    """all_sources() must include at least one CachedSource wrapper."""
    sources = all_sources()
    cached = [s for s in sources if isinstance(s, CachedSource)]
    assert len(cached) >= 1, (
        "all_sources() contains zero CachedSource instances — "
        "TTL cache + static fallback not wired for any live fetcher"
    )
    # Verify CachedSource wraps a live source (not just a static one)
    for cs in cached:
        assert cs._live is not None  # type: ignore[attr-defined]


def test_cachedsource_ttl_is_positive() -> None:
    """Every CachedSource in all_sources() must have a positive TTL."""
    sources = all_sources()
    for src in sources:
        if not isinstance(src, CachedSource):
            continue
        assert src._ttl > 0, (  # type: ignore[attr-defined]
            f"CachedSource({src.provider_slug()}) has non-positive TTL: {src._ttl}"
        )


def test_cachedsource_delegates_to_live() -> None:
    """CachedSource in all_sources() delegates provider_slug/billing to live."""
    sources = all_sources()
    for src in sources:
        if not isinstance(src, CachedSource):
            continue
        slug = src.provider_slug()
        assert isinstance(slug, str) and slug
        billing = src.billing()
        assert billing.provider == slug, (
            f"CachedSource({slug}) billing.provider={billing.provider} != slug"
        )


def test_cache_hit_returns_cached_without_network() -> None:
    """After first fetch, second call within TTL returns cached data (no live call)."""
    # Use a trivial source to verify cache-hit path.
    class _FakeSource:
        _count = 0

        def provider_slug(self) -> str:
            return "fake"

        def billing(self) -> Any:
            from general_ludd.pricing_intel.models import BillingGranularity, BillingTerms, ProviderBilling
            return ProviderBilling(
                provider="fake", granularity=BillingGranularity.per_token,
                terms=BillingTerms.postpaid_per_use, currency="USD",
            )

        def fetch_model_prices(self) -> list[ModelPrice]:
            _FakeSource._count += 1
            return [ModelPrice(
                provider="fake", model_id="fake-1",
                input_usd_per_1k=0.001, output_usd_per_1k=0.002,
                fetched_at=0.0, source="test",
            )]

        def fetch_compute_prices(self) -> list[ComputePrice]:
            return []

    cached = CachedSource(_FakeSource(), ttl_seconds=60.0)
    first = cached.fetch_model_prices()
    assert len(first) == 1
    assert _FakeSource._count == 1

    # Second call within TTL — must NOT call live source again.
    second = cached.fetch_model_prices()
    assert len(second) == 1
    assert _FakeSource._count == 1, (
        "CachedSource re-fetched from live within TTL — cache TTL not honored"
    )


def test_cache_bypass_with_refresh() -> None:
    """refresh=True on fetch_model_prices bypasses cache."""
    class _FakeSource:
        _count = 0

        def provider_slug(self) -> str:
            return "fake_rf"

        def billing(self) -> Any:
            from general_ludd.pricing_intel.models import BillingGranularity, BillingTerms, ProviderBilling
            return ProviderBilling(
                provider="fake_rf", granularity=BillingGranularity.per_token,
                terms=BillingTerms.postpaid_per_use, currency="USD",
            )

        def fetch_model_prices(self) -> list[ModelPrice]:
            _FakeSource._count += 1
            return [ModelPrice(
                provider="fake_rf", model_id="fake-1",
                input_usd_per_1k=0.001, output_usd_per_1k=0.002,
                fetched_at=0.0, source="test",
            )]

        def fetch_compute_prices(self) -> list[ComputePrice]:
            return []

    cached = CachedSource(_FakeSource(), ttl_seconds=60.0)
    cached.fetch_model_prices()
    assert _FakeSource._count == 1
    cached.fetch_model_prices(refresh=True)
    assert _FakeSource._count == 2, (
        "refresh=True did not bypass cache — CachedSource returned stale data"
    )
