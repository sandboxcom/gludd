"""Tests for HuggingFaceSource — STATIC dedicated-endpoint GPU price table.

HuggingFace publishes dedicated Inference Endpoints GPU pricing at:
  https://huggingface.co/pricing#dedicated-endpoints

No machine-readable public per-token catalog exists (serverless inference is
metered against the account, not exposed in a pricing API), so:
  - fetch_model_prices() returns [] (no public per-token catalog)
  - fetch_compute_prices() returns the static dedicated-endpoint table

Billing semantics for dedicated endpoints:
  - granularity: per_hour
  - terms:       prepaid_balance (account credits consumed per hour of use)
  - spot:        False (dedicated endpoints are reserved, not interruptible)

Coverage:
  - provider slug + corrected billing semantics (per_hour + prepaid_balance)
  - static dedicated-endpoint table correctness (~10 key GPU SKUs)
  - per-hour pricing integrity (usd_per_unit is the hourly rate; no /3600)
  - every ComputePrice carries a source URL
  - fetch_model_prices returns [] (no public per-token catalog)
  - registration in all_sources()
"""

from __future__ import annotations

import pytest

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ProviderBilling,
)
from general_ludd.pricing_intel.sources import HuggingFaceSource, all_sources


# ---------------------------------------------------------------------------
# Identity + billing semantics
# ---------------------------------------------------------------------------

class TestHuggingFaceIdentity:
    def test_provider_slug(self) -> None:
        assert HuggingFaceSource().provider_slug() == "huggingface"

    def test_billing_returns_provider_billing(self) -> None:
        b = HuggingFaceSource().billing()
        assert isinstance(b, ProviderBilling)
        assert b.provider == "huggingface"

    def test_billing_granularity_is_per_hour(self) -> None:
        """Dedicated endpoints bill per hour, not per token."""
        b = HuggingFaceSource().billing()
        assert b.granularity == BillingGranularity.per_hour, (
            f"HuggingFace dedicated endpoints bill per hour; got {b.granularity}"
        )

    def test_billing_terms_are_prepaid_balance(self) -> None:
        """HF dedicated endpoints consume a prepaid account balance."""
        b = HuggingFaceSource().billing()
        assert b.terms == BillingTerms.prepaid_balance, (
            f"HuggingFace dedicated endpoints use prepaid_balance; got {b.terms}"
        )

    def test_billing_terms_not_postpaid_per_use(self) -> None:
        """The old (wrong) classification was postpaid_per_use; must not regress."""
        b = HuggingFaceSource().billing()
        assert b.terms != BillingTerms.postpaid_per_use
        assert b.granularity != BillingGranularity.per_token

    def test_billing_currency_is_usd(self) -> None:
        assert HuggingFaceSource().billing().currency == "USD"

    def test_billing_notes_reference_source(self) -> None:
        b = HuggingFaceSource().billing()
        assert "huggingface.co" in b.notes.lower(), (
            f"HuggingFace billing notes should reference huggingface.co; got: {b.notes}"
        )


# ---------------------------------------------------------------------------
# Static dedicated-endpoint compute table
# ---------------------------------------------------------------------------

