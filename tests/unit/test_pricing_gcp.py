"""Tests for GCPPricingSource — LIVE GCP Cloud Billing SKU catalog (google-cloud-billing).

Covers:
  - provider slug + billing semantics (postpaid_monthly, per_second)
  - Cloud Billing SKU catalog list_skus response parsing
  - GPU SKU filtering (resource_family=Compute + resource_group=GPU or description~"GPU")
  - GOOGLE_APPLICATION_CREDENTIALS gating: skip cleanly when unset (no import / network)
  - Fail-soft: ImportError, API error, malformed SKU entries, missing prices
  - Per-second pricing conversion (USD/hr -> USD/s) from Money(units + nanos)
  - Spot (Preemptible usage_type) detection
  - Source URL documented on every ComputePrice
  - list_skus called with the Compute Engine service parent
  - fetch_model_prices returns [] (GCP offers compute, not model API)

``google-cloud-billing`` is an OPTIONAL runtime dependency; the source uses a
lazy import inside ``_get_client``. Tests mock that method so the suite runs
without the SDK installed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
)
from general_ludd.pricing_intel.sources import GCPPricingSource

# ---------------------------------------------------------------------------
# Helpers: build Cloud Billing SKU objects (mimic protobuf message shape)
# ---------------------------------------------------------------------------


def _money(units: int, nanos: int) -> SimpleNamespace:
    """google.type.Money shape: whole units + fractional nanos (1e9 per unit)."""
    return SimpleNamespace(units=units, nanos=nanos, currency_code="USD")


def _tier_rate(units: int, nanos: int, start: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        start_usage_amount=start,
        unit_price=_money(units, nanos),
    )


def _pricing_expression(units: int, nanos: int) -> SimpleNamespace:
    return SimpleNamespace(
        usage_unit="h",
        display_quantity=1.0,
        tiered_rates=[_tier_rate(units, nanos)],
    )


def _pricing_info(units: int, nanos: int) -> SimpleNamespace:
    return SimpleNamespace(
        effective_time=None,
        pricing_expression=_pricing_expression(units, nanos),
    )


def _sku(
    *,
    sku_id: str,
    description: str,
    resource_family: str = "Compute",
    resource_group: str = "GPU",
    usage_type: str = "OnDemand",
    service_regions: list[str] | None = None,
    units: int = 0,
    nanos: int = 0,
    pricing_infos: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    """Build a SKU in the shape returned by CloudCatalogClient.list_skus."""
    category = SimpleNamespace(
        service_display_name="Compute Engine",
        resource_family=resource_family,
        resource_group=resource_group,
        usage_type=usage_type,
    )
    infos = pricing_infos if pricing_infos is not None else [_pricing_info(units, nanos)]
    return SimpleNamespace(
        name=f"services/6F81-5844-456A/skus/{sku_id}",
        sku_id=sku_id,
        description=description,
        category=category,
        pricing_info=infos,
        service_regions=service_regions or ["us-central1"],
        geo_taxonomy_type="REGIONAL",
    )


# Representative GPU SKUs from Compute Engine (us-central1).
SAMPLE_SKUS: list[SimpleNamespace] = [
    # A100 40GB on-demand: $3.673/hr = $3 + 673_000_000 nanos
    _sku(
        sku_id="a2-highgpu-1g-ondemand",
        description="A2 Highgpu 1G Gpu",
        resource_group="GPU",
        usage_type="OnDemand",
        units=3,
        nanos=673_000_000,
    ),
    # H100 on-demand: $98.328/hr
    _sku(
        sku_id="a3-highgpu-8g-ondemand",
        description="A3 Highgpu 8G Gpu",
        resource_group="GPU",
        usage_type="OnDemand",
        units=98,
        nanos=328_000_000,
    ),
    # A100 40GB preemptible (spot): $1.102/hr
    _sku(
        sku_id="a2-highgpu-1g-preempt",
        description="A2 Highgpu 1G Gpu Preemptible",
        resource_group="GPU",
        usage_type="Preemptible",
        units=1,
        nanos=102_000_000,
    ),
    # Non-GPU compute SKU (Storage PD); must be dropped by the GPU filter.
    _sku(
        sku_id="storage-pd-standard",
        description="Storage PD Capacity Standard",
        resource_family="Storage",
        resource_group="PDStandard",
        usage_type="OnDemand",
        units=0,
        nanos=40_000_000,
    ),
]


def _mock_gcp_client(skus: list[Any]) -> MagicMock:
    """Build a mock CloudCatalogClient whose list_skus returns ``skus``."""
    client = MagicMock()
    # list_skus returns a pager; the SDK paged iterator yields SKUs.
    pager = MagicMock()
    pager.__iter__ = lambda self: iter(skus)
    client.list_skus.return_value = pager
    return client


# ---------------------------------------------------------------------------
# Identity / billing semantics
# ---------------------------------------------------------------------------


class TestGCPPricingIdentity:
    def test_provider_slug(self) -> None:
        assert GCPPricingSource().provider_slug() == "gcp_live"

    def test_billing_terms(self) -> None:
        b = GCPPricingSource().billing()
        assert b.provider == "gcp_live"
        # GCP bills postpaid monthly — same model as the static GCPSource.
        assert b.terms == BillingTerms.postpaid_monthly
        assert b.granularity == BillingGranularity.per_second
        assert b.spot_available is True
        assert b.currency == "USD"


# ---------------------------------------------------------------------------
# GOOGLE_APPLICATION_CREDENTIALS gating — skip cleanly when unset
# ---------------------------------------------------------------------------


class TestGCPPricingCredsGate:
    def test_no_creds_returns_empty_and_no_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without GOOGLE_APPLICATION_CREDENTIALS, skip cleanly (no SDK, no network)."""
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        src = GCPPricingSource()
        with patch.object(GCPPricingSource, "_get_client") as mock_get_client:
            prices = src.fetch_compute_prices()
        assert prices == []
        mock_get_client.assert_not_called()


