"""D.15 — Additional tests for CachedSource TTL cache + static fallback.

Covers gaps not in test_pricing_cache_and_fallback.py:
  - staleness_text() exact boundary behaviour (1h, 24h, future timestamp)
  - all_sources() integration: CachedSource wrappers present for RunPod/AWS/GCP
  - Dual failure: live + static both fail, no stale cache → []
  - fetched_at timestamps preserved on cached returns
  - Empty live + no static fallback → cached empty, no retry on compute too
  - Compute cache independence under TTL expiry
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import (
    AWSPricingSource,
    AWSSource,
    CachedSource,
    GCPPricingSource,
    GCPSource,
    RunPodPricingSource,
    RunPodSource,
    all_sources,
    staleness_text,
)


# ---------------------------------------------------------------------------
# staleness_text() boundary tests
# ---------------------------------------------------------------------------


class TestStalenessTextBoundaries:
    def test_future_timestamp_returns_fresh(self) -> None:
        assert staleness_text(time.time() + 100000) == "fresh"

    def test_at_exactly_1_hour_boundary_returns_stale(self) -> None:
        t = time.time() - 3601
        assert staleness_text(t) == "stale"

    def test_at_exactly_24_hour_boundary_returns_very_stale(self) -> None:
        t = time.time() - 86401
        assert staleness_text(t) == "very_stale"

    def test_just_under_1_hour_is_fresh(self) -> None:
        t = time.time() - 3599
        assert staleness_text(t) == "fresh"

    def test_between_1h_and_24h_is_stale(self) -> None:
        t = time.time() - 7200
        assert staleness_text(t) == "stale"


# ---------------------------------------------------------------------------
# all_sources() integration
# ---------------------------------------------------------------------------


class TestAllSourcesHasCachedWrappers:
    def test_all_sources_returns_non_empty_list(self) -> None:
        sources = all_sources()
        assert len(sources) > 0

    def test_cached_source_for_runpod_present(self) -> None:
        sources = all_sources()
        cached = [s for s in sources if isinstance(s, CachedSource) and s.provider_slug() == "runpod_live"]
        assert len(cached) >= 1

    def test_cached_source_for_aws_present(self) -> None:
        sources = all_sources()
        cached = [s for s in sources if isinstance(s, CachedSource) and s.provider_slug() == "aws_live"]
        assert len(cached) >= 1

    def test_cached_source_for_gcp_present(self) -> None:
        sources = all_sources()
        cached = [s for s in sources if isinstance(s, CachedSource) and s.provider_slug() == "gcp_live"]
        assert len(cached) >= 1

    def test_both_cached_and_uncached_runpod_sources_exist(self) -> None:
        sources = all_sources()
        slugs = {s.provider_slug() for s in sources}
        assert "runpod_live" in slugs
        assert "runpod" in slugs

    def test_all_sources_type_consistency(self) -> None:
        from general_ludd.pricing_intel.sources import PricingSource
        for src in all_sources():
            assert isinstance(src, PricingSource)


# ---------------------------------------------------------------------------
# Dual failure: live + static both fail, no stale cache
# ---------------------------------------------------------------------------


class _FakeFailingSource:
    def provider_slug(self) -> str:
        return "failing"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="failing", granularity=BillingGranularity.per_second,
            terms=BillingTerms.prepaid_balance, currency="USD",
            min_charge=None, spot_available=False, notes="",
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        raise RuntimeError("always down")

    def fetch_compute_prices(self) -> list[ComputePrice]:
        raise RuntimeError("always down")


class TestDualFailure:
    def test_both_live_and_static_fail_model_returns_empty(self) -> None:
        cached = CachedSource(
            live=_FakeFailingSource(),
            static_fallback=_FakeFailingSource(),
            ttl_seconds=3600,
        )
        prices = cached.fetch_model_prices()
        assert prices == []

    def test_both_live_and_static_fail_compute_returns_empty(self) -> None:
        cached = CachedSource(
            live=_FakeFailingSource(),
            static_fallback=_FakeFailingSource(),
            ttl_seconds=3600,
        )
        prices = cached.fetch_compute_prices()
        assert prices == []


# ---------------------------------------------------------------------------
# fetched_at timestamps preserved on cached results
# ---------------------------------------------------------------------------


class _FakeLiveWithTimestamp:
    def __init__(self, fetched_at: float) -> None:
        self._fetched_at = fetched_at

    def provider_slug(self) -> str:
        return "ts_live"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="ts_live", granularity=BillingGranularity.per_second,
            terms=BillingTerms.prepaid_balance, currency="USD",
            min_charge=None, spot_available=False, notes="",
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        return [
            ModelPrice(provider="ts_live", model_id="m1",
                       input_usd_per_1k=0.01, output_usd_per_1k=0.02,
                       fetched_at=self._fetched_at, source="https://ts.dev")
        ]

    def fetch_compute_prices(self) -> list[ComputePrice]:
        return [
            ComputePrice(provider="ts_live", sku="g1", usd_per_unit=0.5 / 3600,
                         granularity=BillingGranularity.per_second, spot=False,
                         terms=BillingTerms.prepaid_balance,
                         fetched_at=self._fetched_at, source="https://ts.dev")
        ]


class TestFetchedAtPreservation:
    def test_cached_model_prices_preserve_original_fetched_at(self) -> None:
        fixed_ts = 1700000000.0
        cached = CachedSource(
            live=_FakeLiveWithTimestamp(fixed_ts),
            static_fallback=None,
            ttl_seconds=3600,
        )
        p1 = cached.fetch_model_prices()
        p2 = cached.fetch_model_prices()
        assert p1[0].fetched_at == pytest.approx(fixed_ts)
        assert p2[0].fetched_at == pytest.approx(fixed_ts)

    def test_cached_compute_prices_preserve_original_fetched_at(self) -> None:
        fixed_ts = 1700000000.0
        cached = CachedSource(
            live=_FakeLiveWithTimestamp(fixed_ts),
            static_fallback=None,
            ttl_seconds=3600,
        )
        p1 = cached.fetch_compute_prices()
        p2 = cached.fetch_compute_prices()
        assert p1[0].fetched_at == pytest.approx(fixed_ts)
        assert p2[0].fetched_at == pytest.approx(fixed_ts)


# ---------------------------------------------------------------------------
# Empty live + no static fallback → cache empty, no retry
# ---------------------------------------------------------------------------


class _EmptySource:
    def provider_slug(self) -> str:
        return "empty"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="empty", granularity=BillingGranularity.per_second,
            terms=BillingTerms.prepaid_balance, currency="USD",
            min_charge=None, spot_available=False, notes="",
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        return []

    def fetch_compute_prices(self) -> list[ComputePrice]:
        return []


class TestEmptyLiveNoFallback:
    def test_empty_model_no_fallback_is_cached_not_retried(self) -> None:
        cached = CachedSource(live=_EmptySource(), static_fallback=None, ttl_seconds=3600)
        with patch.object(_EmptySource, "fetch_model_prices", wraps=_EmptySource().fetch_model_prices) as spy:
            cached.fetch_model_prices()
            cached.fetch_model_prices()
            assert spy.call_count == 1

    def test_empty_compute_no_fallback_is_cached_not_retried(self) -> None:
        cached = CachedSource(live=_EmptySource(), static_fallback=None, ttl_seconds=3600)
        with patch.object(_EmptySource, "fetch_compute_prices", wraps=_EmptySource().fetch_compute_prices) as spy:
            cached.fetch_compute_prices()
            cached.fetch_compute_prices()
            assert spy.call_count == 1


# ---------------------------------------------------------------------------
# Compute cache independence under TTL expiry
# ---------------------------------------------------------------------------


class _CountedSource:
    def __init__(self) -> None:
        self.model_calls = 0
        self.compute_calls = 0

    def provider_slug(self) -> str:
        return "counted"

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider="counted", granularity=BillingGranularity.per_second,
            terms=BillingTerms.prepaid_balance, currency="USD",
            min_charge=None, spot_available=False, notes="",
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        self.model_calls += 1
        return [ModelPrice(provider="counted", model_id="mm", input_usd_per_1k=0.001,
                           output_usd_per_1k=0.002, fetched_at=time.time(), source="x")]

    def fetch_compute_prices(self) -> list[ComputePrice]:
        self.compute_calls += 1
        return [ComputePrice(provider="counted", sku="cc", usd_per_unit=0.1 / 3600,
                             granularity=BillingGranularity.per_second, spot=False,
                             terms=BillingTerms.prepaid_balance, fetched_at=time.time(), source="x")]


class TestComputeCacheIndependenceUnderTTL:
    def test_model_ttl_expiry_does_not_force_compute_refetch(self) -> None:
        live = _CountedSource()
        cached = CachedSource(live=live, static_fallback=None, ttl_seconds=0)  # always expired
        cached.fetch_compute_prices()
        assert live.compute_calls == 1
        cached.fetch_model_prices()
        assert live.model_calls == 1
        cached.fetch_model_prices()
        assert live.model_calls == 2
        cached.fetch_compute_prices()
        assert live.compute_calls == 2  # compute also expired, but NOT because model did

    def test_compute_ttl_expiry_does_not_force_model_refetch(self) -> None:
        live = _CountedSource()
        cached = CachedSource(live=live, static_fallback=None, ttl_seconds=0)
        cached.fetch_model_prices()
        assert live.model_calls == 1
        cached.fetch_compute_prices()
        assert live.compute_calls == 1
        cached.fetch_compute_prices()
        assert live.compute_calls == 2
        cached.fetch_model_prices()
        assert live.model_calls == 2  # model also expired, but NOT because compute did
