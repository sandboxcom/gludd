"""Unit tests for the /api/pricing endpoints on routers/observe.py.

The pricing catalog is wired into the daemon (``app.state._pricing_catalog``)
but needs an HTTP surface. These tests cover:

- ``GET /api/pricing``          -> ``catalog.all_model_prices()`` as JSON, count.
- ``GET /api/pricing/compute``  -> ``catalog.all_compute_prices()`` as JSON, count.
- ``GET /api/pricing/catalog``  -> the WHOLE live catalog (model + compute +
  billing + provider slugs) in one JSON facet, with a ``counts`` summary.
- graceful degradation: no catalog on app.state -> empty list + count 0 (a
  daemon running without pricing intel is a valid, safe state).

Auth itself is the daemon middleware's job (PSK-gated, NOT public) — covered by
the same posture as the other observe paths in test_observe_auth_posture.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
    ModelPrice,
)
from general_ludd.routers.observe import register


def _catalog_with_two_prices() -> PricingCatalog:
    """Build a PricingCatalog whose sources return known prices.

    Uses a stub PricingSource so no network is touched.
    """
    from general_ludd.pricing_intel.sources import PricingSource

    class _StubSource(PricingSource):
        def provider_slug(self) -> str:
            return "stub"

        def billing(self) -> Any:  # not exercised by these endpoints
            from general_ludd.pricing_intel.models import ProviderBilling

            return ProviderBilling(
                provider="stub",
                granularity=BillingGranularity.per_token,
                terms=BillingTerms.postpaid_per_use,
            )

        def fetch_model_prices(self) -> list[ModelPrice]:
            return [
                ModelPrice(
                    provider="stub",
                    model_id="stub-1",
                    input_usd_per_1k=0.001,
                    output_usd_per_1k=0.002,
                    source="stub://test",
                ),
                ModelPrice(
                    provider="stub",
                    model_id="stub-2",
                    input_usd_per_1k=0.003,
                    output_usd_per_1k=0.006,
                    source="stub://test",
                ),
            ]

        def fetch_compute_prices(self) -> list[ComputePrice]:
            return [
                ComputePrice(
                    provider="stub",
                    sku="gpu-1x",
                    usd_per_unit=1.5,
                    granularity=BillingGranularity.per_hour,
                    spot=False,
                    terms=BillingTerms.prepaid_balance,
                    source="stub://test",
                ),
            ]

    return PricingCatalog(sources=[_StubSource()])


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.state._pricing_catalog = _catalog_with_two_prices()
    register(app, {})
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestPricingModels:
    def test_returns_all_model_prices(self, client: TestClient) -> None:
        resp = client.get("/api/pricing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        ids = {p["model_id"] for p in data["prices"]}
        assert ids == {"stub-1", "stub-2"}

    def test_model_price_serialized_with_fields(self, client: TestClient) -> None:
        resp = client.get("/api/pricing")
        price = resp.json()["prices"][0]
        # Every ModelPrice field is surfaced.
        assert set(price) >= {
            "provider",
            "model_id",
            "input_usd_per_1k",
            "output_usd_per_1k",
            "source",
        }

    def test_provider_filter(self, client: TestClient) -> None:
        resp = client.get("/api/pricing", params={"provider": "stub"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 2
        # Unknown provider -> empty.
        resp2 = client.get("/api/pricing", params={"provider": "nope"})
        assert resp2.json()["count"] == 0


class TestPricingCompute:
    def test_returns_all_compute_prices(self, client: TestClient) -> None:
        resp = client.get("/api/pricing/compute")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["prices"][0]["sku"] == "gpu-1x"

    def test_compute_price_serialized_with_fields(self, client: TestClient) -> None:
        resp = client.get("/api/pricing/compute")
        price = resp.json()["prices"][0]
        assert set(price) >= {
            "provider",
            "sku",
            "usd_per_unit",
            "granularity",
            "spot",
            "terms",
        }


class TestPricingCatalog:
    """The consolidated ``/api/pricing/catalog`` facet: whole catalog in one JSON."""

    def test_returns_model_and_compute_together(self, client: TestClient) -> None:
        resp = client.get("/api/pricing/catalog")
        assert resp.status_code == 200
        data = resp.json()
        # Both slices are present in a single response.
        ids = {p["model_id"] for p in data["model_prices"]}
        assert ids == {"stub-1", "stub-2"}
        assert [p["sku"] for p in data["compute_prices"]] == ["gpu-1x"]

    def test_known_prices_surface(self, client: TestClient) -> None:
        data = client.get("/api/pricing/catalog").json()
        by_id = {p["model_id"]: p for p in data["model_prices"]}
        assert by_id["stub-1"]["input_usd_per_1k"] == 0.001
        assert data["compute_prices"][0]["usd_per_unit"] == 1.5

    def test_billing_and_providers_and_counts(self, client: TestClient) -> None:
        data = client.get("/api/pricing/catalog").json()
        assert data["providers"] == ["stub"]
        assert [b["provider"] for b in data["billing"]] == ["stub"]
        assert data["counts"] == {
            "model_prices": 2,
            "compute_prices": 1,
            "billing": 1,
            "providers": 1,
        }

    def test_provider_filter_narrows_price_lists(self, client: TestClient) -> None:
        data = client.get(
            "/api/pricing/catalog", params={"provider": "nope"}
        ).json()
        assert data["counts"]["model_prices"] == 0
        assert data["counts"]["compute_prices"] == 0


class TestPricingDegraded:
    def test_models_empty_when_no_catalog(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pricing")
        assert resp.status_code == 200
        assert resp.json() == {"prices": [], "count": 0}

    def test_compute_empty_when_no_catalog(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pricing/compute")
        assert resp.status_code == 200
        assert resp.json() == {"prices": [], "count": 0}

    def test_catalog_empty_when_no_catalog(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pricing/catalog")
        assert resp.status_code == 200
        assert resp.json() == {
            "model_prices": [],
            "compute_prices": [],
            "billing": [],
            "providers": [],
            "counts": {
                "model_prices": 0,
                "compute_prices": 0,
                "billing": 0,
                "providers": 0,
            },
        }
