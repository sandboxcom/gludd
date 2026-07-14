"""Tests for pricing_intel.catalog: PricingCatalog."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import PricingSource

_SRC = "test-fixture"


def _make_mock_source(
    slug: str,
    billing: ProviderBilling | None = None,
    models: list[ModelPrice] | None = None,
    compute: list[ComputePrice] | None = None,
) -> MagicMock:
    src = MagicMock(spec=PricingSource)
    src.provider_slug.return_value = slug
    src.billing.return_value = billing or ProviderBilling(
        provider=slug,
        granularity=BillingGranularity.per_token,
        terms=BillingTerms.postpaid_per_use,
        currency="USD",
    )
    src.fetch_model_prices.return_value = models or []
    src.fetch_compute_prices.return_value = compute or []
    return src


def _mp(model_id="a", input_usd=0.01, output_usd=0.02, **kw):
    return ModelPrice(provider="test", model_id=model_id,
                      input_usd_per_1k=input_usd, output_usd_per_1k=output_usd,
                      source=_SRC, **kw)


def _cp(sku="sku-1", usd=0.01, spot=False, gpu_type="A100", **kw):
    return ComputePrice(provider="test", sku=sku, usd_per_unit=usd,
                        granularity=BillingGranularity.per_second,
                        spot=spot, terms=BillingTerms.prepaid_balance,
                        source=_SRC, gpu_type=gpu_type, **kw)


class TestPricingCatalogInit:
    def test_empty_catalog(self):
        catalog = PricingCatalog(sources=[])
        assert catalog.provider_slugs() == []

    def test_catalog_with_sources(self):
        src = _make_mock_source("test-provider")
        catalog = PricingCatalog(sources=[src])
        assert catalog.provider_slugs() == ["test-provider"]

    def test_catalog_default_sources(self):
        catalog = PricingCatalog()
        slugs = catalog.provider_slugs()
        assert "openrouter" in slugs
        assert "anthropic" in slugs
        assert "runpod" in slugs


class TestPricingCatalogBilling:
    def test_billing_known_provider(self):
        billing = ProviderBilling(
            provider="test",
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
            currency="USD",
        )
        src = _make_mock_source("test", billing=billing)
        catalog = PricingCatalog(sources=[src])
        result = catalog.billing("test")
        assert result is not None
        assert result.provider == "test"

    def test_billing_unknown_provider(self):
        catalog = PricingCatalog(sources=[])
        assert catalog.billing("unknown") is None

    def test_all_billing(self):
        src1 = _make_mock_source("p1")
        src2 = _make_mock_source("p2")
        catalog = PricingCatalog(sources=[src1, src2])
        results = catalog.all_billing()
        assert len(results) == 2

    def test_all_billing_fail_soft(self):
        src = _make_mock_source("p1")
        src.billing.side_effect = RuntimeError("boom")
        catalog = PricingCatalog(sources=[src])
        results = catalog.all_billing()
        assert results == []


class TestPricingCatalogModelPrices:
    def test_model_price_found(self):
        mp = _mp(model_id="model-a", input_usd=0.01, output_usd=0.03)
        src = _make_mock_source("test", models=[mp])
        catalog = PricingCatalog(sources=[src])
        result = catalog.model_price("test", "model-a")
        assert result is not None
        assert result.model_id == "model-a"

    def test_model_price_not_found(self):
        src = _make_mock_source("test", models=[])
        catalog = PricingCatalog(sources=[src])
        assert catalog.model_price("test", "nonexistent") is None

    def test_model_price_unknown_provider(self):
        catalog = PricingCatalog(sources=[])
        assert catalog.model_price("unknown", "x") is None

    def test_all_model_prices(self):
        src = _make_mock_source("test", models=[_mp("a"), _mp("b")])
        catalog = PricingCatalog(sources=[src])
        results = catalog.all_model_prices()
        assert len(results) == 2

    def test_all_model_prices_filtered_by_provider(self):
        src = _make_mock_source("test", models=[_mp("a")])
        catalog = PricingCatalog(sources=[src])
        results = catalog.all_model_prices(provider="unknown")
        assert results == []

    def test_fetch_model_prices_caches(self):
        src = _make_mock_source("test", models=[_mp("a")])
        catalog = PricingCatalog(sources=[src], ttl_seconds=3600)
        catalog.model_price("test", "a")
        catalog.model_price("test", "a")
        assert src.fetch_model_prices.call_count == 1

    def test_fetch_model_prices_refresh(self):
        src = _make_mock_source("test", models=[_mp("a")])
        catalog = PricingCatalog(sources=[src], ttl_seconds=3600)
        catalog.model_price("test", "a")
        catalog.model_price("test", "a", refresh=True)
        assert src.fetch_model_prices.call_count == 2

    def test_fetch_model_prices_fail_soft(self):
        src = _make_mock_source("test", models=[])
        src.fetch_model_prices.side_effect = RuntimeError("boom")
        catalog = PricingCatalog(sources=[src])
        assert catalog.model_price("test", "x") is None

    def test_all_model_info(self):
        mp = _mp("a", context_window=4096, notes="fast")
        src = _make_mock_source("test", models=[mp])
        catalog = PricingCatalog(sources=[src])
        infos = catalog.all_model_info()
        assert len(infos) == 1
        assert infos[0].model_id == "a"
        assert infos[0].context_window == 4096


class TestPricingCatalogComputePrices:
    def test_compute_price_found(self):
        cp = _cp(sku="gpu-1", usd=0.01)
        src = _make_mock_source("test", compute=[cp])
        catalog = PricingCatalog(sources=[src])
        result = catalog.compute_price("test", "gpu-1")
        assert result is not None
        assert result.sku == "gpu-1"

    def test_compute_price_not_found(self):
        catalog = PricingCatalog(sources=[])
        assert catalog.compute_price("test", "x") is None

    def test_cheapest_compute(self):
        cp1 = _cp(sku="expensive", usd=10.0)
        cp2 = _cp(sku="cheap", usd=0.5)
        src = _make_mock_source("test", compute=[cp1, cp2])
        catalog = PricingCatalog(sources=[src])
        results = catalog.cheapest_compute()
        assert results[0].sku == "cheap"
        assert results[1].sku == "expensive"

    def test_cheapest_compute_with_gpu_filter(self):
        cp1 = _cp(sku="nvidia", usd=1.0, gpu_type="NVIDIA A100")
        cp2 = _cp(sku="amd", usd=0.5, gpu_type="AMD MI250")
        src = _make_mock_source("test", compute=[cp1, cp2])
        catalog = PricingCatalog(sources=[src])
        results = catalog.cheapest_compute(gpu_type_substr="A100")
        assert len(results) == 1
        assert results[0].sku == "nvidia"

    def test_all_compute_prices_spot_filter(self):
        cp_on = _cp(sku="on-demand", usd=1.0, spot=False)
        cp_spot = _cp(sku="spot", usd=0.5, spot=True)
        src = _make_mock_source("test", compute=[cp_on, cp_spot])
        catalog = PricingCatalog(sources=[src])
        spot_only = catalog.all_compute_prices(spot=True)
        assert len(spot_only) == 1
        assert spot_only[0].sku == "spot"

    def test_fetch_compute_prices_fail_soft(self):
        src = _make_mock_source("test", compute=[])
        src.fetch_compute_prices.side_effect = RuntimeError("boom")
        catalog = PricingCatalog(sources=[src])
        assert catalog.compute_price("test", "x") is None
