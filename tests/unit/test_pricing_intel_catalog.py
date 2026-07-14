"""Structural tests for the PricingCatalog aggregator."""

from __future__ import annotations

from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
    ProviderBilling,
)


class _FakeSource:
    def __init__(
        self, slug: str,
        models: list[ModelPrice] | None = None,
        compute: list[ComputePrice] | None = None,
    ) -> None:
        self._slug = slug
        self._models = models or []
        self._compute = compute or []

    def provider_slug(self) -> str:
        return self._slug

    def billing(self) -> ProviderBilling:
        return ProviderBilling(
            provider=self._slug,
            granularity=BillingGranularity.per_token,
            terms=BillingTerms.postpaid_per_use,
        )

    def fetch_model_prices(self) -> list[ModelPrice]:
        return self._models

    def fetch_compute_prices(self) -> list[ComputePrice]:
        return self._compute


def _make_model_price(model_id: str, inp: float = 0.001, out: float = 0.002) -> ModelPrice:
    return ModelPrice(
        provider="test", model_id=model_id,
        input_usd_per_1k=inp, output_usd_per_1k=out,
        source="https://example.com/pricing",
    )


def _make_compute_price(sku: str, usd: float = 1.0, spot: bool = False) -> ComputePrice:
    return ComputePrice(
        provider="test", sku=sku,
        usd_per_unit=usd,
        granularity=BillingGranularity.per_second,
        spot=spot,
        terms=BillingTerms.postpaid_monthly,
        source="https://example.com/compute",
    )


class TestPricingCatalogInit:
    def test_init_default(self) -> None:
        cat = PricingCatalog()
        assert cat._ttl == 3600.0
        assert isinstance(cat._sources, list)

    def test_init_custom_sources(self) -> None:
        src = _FakeSource("stub")
        cat = PricingCatalog(sources=[src])
        assert len(cat._sources) == 1
        assert cat._sources[0].provider_slug() == "stub"

    def test_init_custom_ttl(self) -> None:
        cat = PricingCatalog(ttl_seconds=60.0)
        assert cat._ttl == 60.0


class TestProviderSlugs:
    def test_returns_slugs(self) -> None:
        src = _FakeSource("stub-a")
        src2 = _FakeSource("stub-b")
        cat = PricingCatalog(sources=[src, src2])
        assert cat.provider_slugs() == ["stub-a", "stub-b"]

    def test_empty(self) -> None:
        cat = PricingCatalog(sources=[])
        assert cat.provider_slugs() == []


class TestBilling:
    def test_known_provider(self) -> None:
        src = _FakeSource("stub")
        cat = PricingCatalog(sources=[src])
        billing = cat.billing("stub")
        assert billing is not None
        assert billing.provider == "stub"

    def test_unknown_provider(self) -> None:
        cat = PricingCatalog(sources=[])
        assert cat.billing("missing") is None

    def test_all_billing(self) -> None:
        src_a = _FakeSource("a")
        src_b = _FakeSource("b")
        cat = PricingCatalog(sources=[src_a, src_b])
        all_b = cat.all_billing()
        assert len(all_b) == 2

    def test_all_billing_fail_soft(self) -> None:
        class _BoomSource:
            def provider_slug(self) -> str:
                return "boom"

            def billing(self) -> None:
                raise RuntimeError("boom")

            def fetch_model_prices(self) -> list[ModelPrice]:
                return []

            def fetch_compute_prices(self) -> list[ComputePrice]:
                return []

        cat = PricingCatalog(sources=[_BoomSource()])
        assert cat.all_billing() == []