# ---------------------------------------------------------------------------
# Response parsing — happy path
# ---------------------------------------------------------------------------


class TestGCPPricingFetch:
    def test_parses_skus_into_compute_prices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each GPU SKU becomes a per-second ComputePrice."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        client = _mock_gcp_client(SAMPLE_SKUS)
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()

        # 3 GPU SKUs; the Storage PD SKU is dropped.
        assert len(prices) == 3
        sku_ids = {p.sku for p in prices}
        assert sku_ids == {
            "a2-highgpu-1g-ondemand",
            "a3-highgpu-8g-ondemand",
            "a2-highgpu-1g-preempt",
        }

    def test_per_second_conversion_from_money(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Money(units=3, nanos=673_000_000) -> $3.673/hr -> $3.673/3600 per second."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        client = _mock_gcp_client(SAMPLE_SKUS)
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()

        a100 = next(p for p in prices if p.sku == "a2-highgpu-1g-ondemand")
        hourly = 3 + 673_000_000 / 1e9  # 3.673
        assert a100.usd_per_unit == pytest.approx(hourly / 3600.0)
        assert a100.granularity == BillingGranularity.per_second
        assert a100.terms == BillingTerms.postpaid_monthly
        assert a100.spot is False
        assert a100.provider == "gcp_live"
        assert a100.gpu_type  # non-empty GPU type label

    def test_spot_detection_for_preemptible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SKUs with category.usage_type == 'Preemptible' are marked spot=True."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        client = _mock_gcp_client(SAMPLE_SKUS)
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()

        preempt = next(
            p for p in prices if p.sku == "a2-highgpu-1g-preempt"
        )
        assert preempt.spot is True

    def test_on_demand_marked_not_spot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        client = _mock_gcp_client(SAMPLE_SKUS)
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()
        ondemand = next(
            p for p in prices if p.sku == "a3-highgpu-8g-ondemand"
        )
        assert ondemand.spot is False

    def test_filters_to_gpu_skus(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only GPU-family / GPU-description SKUs survive client-side filtering."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        client = _mock_gcp_client(SAMPLE_SKUS)
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()
        assert prices, "expected non-empty GPU price list"
        # Every returned SKU should have GPU origin (the Storage SKU is dropped).
        for p in prices:
            assert "storage" not in p.sku.lower()

    def test_all_prices_have_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        client = _mock_gcp_client(SAMPLE_SKUS)
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()
        assert prices
        for p in prices:
            assert p.source, f"Price for {p.sku} missing source"
            assert "cloud.google.com" in p.source.lower() or "billing" in p.source.lower()

    def test_list_skus_called_with_compute_engine_parent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_skus parent MUST be 'services/6F81-5844-456A' (Compute Engine)."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        client = _mock_gcp_client(SAMPLE_SKUS)
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            GCPPricingSource().fetch_compute_prices()
        assert client.list_skus.called
        call_args = client.list_skus.call_args
        # Parent may be passed positionally or as kwarg.
        parent = call_args.kwargs.get("parent")
        if parent is None and call_args.args:
            parent = call_args.args[0]
        assert parent == "services/6F81-5844-456A"


# ---------------------------------------------------------------------------
# Fail-soft paths
# ---------------------------------------------------------------------------


class TestGCPPricingFailSoft:
    def test_import_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If google-cloud-billing is not installed, skip cleanly."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")

        def _raise_importerror() -> Any:
            raise ImportError("google-cloud-billing is not installed")

        with patch.object(
            GCPPricingSource, "_get_client", side_effect=_raise_importerror
        ):
            prices = GCPPricingSource().fetch_compute_prices()
        assert prices == []

    def test_api_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Any exception during list_skus iteration -> fail-soft -> []."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        client = MagicMock()
        pager = MagicMock()
        pager.__iter__ = MagicMock(side_effect=RuntimeError("GCP API throttled"))
        client.list_skus.return_value = pager
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()
        assert prices == []

    def test_sku_missing_pricing_info_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A GPU SKU with no pricing_info is dropped, not crashed on."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        no_price = _sku(
            sku_id="gpu-no-price",
            description="A2 Highgpu 1G Gpu",
            pricing_infos=[],
        )
        client = _mock_gcp_client([no_price])
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()
        assert prices == []

    def test_sku_missing_tier_rates_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A GPU SKU whose pricing_expression has no tiered_rates is dropped."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        bad_expr = SimpleNamespace(
            usage_unit="h", display_quantity=1.0, tiered_rates=[]
        )
        bad_info = SimpleNamespace(effective_time=None, pricing_expression=bad_expr)
        bad_sku = _sku(
            sku_id="gpu-empty-rates",
            description="A2 Highgpu 1G Gpu",
            pricing_infos=[bad_info],
        )
        client = _mock_gcp_client([bad_sku])
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()
        assert prices == []

    def test_non_gpu_sku_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A SKU in the Storage family is dropped by the GPU filter."""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
        storage = _sku(
            sku_id="storage-pd",
            description="Storage PD Capacity Standard",
            resource_family="Storage",
            resource_group="PDStandard",
        )
        client = _mock_gcp_client([storage])
        with patch.object(GCPPricingSource, "_get_client", return_value=client):
            prices = GCPPricingSource().fetch_compute_prices()
        assert prices == []


# ---------------------------------------------------------------------------
# Model prices — GCP offers compute, not a model API (Vertex AI is separate)
# ---------------------------------------------------------------------------


class TestGCPPricingModels:
    def test_fetch_model_prices_returns_empty(self) -> None:
        assert GCPPricingSource().fetch_model_prices() == []


# ---------------------------------------------------------------------------
# Registry wiring — GCPPricingSource must be in all_sources()
# ---------------------------------------------------------------------------


class TestGCPPricingRegistry:
    def test_gcp_live_in_all_sources(self) -> None:
        from general_ludd.pricing_intel.sources import all_sources

        slugs = {s.provider_slug() for s in all_sources()}
        assert "gcp_live" in slugs
