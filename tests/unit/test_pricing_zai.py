"""Tests for ZAISource — STATIC price table for Z.AI (GLM models).

Z.AI publishes pricing on an HTML page only (no machine-readable API):
  https://docs.z.ai/guides/overview/pricing

Coverage:
  - provider slug + billing semantics (postpaid_per_use, per_token)
  - static price table correctness (GLM-5.2, GLM-5, GLM-4.5)
  - per-1M → per-1K conversion (divide by 1000)
  - every price carries a source URL
  - fetch_compute_prices returns []
  - registration in all_sources()
"""

from __future__ import annotations

import pytest

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import ZAISource, all_sources


class TestZAIIdentity:
    def test_provider_slug(self) -> None:
        assert ZAISource().provider_slug() == "zai"

    def test_billing_terms(self) -> None:
        b = ZAISource().billing()
        assert isinstance(b, ProviderBilling)
        assert b.provider == "zai"
        assert b.terms == BillingTerms.postpaid_per_use
        assert b.granularity == BillingGranularity.per_token
        assert b.spot_available is False
        assert b.currency == "USD"

    def test_billing_notes_reference_source(self) -> None:
        b = ZAISource().billing()
        assert "z.ai" in b.notes.lower(), (
            f"ZAI billing notes should reference z.ai source, got: {b.notes}"
        )


class TestZAIModelPrices:
    def test_returns_three_glm_models(self) -> None:
        prices = ZAISource().fetch_model_prices()
        ids = {p.model_id for p in prices}
        assert ids == {"glm-5.2", "glm-5", "glm-4.5"}, (
            f"expected GLM-5.2, GLM-5, GLM-4.5; got {ids}"
        )

    def test_glm_52_prices(self) -> None:
        """GLM-5.2: $1.4/1M input, $4.4/1M output → per-1K."""
        prices = ZAISource().fetch_model_prices()
        p = next(x for x in prices if x.model_id == "glm-5.2")
        assert p.input_usd_per_1k == pytest.approx(1.4 / 1000)
        assert p.output_usd_per_1k == pytest.approx(4.4 / 1000)
        assert p.provider == "zai"

    def test_glm_5_prices(self) -> None:
        """GLM-5: $1.0/1M input, $3.2/1M output → per-1K."""
        prices = ZAISource().fetch_model_prices()
        p = next(x for x in prices if x.model_id == "glm-5")
        assert p.input_usd_per_1k == pytest.approx(1.0 / 1000)
        assert p.output_usd_per_1k == pytest.approx(3.2 / 1000)

    def test_glm_45_prices(self) -> None:
        """GLM-4.5: $0.6/1M input, $2.2/1M output → per-1K."""
        prices = ZAISource().fetch_model_prices()
        p = next(x for x in prices if x.model_id == "glm-4.5")
        assert p.input_usd_per_1k == pytest.approx(0.6 / 1000)
        assert p.output_usd_per_1k == pytest.approx(2.2 / 1000)

    def test_all_prices_have_source(self) -> None:
        prices = ZAISource().fetch_model_prices()
        assert prices, "ZAI must have at least one price entry"
        for p in prices:
            assert p.source, f"ZAI price for {p.model_id} has no source"
            assert "z.ai" in p.source, (
                f"ZAI source should point to z.ai, got: {p.source}"
            )

    def test_fetched_at_is_set(self) -> None:
        prices = ZAISource().fetch_model_prices()
        for p in prices:
            assert p.fetched_at > 0, (
                f"ZAI price for {p.model_id} missing fetched_at timestamp"
            )

    def test_input_cheaper_than_output(self) -> None:
        """Sanity: input price must be cheaper than output for every model."""
        prices = ZAISource().fetch_model_prices()
        for p in prices:
            assert p.input_usd_per_1k < p.output_usd_per_1k, (
                f"{p.model_id}: input {p.input_usd_per_1k} >= output {p.output_usd_per_1k}"
            )


class TestZAICompute:
    def test_fetch_compute_prices_returns_empty(self) -> None:
        """ZAI is a model API provider; no direct compute offering."""
        assert ZAISource().fetch_compute_prices() == []


class TestZAIRegistration:
    def test_registered_in_all_sources(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert "zai" in slugs, (
            "ZAISource must be registered in all_sources(); got: " + ", ".join(slugs)
        )

    def test_all_sources_has_exactly_one_zai(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert slugs.count("zai") == 1, (
            f"expected exactly one 'zai' source, got {slugs.count('zai')}"
        )