class TestHuggingFaceComputePrices:
    def test_returns_non_empty_list(self) -> None:
        prices = HuggingFaceSource().fetch_compute_prices()
        assert isinstance(prices, list)
        assert len(prices) >= 8, (
            f"expected at least 8 dedicated-endpoint SKUs; got {len(prices)}"
        )

    def test_all_entries_are_compute_price(self) -> None:
        for p in HuggingFaceSource().fetch_compute_prices():
            assert isinstance(p, ComputePrice)

    def test_all_entries_have_provider_huggingface(self) -> None:
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.provider == "huggingface", (
                f"ComputePrice provider must be 'huggingface'; got {p.provider}"
            )

    def test_covers_key_gpu_families(self) -> None:
        """Table must cover the headline GPU families HF sells."""
        prices = HuggingFaceSource().fetch_compute_prices()
        gpu_types = " ".join((p.gpu_type or "").upper() for p in prices)
        for needle in ["T4", "L4", "L40", "A10", "A100", "H100"]:
            assert needle in gpu_types, (
                f"HuggingFace static table missing GPU family '{needle}'; "
                f"got gpu_types: {gpu_types}"
            )

    def test_includes_h100_and_h200(self) -> None:
        """H100 and H200 are the highest-tier HF SKUs and must be present."""
        prices = HuggingFaceSource().fetch_compute_prices()
        gpu_types = " ".join((p.gpu_type or "").upper() for p in prices)
        assert "H100" in gpu_types, "HuggingFace table must include H100"
        assert "H200" in gpu_types, "HuggingFace table must include H200"

    def test_includes_a100_40gb_and_80gb_variants(self) -> None:
        """HF sells both 40GB and 80GB A100 variants; both must be present."""
        prices = HuggingFaceSource().fetch_compute_prices()
        joined = " ".join((p.gpu_type or "").upper() for p in prices)
        assert "A100" in joined and "40GB" in joined, (
            "A100 40GB variant missing from HF table"
        )
        assert "80GB" in joined, "A100 80GB variant missing from HF table"

    def test_all_prices_are_per_hour_granularity(self) -> None:
        """Dedicated endpoints are billed per hour."""
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.granularity == BillingGranularity.per_hour, (
                f"HuggingFace ComputePrice {p.sku} granularity must be per_hour; "
                f"got {p.granularity}"
            )

    def test_usd_per_unit_is_hourly_rate(self) -> None:
        """usd_per_unit must be the raw hourly rate (NOT divided by 3600).

        per_hour granularity means usd_per_unit IS USD/hour. If a value looks
        like a per-second rate (< 0.01), the implementation has a conversion bug.
        """
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.usd_per_unit >= 0.50, (
                f"HuggingFace {p.sku} usd_per_unit={p.usd_per_unit} looks like "
                "a per-second rate, not a per-hour rate (should be >= $0.50/hr)"
            )

    def test_all_prices_have_source_url(self) -> None:
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.source, f"HuggingFace ComputePrice {p.sku} has no source"
            assert "huggingface.co" in p.source, (
                f"HuggingFace source should reference huggingface.co; got {p.source}"
            )

    def test_all_prices_have_fetched_at(self) -> None:
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.fetched_at > 0, (
                f"HuggingFace ComputePrice {p.sku} missing fetched_at"
            )

    def test_terms_are_prepaid_balance_on_every_price(self) -> None:
        """Every ComputePrice must carry the prepaid_balance billing terms."""
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.terms == BillingTerms.prepaid_balance, (
                f"HuggingFace ComputePrice {p.sku} terms={p.terms}; "
                "expected prepaid_balance"
            )

    def test_dedicated_endpoints_are_not_spot(self) -> None:
        """Dedicated endpoints are reserved, not interruptible spot instances."""
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.spot is False, (
                f"HuggingFace dedicated endpoint {p.sku} should not be spot"
            )

    def test_gpu_count_populated(self) -> None:
        """Every dedicated endpoint SKU should report its GPU count."""
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.gpu_count is not None and p.gpu_count >= 1, (
                f"HuggingFace ComputePrice {p.sku} missing gpu_count"
            )

    def test_specific_t4_price_present(self) -> None:
        prices = HuggingFaceSource().fetch_compute_prices()
        t4 = [p for p in prices if "T4" in (p.gpu_type or "").upper()]
        assert t4, "T4 SKU missing from HuggingFace table"

    def test_usd_per_hour_helper_matches_unit(self) -> None:
        """ComputePrice.usd_per_hour() must return usd_per_unit for per_hour granularity."""
        for p in HuggingFaceSource().fetch_compute_prices():
            assert p.usd_per_hour() == pytest.approx(p.usd_per_unit)


# ---------------------------------------------------------------------------
# Model prices: no public per-token catalog
# ---------------------------------------------------------------------------

class TestHuggingFaceModelPrices:
    def test_fetch_model_prices_returns_empty(self) -> None:
        """No public per-token catalog exists for HuggingFace; returns []."""
        assert HuggingFaceSource().fetch_model_prices() == []


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestHuggingFaceRegistration:
    def test_registered_in_all_sources(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert "huggingface" in slugs

    def test_all_sources_has_exactly_one_huggingface(self) -> None:
        slugs = [s.provider_slug() for s in all_sources()]
        assert slugs.count("huggingface") == 1, (
            f"expected exactly one 'huggingface' source; got {slugs.count('huggingface')}"
        )
