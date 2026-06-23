"""TDD tests for InfraTracker.gpu_cost_usd(): PricingCatalog primary, static fallback.

Covers the integration point documented at catalog.py:19:
    InfraTracker.gpu_cost_usd() -> use catalog.compute_price("runpod", sku)
    to replace the static INFRA_PRICING dict.

Resolution order:
  1. If a PricingCatalog is injected, query it (primary).
  2. On any miss / error / absent catalog / non-time granularity, fall back
     to the static infra_cost_usd("gpu_second", ...) in infra/pricing.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from general_ludd.infra.pricing import INFRA_PRICING, InfraTracker
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
)


class _FakeCatalog:
    """Deterministic PricingCatalog stand-in (no network)."""

    def __init__(
        self,
        prices: Sequence[ComputePrice | None] | None = None,
        boom: bool = False,
    ) -> None:
        self._prices = list(prices) if prices is not None else []
        self._boom = boom
        self.calls: list[tuple[str, str, bool]] = []

    def compute_price(
        self,
        provider: str,
        sku: str,
        spot: bool = False,
        refresh: bool = False,
    ) -> ComputePrice | None:
        self.calls.append((provider, sku, spot))
        if self._boom:
            raise RuntimeError("network down")
        if not self._prices:
            return None
        return self._prices.pop(0)


def _cp(
    provider: str,
    sku: str,
    usd_per_unit: float,
    granularity: BillingGranularity = BillingGranularity.per_second,
    spot: bool = False,
) -> ComputePrice:
    return ComputePrice(
        provider=provider,
        sku=sku,
        usd_per_unit=usd_per_unit,
        granularity=granularity,
        spot=spot,
        terms=BillingTerms.prepaid_balance,
        source="fake-test-source",
    )


_STATIC_RATE = INFRA_PRICING["gpu_second"]


class TestGpuCostUsdFallback:
    """When no catalog is injected, the static infra pricing table must be used."""

    def test_no_catalog_uses_static_table(self) -> None:
        tracker = InfraTracker()
        cost = tracker.gpu_cost_usd("A100-SXM4-80GB-1x", 3600)
        assert cost == pytest.approx(_STATIC_RATE * 3600)

    def test_no_catalog_zero_seconds_is_zero_cost(self) -> None:
        tracker = InfraTracker()
        assert tracker.gpu_cost_usd("anything", 0) == pytest.approx(0.0)


class TestGpuCostUsdCatalogPrimary:
    """When a catalog is injected and returns a hit, it overrides the static table."""

    def test_catalog_hit_overrides_static(self) -> None:
        # Static says 0.00083/s; catalog says 0.001/s.
        cat = _FakeCatalog(prices=[_cp("runpod", "A100", 0.001)])
        tracker = InfraTracker(catalog=cat)
        cost = tracker.gpu_cost_usd("A100", 1000)
        assert cost == pytest.approx(0.001 * 1000)
        assert cost != pytest.approx(_STATIC_RATE * 1000)

    def test_catalog_miss_falls_back_to_static(self) -> None:
        cat = _FakeCatalog(prices=[None])
        tracker = InfraTracker(catalog=cat)
        cost = tracker.gpu_cost_usd("A100", 1000)
        assert cost == pytest.approx(_STATIC_RATE * 1000)

    def test_catalog_raising_falls_back_to_static(self) -> None:
        tracker = InfraTracker(catalog=_FakeCatalog(boom=True))
        cost = tracker.gpu_cost_usd("A100", 1000)
        assert cost == pytest.approx(_STATIC_RATE * 1000)


class TestGpuCostUsdGranularityNormalization:
    """Catalog prices arrive in various granularities; all normalize to per-second."""

    def test_per_second_price_used_directly(self) -> None:
        cat = _FakeCatalog(
            prices=[_cp("runpod", "A100", 0.001, BillingGranularity.per_second)]
        )
        tracker = InfraTracker(catalog=cat)
        cost = tracker.gpu_cost_usd("A100", 100)
        assert cost == pytest.approx(0.001 * 100)

    def test_per_hour_price_converted_to_seconds(self) -> None:
        # $3.60/hour over 3600s == $3.60 total.
        cat = _FakeCatalog(
            prices=[_cp("runpod", "A100", 3.60, BillingGranularity.per_hour)]
        )
        tracker = InfraTracker(catalog=cat)
        cost = tracker.gpu_cost_usd("A100", 3600)
        assert cost == pytest.approx(3.60)

    def test_per_minute_price_converted_to_seconds(self) -> None:
        # $0.06/minute over 60s == $0.06 total.
        cat = _FakeCatalog(
            prices=[_cp("runpod", "A100", 0.06, BillingGranularity.per_minute)]
        )
        tracker = InfraTracker(catalog=cat)
        cost = tracker.gpu_cost_usd("A100", 60)
        assert cost == pytest.approx(0.06)

    def test_non_time_granularity_falls_back_to_static(self) -> None:
        # per_token granularity makes no sense for GPU seconds; fall back.
        cat = _FakeCatalog(
            prices=[_cp("runpod", "A100", 99.0, BillingGranularity.per_token)]
        )
        tracker = InfraTracker(catalog=cat)
        cost = tracker.gpu_cost_usd("A100", 1000)
        assert cost == pytest.approx(_STATIC_RATE * 1000)


class TestGpuCostUsdProviderResolution:
    def test_default_provider_used_when_omitted(self) -> None:
        cat = _FakeCatalog(prices=[_cp("runpod", "A100", 0.001)])
        tracker = InfraTracker(catalog=cat, default_provider="runpod")
        tracker.gpu_cost_usd("A100", 100)
        assert cat.calls[0][0] == "runpod"

    def test_custom_default_provider_used(self) -> None:
        cat = _FakeCatalog(prices=[_cp("lambda_labs", "A100", 0.001)])
        tracker = InfraTracker(catalog=cat, default_provider="lambda_labs")
        tracker.gpu_cost_usd("A100", 100)
        assert cat.calls[0][0] == "lambda_labs"

    def test_explicit_provider_overrides_default(self) -> None:
        cat = _FakeCatalog(prices=[_cp("aws", "p4d.24xlarge", 0.002)])
        tracker = InfraTracker(catalog=cat, default_provider="runpod")
        tracker.gpu_cost_usd("p4d.24xlarge", 100, provider="aws")
        assert cat.calls[0][0] == "aws"


class TestGpuCostUsdSpotFlag:
    def test_spot_false_passed_to_catalog_by_default(self) -> None:
        cat = _FakeCatalog(prices=[_cp("runpod", "A100", 0.001, spot=False)])
        tracker = InfraTracker(catalog=cat)
        tracker.gpu_cost_usd("A100", 100)
        assert cat.calls[0][2] is False

    def test_spot_true_passed_to_catalog(self) -> None:
        cat = _FakeCatalog(prices=[_cp("runpod", "A100-spot", 0.0005, spot=True)])
        tracker = InfraTracker(catalog=cat)
        tracker.gpu_cost_usd("A100", 100, spot=True)
        assert cat.calls[0][2] is True


class TestCatalogInjectionDefault:
    def test_construct_without_catalog_is_backwards_compatible(self) -> None:
        tracker = InfraTracker()
        cost = tracker.gpu_cost_usd("A100", 1000)
        assert cost == pytest.approx(_STATIC_RATE * 1000)

    def test_default_default_provider_is_runpod(self) -> None:
        # When a catalog is present and no provider is given, "runpod" is queried.
        cat = _FakeCatalog(prices=[_cp("runpod", "A100", 0.001)])
        tracker = InfraTracker(catalog=cat)
        tracker.gpu_cost_usd("A100", 100)
        assert cat.calls[0][0] == "runpod"
