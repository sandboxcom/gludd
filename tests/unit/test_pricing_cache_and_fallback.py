"""Tests for CachedSource — TTL cache + static fallback for live pricing fetchers.

Covers:
  - Cache hit: second call returns cached result (no re-fetch)
  - Cache TTL expiry: stale cache triggers re-fetch
  - Static fallback on fetch failure: live source errors → static prices returned
  - Staleness metadata: fetcher emits age/delta alongside prices
  - CachedSource delegates provider_slug() and billing() to the live source
  - fetch_model_prices and fetch_compute_prices are both cached independently
  - Empty live result ([]) still gets cached (not retried on every call)
  - Live success → cache populated; live failure → fallback to static source
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
    PricingSource,
    RunPodPricingSource,
    RunPodSource,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_BILLING = ProviderBilling(
    provider="mock_live",
    granularity=BillingGranularity.per_second,
    terms=BillingTerms.prepaid_balance,
    currency="USD",
    min_charge=None,
    spot_available=True,
    notes="Mock live source billing.",
)

_MOCK_FALLBACK_BILLING = ProviderBilling(
    provider="mock_static",
    granularity=BillingGranularity.per_second,
    terms=BillingTerms.prepaid_balance,
    currency="USD",
    min_charge=None,
    spot_available=True,
    notes="Mock static fallback billing.",
)


class _FakeLiveSource:
    """A fake PricingSource for testing cache/fallback behavior.

    Calls to fetch_model_prices / fetch_compute_prices are tracked via a call
    counter so tests can assert on cache-hit-vs-fetch behavior.
    """

    def __init__(self) -> None:
        self.model_call_count = 0
        self.compute_call_count = 0
        self._model_prices: list[ModelPrice] = []
        self._compute_prices: list[ComputePrice] = []

    def provider_slug(self) -> str:
        return "mock_live"

    def billing(self) -> ProviderBilling:
        return _MOCK_BILLING

    def fetch_model_prices(self) -> list[ModelPrice]:
        self.model_call_count += 1
        return list(self._model_prices)

    def fetch_compute_prices(self) -> list[ComputePrice]:
        self.compute_call_count += 1
        return list(self._compute_prices)


class _FakeStaticSource:
    """A fake PricingSource used as the static fallback."""

    def __init__(self) -> None:
        self._model_prices: list[ModelPrice] = []
        self._compute_prices: list[ComputePrice] = []

    def provider_slug(self) -> str:
        return "mock_static"

    def billing(self) -> ProviderBilling:
        return _MOCK_FALLBACK_BILLING

    def fetch_model_prices(self) -> list[ModelPrice]:
        return list(self._model_prices)

    def fetch_compute_prices(self) -> list[ComputePrice]:
        return list(self._compute_prices)


def _make_model_price(model_id: str, fetched_at: float | None = None) -> ModelPrice:
    return ModelPrice(
        provider="mock_live",
        model_id=model_id,
        input_usd_per_1k=0.003,
        output_usd_per_1k=0.015,
        fetched_at=fetched_at or time.time(),
        source="https://mock.dev/pricing",
    )


def _make_compute_price(sku: str, fetched_at: float | None = None) -> ComputePrice:
    return ComputePrice(
        provider="mock_live",
        sku=sku,
        usd_per_unit=0.74 / 3600,
        granularity=BillingGranularity.per_second,
        spot=False,
        terms=BillingTerms.prepaid_balance,
        fetched_at=fetched_at or time.time(),
        source="https://mock.dev/compute",
    )


# ---------------------------------------------------------------------------
# CachedSource — identity and delegation
# ---------------------------------------------------------------------------


class TestCachedSourceIdentity:
    def test_provider_slug_is_live_source_slug(self) -> None:
        live = _FakeLiveSource()
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static)
        assert cached.provider_slug() == "mock_live"

    def test_billing_delegates_to_live_source(self) -> None:
        live = _FakeLiveSource()
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static)
        assert cached.billing() is _MOCK_BILLING

    def test_isinstance_pricing_source(self) -> None:
        live = _FakeLiveSource()
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static)
        assert isinstance(cached, PricingSource)


class TestCachedSourceModelCache:
    def test_second_call_uses_cache_no_refetch(self) -> None:
        live = _FakeLiveSource()
        live._model_prices = [_make_model_price("model-a")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        prices1 = cached.fetch_model_prices()
        assert len(prices1) == 1
        assert live.model_call_count == 1

        prices2 = cached.fetch_model_prices()
        assert len(prices2) == 1
        assert live.model_call_count == 1  # cached — no second call

    def test_cache_expiry_triggers_refetch(self) -> None:
        live = _FakeLiveSource()
        live._model_prices = [_make_model_price("model-a")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=0)

        _prices1 = cached.fetch_model_prices()
        assert live.model_call_count == 1

        _prices2 = cached.fetch_model_prices()
        assert live.model_call_count == 2  # TTL=0 → always expired

    def test_force_refresh_bypasses_cache(self) -> None:
        live = _FakeLiveSource()
        live._model_prices = [_make_model_price("model-a")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        cached.fetch_model_prices()
        assert live.model_call_count == 1

        cached.fetch_model_prices(refresh=True)
        assert live.model_call_count == 2

    def test_empty_live_result_is_cached(self) -> None:
        live = _FakeLiveSource()
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        cached.fetch_model_prices()
        assert live.model_call_count == 1

        cached.fetch_model_prices()
        assert live.model_call_count == 1  # empty result cached, not retried


class TestCachedSourceComputeCache:
    def test_second_call_uses_cache_no_refetch(self) -> None:
        live = _FakeLiveSource()
        live._compute_prices = [_make_compute_price("a100-1x")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        prices1 = cached.fetch_compute_prices()
        assert len(prices1) == 1
        assert live.compute_call_count == 1

        prices2 = cached.fetch_compute_prices()
        assert len(prices2) == 1
        assert live.compute_call_count == 1  # cached

    def test_force_refresh_bypasses_cache(self) -> None:
        live = _FakeLiveSource()
        live._compute_prices = [_make_compute_price("a100-1x")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        cached.fetch_compute_prices()
        assert live.compute_call_count == 1

        cached.fetch_compute_prices(refresh=True)
        assert live.compute_call_count == 2


# ---------------------------------------------------------------------------
# Static fallback on fetch failure
# ---------------------------------------------------------------------------


class TestCachedSourceFallback:
    def test_model_fetch_failure_falls_back_to_static(self) -> None:
        live = _FakeLiveSource()
        live.fetch_model_prices = MagicMock(side_effect=RuntimeError("API down"))
        static = _FakeStaticSource()
        static._model_prices = [_make_model_price("fallback-model", fetched_at=1700000000.0)]
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        prices = cached.fetch_model_prices()
        assert len(prices) == 1
        assert prices[0].model_id == "fallback-model"

    def test_compute_fetch_failure_falls_back_to_static(self) -> None:
        live = _FakeLiveSource()
        live.fetch_compute_prices = MagicMock(side_effect=ConnectionError("network down"))
        static = _FakeStaticSource()
        static._compute_prices = [_make_compute_price("fallback-sku", fetched_at=1700000000.0)]
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        prices = cached.fetch_compute_prices()
        assert len(prices) == 1
        assert prices[0].sku == "fallback-sku"

    def test_no_fallback_when_static_is_none(self) -> None:
        live = _FakeLiveSource()
        live.fetch_model_prices = MagicMock(side_effect=RuntimeError("API down"))
        cached = CachedSource(live=live, static_fallback=None, ttl_seconds=3600)

        prices = cached.fetch_model_prices()
        assert prices == []

    def test_fallback_returns_static_unchanged(self) -> None:
        live = _FakeLiveSource()
        live.fetch_compute_prices = MagicMock(side_effect=RuntimeError("API down"))
        static = _FakeStaticSource()
        static._compute_prices = [_make_compute_price("original-sku", fetched_at=1700000000.0)]
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        prices = cached.fetch_compute_prices()
        assert len(prices) == 1
        assert prices[0].sku == "original-sku"
        assert prices[0].fetched_at == pytest.approx(1700000000.0)

    def test_cache_returned_on_live_failure_when_cached_entries_exist(self) -> None:
        live = _FakeLiveSource()
        live._compute_prices = [_make_compute_price("cached-sku")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        cached.fetch_compute_prices()
        assert live.compute_call_count == 1

        live.fetch_compute_prices = MagicMock(side_effect=RuntimeError("API down"))
        prices = cached.fetch_compute_prices(refresh=True)
        assert len(prices) == 1
        assert prices[0].sku == "cached-sku"
        assert prices[0].provider == "mock_live"  # stale cache, not fallback


# ---------------------------------------------------------------------------
# Staleness metadata
# ---------------------------------------------------------------------------


class TestStalenessMetadata:
    def test_fresh_price_has_staleness_under_1_hour(self) -> None:
        now = time.time()
        live = _FakeLiveSource()
        live._compute_prices = [_make_compute_price("fresh-sku", fetched_at=now)]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        prices = cached.fetch_compute_prices()
        assert len(prices) == 1
        from general_ludd.pricing_intel.sources import staleness_text

        text = staleness_text(prices[0].fetched_at)
        assert "fresh" in text
        assert "stale" not in text

    def test_old_price_has_staleness_warning(self) -> None:
        old_time = time.time() - 7200  # 2 hours ago
        from general_ludd.pricing_intel.sources import staleness_text

        text = staleness_text(old_time)
        assert "stale" in text


# ---------------------------------------------------------------------------
# Concrete wiring: RunPod CachedSource
# ---------------------------------------------------------------------------


class TestRunPodCachedSource:
    def test_provider_slug_is_runpod_live(self) -> None:
        cached = CachedSource(live=RunPodPricingSource(), static_fallback=RunPodSource())
        assert cached.provider_slug() == "runpod_live"

    def test_falls_back_to_static_on_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        live = RunPodPricingSource()
        static = RunPodSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client"
        ) as mock_client_cls:
            mock_client_cls.side_effect = ConnectionError("no network")
            prices = cached.fetch_compute_prices()

        assert len(prices) > 0
        for p in prices:
            assert p.provider == "runpod"

    def test_live_success_returns_live_prices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        live = RunPodPricingSource()
        static = RunPodSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"gpuTypes": [
                {"id": "NVIDIA RTX 4090", "displayName": "RTX 4090", "memoryInGb": 24,
                 "securePrice": 0.74, "communityPrice": None, "spot": None}
            ]}
        }

        with patch(
            "general_ludd.pricing_intel.sources.httpx.Client"
        ) as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = MagicMock(return_value=mock_resp)
            mock_cls.return_value = mock_client
            prices = cached.fetch_compute_prices()

        assert len(prices) == 1
        assert prices[0].sku == "NVIDIA RTX 4090-secure"
        assert prices[0].provider == "runpod_live"
        assert prices[0].usd_per_unit == pytest.approx(0.74 / 3600)


# ---------------------------------------------------------------------------
# Concrete wiring: AWS CachedSource
# ---------------------------------------------------------------------------


class TestAWSCachedSource:
    def test_provider_slug_is_aws_live(self) -> None:
        cached = CachedSource(live=AWSPricingSource(), static_fallback=AWSSource())
        assert cached.provider_slug() == "aws_live"

    def test_falls_back_to_static_on_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key")
        live = AWSPricingSource()
        static = AWSSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        live._get_client = MagicMock(side_effect=ImportError("no boto3"))
        prices = cached.fetch_compute_prices()
        assert len(prices) > 0
        for p in prices:
            assert p.provider == "aws"

    def test_falls_back_to_static_on_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key")
        live = AWSPricingSource()
        static = AWSSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        mock_client = MagicMock()
        live._get_client = MagicMock(return_value=mock_client)
        live._fetch_price_list = MagicMock(side_effect=RuntimeError("API down"))
        prices = cached.fetch_compute_prices()
        assert len(prices) > 0
        for p in prices:
            assert p.provider == "aws"


# ---------------------------------------------------------------------------
# Concrete wiring: GCP CachedSource
# ---------------------------------------------------------------------------


class TestGCPCachedSource:
    def test_provider_slug_is_gcp_live(self) -> None:
        cached = CachedSource(live=GCPPricingSource(), static_fallback=GCPSource())
        assert cached.provider_slug() == "gcp_live"

    def test_falls_back_to_static_on_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/key.json")
        live = GCPPricingSource()
        static = GCPSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        live._get_client = MagicMock(side_effect=ImportError("no google-cloud-billing"))
        prices = cached.fetch_compute_prices()
        assert len(prices) > 0
        for p in prices:
            assert p.provider == "gcp"

    def test_falls_back_to_static_on_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/key.json")
        live = GCPPricingSource()
        static = GCPSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        mock_client = MagicMock()
        live._get_client = MagicMock(return_value=mock_client)
        live._fetch_skus = MagicMock(side_effect=RuntimeError("API down"))
        prices = cached.fetch_compute_prices()
        assert len(prices) > 0
        for p in prices:
            assert p.provider == "gcp"


# ---------------------------------------------------------------------------
# CachedSource with different TTL values
# ---------------------------------------------------------------------------


class TestCachedSourceTTL:
    def test_default_ttl_is_3600(self) -> None:
        live = _FakeLiveSource()
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static)
        assert cached._ttl == 3600.0

    def test_custom_ttl(self) -> None:
        live = _FakeLiveSource()
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=1800)
        assert cached._ttl == 1800.0

    def test_0_ttl_always_refreshes(self) -> None:
        live = _FakeLiveSource()
        live._model_prices = [_make_model_price("model-x")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=0)

        for _ in range(5):
            cached.fetch_model_prices()
        assert live.model_call_count == 5

    def test_negative_ttl_always_refreshes(self) -> None:
        live = _FakeLiveSource()
        live._model_prices = [_make_model_price("model-x")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=-1)

        for _ in range(3):
            cached.fetch_model_prices()
        assert live.model_call_count == 3


# ---------------------------------------------------------------------------
# CachedSource — model and compute caches are independent
# ---------------------------------------------------------------------------


class TestCachedSourceIndependentCaches:
    def test_model_cache_does_not_affect_compute_cache(self) -> None:
        live = _FakeLiveSource()
        live._model_prices = [_make_model_price("model-a")]
        live._compute_prices = [_make_compute_price("sku-a")]
        static = _FakeStaticSource()
        cached = CachedSource(live=live, static_fallback=static, ttl_seconds=3600)

        cached.fetch_model_prices()
        assert live.model_call_count == 1
        assert live.compute_call_count == 0

        cached.fetch_compute_prices()
        assert live.model_call_count == 1
        assert live.compute_call_count == 1

        cached.fetch_model_prices()
        assert live.model_call_count == 1  # still cached
        assert live.compute_call_count == 1  # still cached


# ---------------------------------------------------------------------------
# Concrete wiring check: CachedSource instances match provider slugs
# ---------------------------------------------------------------------------


class TestConcreteCachedSourceSlugs:
    def test_runpod_cached_slug_is_runpod_live(self) -> None:
        src = CachedSource(live=RunPodPricingSource(), static_fallback=RunPodSource())
        assert src.provider_slug() == "runpod_live"

    def test_aws_cached_slug_is_aws_live(self) -> None:
        src = CachedSource(live=AWSPricingSource(), static_fallback=AWSSource())
        assert src.provider_slug() == "aws_live"

    def test_gcp_cached_slug_is_gcp_live(self) -> None:
        src = CachedSource(live=GCPPricingSource(), static_fallback=GCPSource())
        assert src.provider_slug() == "gcp_live"
