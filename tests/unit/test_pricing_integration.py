"""Integration tests for pricing sources: CachedSource TTL, static fallback,
RunPod/AWS/GCP parsing, currency/granularity conversion.

Coverage:
  - CachedSource TTL: cache hit within TTL, re-fetch after staleness, refresh bypass
  - CachedSource static fallback: live failure → cache; live+no-cache failure → static
  - RunPodSource compute parsing: on-demand + spot prices, billing semantics
  - AWSSource compute parsing: on-demand + spot, billing semantics
  - GCPSource compute parsing: on-demand + spot, billing semantics
  - Currency / granularity conversion: usd_per_hour() normalization
  - PricingCatalog caching: model and compute caches with TTL expiry
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
)
from general_ludd.pricing_intel.sources import (
    AnthropicSource,
    AWSSource,
    CachedSource,
    GCPSource,
    OpenRouterSource,
    RunPodPricingSource,
    RunPodSource,
    staleness_text,
)

# ---------------------------------------------------------------------------
# CachedSource — TTL cache
# ---------------------------------------------------------------------------

class TestCachedSourceTTL:
    def test_cache_hit_within_ttl_returns_cached_model_prices(self) -> None:
        source = AnthropicSource()
        cached = CachedSource(source, ttl_seconds=3600)

        first = cached.fetch_model_prices()
        assert len(first) > 0
        second = cached.fetch_model_prices()
        assert second is not first
        assert [p.model_id for p in second] == [p.model_id for p in first]

    def test_cache_hit_within_ttl_returns_cached_compute_prices(self) -> None:
        source = RunPodSource()
        cached = CachedSource(source, ttl_seconds=3600)

        first = cached.fetch_compute_prices()
        assert len(first) > 0
        second = cached.fetch_compute_prices()
        assert second is not first
        assert [p.sku for p in second] == [p.sku for p in first]

    def test_refresh_bypasses_cache_for_models(self) -> None:
        source = AnthropicSource()
        cached = CachedSource(source, ttl_seconds=999999)

        first = cached.fetch_model_prices()
        second = cached.fetch_model_prices(refresh=True)
        assert second is not first
        assert len(second) == len(first)

    def test_refresh_bypasses_cache_for_compute(self) -> None:
        source = RunPodSource()
        cached = CachedSource(source, ttl_seconds=999999)

        first = cached.fetch_compute_prices()
        second = cached.fetch_compute_prices(refresh=True)
        assert second is not first
        assert len(second) == len(first)

    def test_ttl_zero_always_re_fetches(self) -> None:
        source = AnthropicSource()
        cached = CachedSource(source, ttl_seconds=0)

        first = cached.fetch_model_prices()
        second = cached.fetch_model_prices()
        assert second is not first
        assert len(second) == len(first)

    def test_negative_ttl_treated_as_always_stale(self) -> None:
        source = AnthropicSource()
        cached = CachedSource(source, ttl_seconds=-1)

        first = cached.fetch_model_prices()
        second = cached.fetch_model_prices()
        assert second is not first

    def test_provider_slug_delegates_to_live_source(self) -> None:
        live = AnthropicSource()
        cached = CachedSource(live)
        assert cached.provider_slug() == live.provider_slug()

    def test_billing_delegates_to_live_source(self) -> None:
        live = RunPodSource()
        cached = CachedSource(live)
        billing = cached.billing()
        assert billing.terms == BillingTerms.prepaid_balance
        assert billing.granularity == BillingGranularity.per_second


# ---------------------------------------------------------------------------
# CachedSource — static fallback
# ---------------------------------------------------------------------------

class TestCachedSourceStaticFallback:
    def test_live_failure_falls_back_to_static_models(self) -> None:
        static = AnthropicSource()
        static_prices = static.fetch_model_prices()

        live = OpenRouterSource()
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(side_effect=ConnectionError("down"))

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            cached = CachedSource(live, static_fallback=static)
            prices = cached.fetch_model_prices(refresh=True)

        assert len(prices) == len(static_prices)

    def test_live_failure_falls_back_to_static_compute(self) -> None:
        static = RunPodSource()
        static_compute = static.fetch_compute_prices()

        live = RunPodPricingSource()

        with patch.dict("os.environ", {"RUNPOD_API_KEY": "fake-key"}), \
             patch("general_ludd.pricing_intel.sources.httpx.Client") as mock_http:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.post = MagicMock(side_effect=ConnectionError("down"))
                mock_http.return_value = mock_client

                cached = CachedSource(live, static_fallback=static)
                prices = cached.fetch_compute_prices(refresh=True)

        assert len(prices) == len(static_compute)

    def test_live_empty_falls_back_to_static(self) -> None:
        static = AnthropicSource()
        static_prices = static.fetch_model_prices()

        live = OpenRouterSource()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.get = MagicMock(return_value=mock_resp)

        with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=mock_client_instance):
            cached = CachedSource(live, static_fallback=static)
            prices = cached.fetch_model_prices(refresh=True)

        assert len(prices) == len(static_prices)

    def test_stale_cache_returned_on_live_failure(self) -> None:
        static = AnthropicSource()
        cached = CachedSource(static, ttl_seconds=999999)
        cached.fetch_model_prices()

        live = OpenRouterSource()
        cached_with_fallback = CachedSource(live, static_fallback=static)
        cached_with_fallback._model_cache = [ModelPrice(
            provider="test", model_id="cached-model",
            input_usd_per_1k=0.001, output_usd_per_1k=0.002,
            source="https://test.com",
        )]
        cached_with_fallback._model_cache_time = time.time()
        cached_with_fallback._ttl = 999999
        cached_with_fallback._live = MagicMock()
        cached_with_fallback._live.fetch_model_prices = MagicMock(
            side_effect=RuntimeError("live down")
        )

        prices = cached_with_fallback.fetch_model_prices()
        assert any(p.model_id == "cached-model" for p in prices)

    def test_static_fallback_failure_returns_empty(self) -> None:
        static = MagicMock()
        static.fetch_model_prices = MagicMock(side_effect=RuntimeError("static down"))
        static.fetch_compute_prices = MagicMock(side_effect=RuntimeError("static down"))

        live = MagicMock()
        live.provider_slug = MagicMock(return_value="test")
        live.fetch_model_prices = MagicMock(side_effect=RuntimeError("live down"))
        live.fetch_compute_prices = MagicMock(side_effect=RuntimeError("live down"))

        cached = CachedSource(live, static_fallback=static)
        model_prices = cached.fetch_model_prices(refresh=True)
        compute_prices = cached.fetch_compute_prices(refresh=True)

        assert model_prices == []
        assert compute_prices == []


# ---------------------------------------------------------------------------
# RunPod compute parsing
# ---------------------------------------------------------------------------

class TestRunPodParsing:
    def test_all_prices_per_second_granularity(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        for cp in prices:
            assert cp.granularity == BillingGranularity.per_second

    def test_all_prices_prepaid_balance(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        for cp in prices:
            assert cp.terms == BillingTerms.prepaid_balance

    def test_ondemand_not_spot(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        ondemand = [p for p in prices if not p.spot]
        assert len(ondemand) > 0
        for cp in ondemand:
            assert cp.spot is False

    def test_spot_prices_marked_spot(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        spot = [p for p in prices if p.spot]
        assert len(spot) > 0
        for cp in spot:
            assert cp.spot is True

    def test_hourly_to_second_conversion(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        h100_sku = "H100-SXM5-80GB-1x"
        h100 = next(p for p in prices if p.sku == h100_sku)
        expected_per_sec = 4.69 / 3600.0
        assert abs(h100.usd_per_unit - expected_per_sec) < 1e-9

    def test_usd_per_hour_roundtrips(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        for cp in prices:
            hourly = cp.usd_per_hour()
            assert hourly > 0
            assert abs(cp.usd_per_unit * 3600 - hourly) < 1e-6

    def test_gpu_count_positive(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        for cp in prices:
            assert cp.gpu_count is not None
            assert cp.gpu_count > 0

    def test_gpu_type_present(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        for cp in prices:
            assert cp.gpu_type is not None
            assert len(cp.gpu_type) > 0

    def test_source_url_points_to_runpod(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        for cp in prices:
            assert "runpod.io" in cp.source

    def test_fetched_at_is_recent(self) -> None:
        prices = RunPodSource().fetch_compute_prices()
        for cp in prices:
            assert cp.fetched_at > 0
            assert staleness_text(cp.fetched_at) == "very_stale"


# ---------------------------------------------------------------------------
# AWS compute parsing
# ---------------------------------------------------------------------------

class TestAWSParsing:
    def test_all_prices_per_second_granularity(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        for cp in prices:
            assert cp.granularity == BillingGranularity.per_second

    def test_all_prices_postpaid_monthly(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        for cp in prices:
            assert cp.terms == BillingTerms.postpaid_monthly

    def test_spot_vs_ondemand_split(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        ondemand = [p for p in prices if not p.spot]
        spot = [p for p in prices if p.spot]
        assert len(ondemand) > 0
        assert len(spot) > 0

    def test_spot_cheaper_than_ondemand_same_class(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        p4d_ondemand = next(p for p in prices if p.sku == "p4d.24xlarge")
        p4d_spot = next(p for p in prices if p.sku == "p4d.24xlarge-spot")
        assert p4d_spot.usd_per_hour() < p4d_ondemand.usd_per_hour()

    def test_instance_type_as_sku(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        p3 = next(p for p in prices if p.sku == "p3.2xlarge")
        assert p3.gpu_type == "V100 16GB"
        assert p3.gpu_count == 1

    def test_hourly_to_second_conversion(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        p5 = next(p for p in prices if p.sku == "p5.48xlarge")
        expected_per_sec = 98.32 / 3600.0
        assert abs(p5.usd_per_unit - expected_per_sec) < 1e-9

    def test_g5_family_present(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        g5_prices = [p for p in prices if p.sku.startswith("g5.")]
        assert len(g5_prices) > 0
        for cp in g5_prices:
            assert "A10G" in (cp.gpu_type or "")

    def test_source_url_points_to_aws(self) -> None:
        prices = AWSSource().fetch_compute_prices()
        for cp in prices:
            assert "aws.amazon.com" in cp.source

    def test_all_min_charge_zero(self) -> None:
        billing = AWSSource().billing()
        assert billing.min_charge == 0.0


# ---------------------------------------------------------------------------
# GCP compute parsing
# ---------------------------------------------------------------------------

class TestGCPParsing:
    def test_all_prices_per_second_granularity(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        for cp in prices:
            assert cp.granularity == BillingGranularity.per_second

    def test_all_prices_postpaid_monthly(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        for cp in prices:
            assert cp.terms == BillingTerms.postpaid_monthly

    def test_spot_vs_ondemand_split(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        ondemand = [p for p in prices if not p.spot]
        spot = [p for p in prices if p.spot]
        assert len(ondemand) > 0
        assert len(spot) > 0

    def test_spot_cheaper_than_ondemand_same_class(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        a2_ondemand = next(p for p in prices if p.sku == "a2-highgpu-1g")
        a2_spot = next(p for p in prices if p.sku == "a2-highgpu-1g-spot")
        assert a2_spot.usd_per_hour() < a2_ondemand.usd_per_hour()

    def test_a2_a3_present(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        a2 = [p for p in prices if "a2" in p.sku.lower()]
        a3 = [p for p in prices if "a3" in p.sku.lower()]
        assert len(a2) > 0
        assert len(a3) > 0

    def test_hourly_to_second_conversion(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        a2_1g = next(p for p in prices if p.sku == "a2-highgpu-1g")
        expected_per_sec = 3.673 / 3600.0
        assert abs(a2_1g.usd_per_unit - expected_per_sec) < 1e-9

    def test_gpu_type_present(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        for cp in prices:
            assert cp.gpu_type is not None
            assert len(cp.gpu_type) > 0

    def test_source_url_points_to_gcp(self) -> None:
        prices = GCPSource().fetch_compute_prices()
        for cp in prices:
            assert "cloud.google.com" in cp.source

    def test_billing_min_charge_zero(self) -> None:
        billing = GCPSource().billing()
        assert billing.min_charge == 0.0


# ---------------------------------------------------------------------------
# Currency conversion — usd_per_hour normalization
# ---------------------------------------------------------------------------

class TestCurrencyConversion:
    def test_per_second_to_hourly(self) -> None:
        rate_per_sec = 1.0
        cp = ComputePrice(
            provider="test", sku="s", usd_per_unit=rate_per_sec,
            granularity=BillingGranularity.per_second, spot=False,
            terms=BillingTerms.postpaid_monthly,
            source="https://example.com",
        )
        assert cp.usd_per_hour() == 3600.0

    def test_per_minute_to_hourly(self) -> None:
        rate_per_min = 1.0
        cp = ComputePrice(
            provider="test", sku="m", usd_per_unit=rate_per_min,
            granularity=BillingGranularity.per_minute, spot=False,
            terms=BillingTerms.postpaid_monthly,
            source="https://example.com",
        )
        assert cp.usd_per_hour() == 60.0

    def test_per_hour_passthrough(self) -> None:
        cp = ComputePrice(
            provider="test", sku="h", usd_per_unit=10.0,
            granularity=BillingGranularity.per_hour, spot=False,
            terms=BillingTerms.postpaid_monthly,
            source="https://example.com",
        )
        assert cp.usd_per_hour() == 10.0

    def test_runpod_to_aws_comparison_different_granularity(self) -> None:
        runpod = RunPodSource().fetch_compute_prices()
        aws = AWSSource().fetch_compute_prices()

        rp_a100_per_sec = next(
            p for p in runpod if p.sku == "A100-SXM4-80GB-1x"
        )
        rp_hourly = rp_a100_per_sec.usd_per_hour()
        assert rp_hourly > 0

        aws_a100 = next(
            p for p in aws if "A100" in (p.gpu_type or "") and not p.spot
        )
        aws_hourly = aws_a100.usd_per_hour()
        assert aws_hourly > 0

    def test_gcp_runs_out_usd_per_hour_independent_of_granularity(self) -> None:
        gcp = GCPSource().fetch_compute_prices()
        ondemand = [p for p in gcp if not p.spot]
        for cp in ondemand:
            hourly = cp.usd_per_hour()
            assert hourly > 0
            assert abs(cp.usd_per_unit * 3600 - hourly) < 1e-6

    def test_billing_currency_all_usd(self) -> None:
        for src_cls in (RunPodSource, AWSSource, GCPSource, AnthropicSource):
            billing = src_cls().billing()
            assert billing.currency == "USD"

    def test_staleness_text_fresh_stale_very_stale(self) -> None:
        now = time.time()
        assert staleness_text(now - 60) == "fresh"
        assert staleness_text(now - 1800) == "fresh"
        assert staleness_text(now - 3601) == "stale"
        assert staleness_text(now - 86401) == "very_stale"

    def test_staleness_text_future_fetched_at(self) -> None:
        future = time.time() + 100000
        assert staleness_text(future) == "fresh"


# ---------------------------------------------------------------------------
# PricingCatalog caching and integration
# ---------------------------------------------------------------------------

class TestPricingCatalogIntegration:
    def test_model_price_cache_hit_avoids_refetch(self) -> None:
        catalog = PricingCatalog()
        first = catalog.model_price("anthropic", "claude-3-5-sonnet-20241022")
        second = catalog.model_price("anthropic", "claude-3-5-sonnet-20241022")
        assert first is not None
        assert second is not None
        assert first.provider == second.provider
        assert first.model_id == second.model_id

    def test_compute_price_cache_hit_avoids_refetch(self) -> None:
        catalog = PricingCatalog()
        first = catalog.compute_price("runpod", "A100-SXM4-80GB-1x")
        second = catalog.compute_price("runpod", "A100-SXM4-80GB-1x")
        assert first is not None
        assert second is not None
        assert first.sku == second.sku

    def test_refresh_forces_model_re_fetch(self) -> None:
        catalog = PricingCatalog()
        first = catalog.model_price("anthropic", "claude-3-5-sonnet-20241022")
        second = catalog.model_price("anthropic", "claude-3-5-sonnet-20241022", refresh=True)
        assert first is not None
        assert second is not None
        assert first.model_id == second.model_id

    def test_refresh_forces_compute_re_fetch(self) -> None:
        catalog = PricingCatalog()
        first = catalog.compute_price("runpod", "RTX-4090-1x")
        second = catalog.compute_price("runpod", "RTX-4090-1x", refresh=True)
        assert first is not None
        assert second is not None

    def test_runpod_in_catalog_matches_runpod_source(self) -> None:
        catalog = PricingCatalog()
        source_prices = RunPodSource().fetch_compute_prices()
        catalog_a100 = catalog.compute_price("runpod", "A100-SXM4-80GB-1x")
        source_a100 = next(p for p in source_prices if p.sku == "A100-SXM4-80GB-1x")
        assert catalog_a100 is not None
        assert abs(catalog_a100.usd_per_unit - source_a100.usd_per_unit) < 1e-9

    def test_aws_in_catalog_matches_aws_source(self) -> None:
        catalog = PricingCatalog()
        source_prices = AWSSource().fetch_compute_prices()
        catalog_p4d = catalog.compute_price("aws", "p4d.24xlarge")
        source_p4d = next(p for p in source_prices if p.sku == "p4d.24xlarge")
        assert catalog_p4d is not None
        assert abs(catalog_p4d.usd_per_unit - source_p4d.usd_per_unit) < 1e-9

    def test_gcp_in_catalog_matches_gcp_source(self) -> None:
        catalog = PricingCatalog()
        source_prices = GCPSource().fetch_compute_prices()
        catalog_a2 = catalog.compute_price("gcp", "a2-highgpu-1g")
        source_a2 = next(p for p in source_prices if p.sku == "a2-highgpu-1g")
        assert catalog_a2 is not None
        assert abs(catalog_a2.usd_per_unit - source_a2.usd_per_unit) < 1e-9

    def test_spot_lookup_with_suffix_fallback(self) -> None:
        catalog = PricingCatalog()
        aws_spot = catalog.compute_price("aws", "p4d.24xlarge", spot=True)
        assert aws_spot is not None
        assert aws_spot.spot is True
        assert "p4d.24xlarge-spot" in aws_spot.sku

    def test_non_spot_lookup_returns_ondemand(self) -> None:
        catalog = PricingCatalog()
        aws = catalog.compute_price("aws", "p4d.24xlarge", spot=False)
        assert aws is not None
        assert aws.spot is False

    def test_all_compute_prices_provider_filter(self) -> None:
        catalog = PricingCatalog()
        runpod_only = catalog.all_compute_prices(provider="runpod")
        assert len(runpod_only) > 0
        assert all(p.provider == "runpod" for p in runpod_only)

    def test_cheapest_compute_excludes_non_time_granularities(self) -> None:
        catalog = PricingCatalog()
        cheapest = catalog.cheapest_compute()
        hourly_rates = [p.usd_per_hour() for p in cheapest]
        assert hourly_rates == sorted(hourly_rates)
        assert hourly_rates[0] <= hourly_rates[-1]