class TestModelPrice:
    def test_exact_match(self) -> None:
        m = _make_model_price("gpt-4")
        src = _FakeSource("openai", models=[m])
        cat = PricingCatalog(sources=[src])
        result = cat.model_price("openai", "gpt-4")
        assert result is not None
        assert result.model_id == "gpt-4"

    def test_no_match(self) -> None:
        m = _make_model_price("gpt-4")
        src = _FakeSource("openai", models=[m])
        cat = PricingCatalog(sources=[src])
        assert cat.model_price("openai", "nonexistent") is None

    def test_unknown_provider(self) -> None:
        cat = PricingCatalog(sources=[])
        assert cat.model_price("missing", "any") is None

    def test_all_model_prices(self) -> None:
        src = _FakeSource("openai", models=[_make_model_price("a"), _make_model_price("b")])
        cat = PricingCatalog(sources=[src])
        all_m = cat.all_model_prices()
        assert len(all_m) == 2

    def test_all_model_prices_filtered(self) -> None:
        src = _FakeSource("openai", models=[_make_model_price("a")])
        cat = PricingCatalog(sources=[src])
        assert len(cat.all_model_prices(provider="openai")) == 1
        assert len(cat.all_model_prices(provider="nonexistent")) == 0


class TestComputePrice:
    def test_exact_match(self) -> None:
        c = _make_compute_price("A100-1x", usd=2.49)
        src = _FakeSource("runpod", compute=[c])
        cat = PricingCatalog(sources=[src])
        result = cat.compute_price("runpod", "A100-1x")
        assert result is not None
        assert result.sku == "A100-1x"

    def test_no_match(self) -> None:
        c = _make_compute_price("A100-1x")
        src = _FakeSource("runpod", compute=[c])
        cat = PricingCatalog(sources=[src])
        assert cat.compute_price("runpod", "nonexistent") is None

    def test_spot_suffix_match(self) -> None:
        c = _make_compute_price("p4d.24xlarge-spot", usd=9.83, spot=True)
        src = _FakeSource("aws", compute=[c])
        cat = PricingCatalog(sources=[src])
        result = cat.compute_price("aws", "p4d.24xlarge", spot=True)
        assert result is not None
        assert result.sku == "p4d.24xlarge-spot"
        assert result.spot is True

    def test_all_compute_prices(self) -> None:
        src = _FakeSource("runpod", compute=[_make_compute_price("A"), _make_compute_price("B")])
        cat = PricingCatalog(sources=[src])
        all_c = cat.all_compute_prices()
        assert len(all_c) == 2

    def test_all_compute_prices_spot_filter(self) -> None:
        src = _FakeSource("runpod", compute=[
            _make_compute_price("A", spot=False),
            _make_compute_price("B", spot=True),
        ])
        cat = PricingCatalog(sources=[src])
        assert len(cat.all_compute_prices(spot=True)) == 1
        assert len(cat.all_compute_prices(spot=False)) == 1
        assert len(cat.all_compute_prices(spot=None)) == 2


class TestCheapestCompute:
    def test_sorts_by_price(self) -> None:
        src = _FakeSource("runpod", compute=[
            _make_compute_price("expensive", usd=10.0),
            _make_compute_price("cheap", usd=0.5),
            _make_compute_price("mid", usd=3.0),
        ])
        cat = PricingCatalog(sources=[src])
        results = cat.cheapest_compute()
        assert results[0].sku == "cheap"
        assert results[-1].sku == "expensive"

    def test_empty(self) -> None:
        cat = PricingCatalog(sources=[])
        assert cat.cheapest_compute() == []


class TestAllModelInfo:
    def test_combines_pricing(self) -> None:
        m = _make_model_price("gpt-4", inp=0.03, out=0.06)
        src = _FakeSource("openai", models=[m])
        cat = PricingCatalog(sources=[src])
        info_list = cat.all_model_info()
        assert len(info_list) == 1
        assert info_list[0].model_id == "gpt-4"
        assert info_list[0].pricing.input_usd_per_1k == 0.03


class TestCacheStaleness:
    def test_fresh_cache_returns_cached(self) -> None:
        m1 = _make_model_price("m1")
        src = _FakeSource("openai", models=[m1])
        cat = PricingCatalog(sources=[src])
        assert cat.model_price("openai", "m1") is not None
        assert cat.model_price("openai", "m1") is not None  # cached hit

    def test_refresh_bypasses_cache(self) -> None:
        m1 = _make_model_price("m1")
        src = _FakeSource("openai", models=[m1])
        cat = PricingCatalog(sources=[src])
        cat.model_price("openai", "m1")  # prime cache
        result = cat.model_price("openai", "m1", refresh=True)
        assert result is not None
        assert result.model_id == "m1"
