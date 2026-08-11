"""Deep tests for routers/observe.py — pricing endpoints, edge cases, model validation.

Extends test_observe_router.py with coverage for:
- Pricing endpoints (GET /api/pricing, /pricing/compute, /pricing/catalog, /pricing/info)
- _get_registry edge cases (non-ConnectorRegistry value on app.state)
- _get_pricing_catalog edge cases (non-PricingCatalog value on app.state)
- wire_observability edge cases (None config, old registry cleanup)
- ObserveQueryRequest model validation (empty source, over-length source)
- Registry-absent degradation paths (health, query)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.connectors.registry import ConnectorRegistry
from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.routers.observe import (
    ObserveQueryRequest,
    _get_pricing_catalog,
    _get_registry,
    register,
    wire_observability,
)

# --------------------------------------------------------------------------- #
# Fake connectors (mirrors test_observe_router.py)
# --------------------------------------------------------------------------- #


class _FakeSource:
    def __init__(self, config: dict[str, Any]) -> None:
        self.name = str(config.get("name") or "fake")
        self.KIND = str(config.get("kind") or "logs")
        self._healthy = bool(config.get("_healthy", True))
        self._records = list(config.get("_records") or [])

    def health(self) -> dict[str, Any]:
        return {"ok": self._healthy, "source": self.name}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(r, _spec=spec) for r in self._records]


class _CloseableSource(_FakeSource):
    def close(self) -> None:
        pass


_FACTORIES = {"fake": _FakeSource, "closeable": _CloseableSource}


def _registry() -> ConnectorRegistry:
    return ConnectorRegistry.from_config(
        [
            {"name": "prod-logs", "kind": "logs", "factory": "fake", "_records": [{"message": "hello"}]},
            {"name": "prod-metrics", "kind": "metrics", "factory": "fake"},
            {"name": "down-src", "kind": "logs", "factory": "fake", "_healthy": False},
        ],
        factories=_FACTORIES,
    )


# --------------------------------------------------------------------------- #
# Fake PricingCatalog
# --------------------------------------------------------------------------- #


@dataclass
class _FakeModelPrice:
    model_id: str = ""
    provider: str = ""
    input_price_per_1k: float = 0.0
    output_price_per_1k: float = 0.0
    context_window: int | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "input_price_per_1k": self.input_price_per_1k,
            "output_price_per_1k": self.output_price_per_1k,
            "context_window": self.context_window,
            "notes": self.notes,
        }


@dataclass
class _FakeComputePrice:
    sku: str = ""
    provider: str = ""
    gpu_type: str = ""
    gpu_count: int = 1
    spot: bool = False
    price_per_hour: float = 0.0

    def usd_per_hour(self) -> float:
        return self.price_per_hour


@dataclass
class _FakeProviderBilling:
    provider: str = ""


@dataclass
class _FakeModelInfo:
    model_id: str = ""
    provider: str = ""
    context_window: int | None = None
    pricing: _FakeModelPrice = field(default_factory=_FakeModelPrice)
    notes: str = ""


class FakePricingCatalog(PricingCatalog):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(sources=[], ttl_seconds=999999)
        self._providers: list[str] = ["openai", "anthropic"]
        self._model_prices = {
            None: [_FakeModelPrice(model_id="gpt-4", provider="openai", input_price_per_1k=0.03)],
            "openai": [_FakeModelPrice(model_id="gpt-4", provider="openai", input_price_per_1k=0.03)],
            "anthropic": [_FakeModelPrice(model_id="claude-3", provider="anthropic", input_price_per_1k=0.015)],
        }
        self._compute_prices = {
            None: [_FakeComputePrice(sku="A100-1x", provider="runpod", gpu_type="A100", price_per_hour=1.99)],
            "runpod": [_FakeComputePrice(sku="A100-1x", provider="runpod", gpu_type="A100", price_per_hour=1.99)],
        }
        self._billing = [_FakeProviderBilling(provider="openai")]
        self._model_infos = {
            None: [_FakeModelInfo(model_id="gpt-4", provider="openai")],
            "openai": [_FakeModelInfo(model_id="gpt-4", provider="openai")],
        }

    def all_model_prices(self, provider: str | None = None, refresh: bool = False) -> list[Any]:
        return self._model_prices.get(provider, [])

    def all_compute_prices(self, provider: str | None = None, **kw: object) -> list[Any]:
        return self._compute_prices.get(provider, [])

    def all_billing(self) -> list[Any]:
        return self._billing

    def provider_slugs(self) -> list[str]:
        return self._providers

    def all_model_info(self, provider: str | None = None, refresh: bool = False) -> list[Any]:
        return self._model_infos.get(provider, [])


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def app_with_registry() -> FastAPI:
    app = FastAPI()
    app.state._connector_registry = _registry()
    app.state._pricing_catalog = FakePricingCatalog()
    register(app, {})
    return app


@pytest.fixture
def client(app_with_registry: FastAPI) -> TestClient:
    return TestClient(app_with_registry)


# --------------------------------------------------------------------------- #
# Pricing endpoints
# --------------------------------------------------------------------------- #


class TestPricingModels:
    def test_returns_model_prices(self, client: TestClient) -> None:
        resp = client.get("/api/pricing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["prices"][0]["model_id"] == "gpt-4"

    def test_filter_by_provider(self, client: TestClient) -> None:
        resp = client.get("/api/pricing?provider=anthropic")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["prices"][0]["model_id"] == "claude-3"

    def test_unknown_provider_returns_empty(self, client: TestClient) -> None:
        resp = client.get("/api/pricing?provider=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["prices"] == []

    def test_degraded_no_catalog(self) -> None:
        app = FastAPI()
        app.state._connector_registry = _registry()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pricing")
        assert resp.status_code == 200
        assert resp.json() == {"prices": [], "count": 0}


class TestPricingCompute:
    def test_returns_compute_prices(self, client: TestClient) -> None:
        resp = client.get("/api/pricing/compute")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["prices"][0]["sku"] == "A100-1x"

    def test_degraded_no_catalog(self) -> None:
        app = FastAPI()
        app.state._connector_registry = _registry()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pricing/compute")
        assert resp.status_code == 200
        assert resp.json() == {"prices": [], "count": 0}


class TestPricingCatalog:
    def test_returns_full_catalog(self, client: TestClient) -> None:
        resp = client.get("/api/pricing/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["model_prices"] == 1
        assert data["counts"]["compute_prices"] == 1
        assert data["counts"]["billing"] == 1
        assert data["counts"]["providers"] == 2
        assert "model_prices" in data
        assert "compute_prices" in data
        assert "billing" in data
        assert "providers" in data

    def test_filter_by_provider_in_catalog(self, client: TestClient) -> None:
        resp = client.get("/api/pricing/catalog?provider=openai")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["model_prices"] == 1

    def test_degraded_no_catalog(self) -> None:
        app = FastAPI()
        app.state._connector_registry = _registry()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pricing/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "model_prices": [],
            "compute_prices": [],
            "billing": [],
            "providers": [],
            "counts": {"model_prices": 0, "compute_prices": 0, "billing": 0, "providers": 0},
        }


class TestPricingInfo:
    def test_returns_model_info(self, client: TestClient) -> None:
        resp = client.get("/api/pricing/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["models"][0]["model_id"] == "gpt-4"

    def test_filter_by_provider(self, client: TestClient) -> None:
        resp = client.get("/api/pricing/info?provider=openai")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_degraded_no_catalog(self) -> None:
        app = FastAPI()
        app.state._connector_registry = _registry()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/pricing/info")
        assert resp.status_code == 200
        assert resp.json() == {"models": [], "count": 0}


# --------------------------------------------------------------------------- #
# _get_registry edge cases
# --------------------------------------------------------------------------- #


class TestGetRegistry:
    def test_returns_none_when_no_state_attr(self) -> None:
        app = FastAPI()
        assert _get_registry(app) is None

    def test_returns_none_when_wrong_type(self) -> None:
        app = FastAPI()
        app.state._connector_registry = "not-a-registry"
        assert _get_registry(app) is None

    def test_returns_registry_when_correct_type(self) -> None:
        app = FastAPI()
        reg = _registry()
        app.state._connector_registry = reg
        assert _get_registry(app) is reg


# --------------------------------------------------------------------------- #
# _get_pricing_catalog edge cases
# --------------------------------------------------------------------------- #


class TestGetPricingCatalog:
    def test_returns_none_when_no_state_attr(self) -> None:
        app = FastAPI()
        assert _get_pricing_catalog(app) is None

    def test_returns_none_when_wrong_type(self) -> None:
        app = FastAPI()
        app.state._pricing_catalog = "not-a-catalog"
        assert _get_pricing_catalog(app) is None

    def test_returns_catalog_when_correct_type(self) -> None:
        app = FastAPI()
        cat = FakePricingCatalog()
        app.state._pricing_catalog = cat
        assert _get_pricing_catalog(app) is cat


# --------------------------------------------------------------------------- #
# wire_observability edge cases
# --------------------------------------------------------------------------- #


class TestWireObservabilityEdgeCases:
    def test_wire_with_none_config_registers_empty_router(self) -> None:
        app = FastAPI()
        reg = wire_observability(app, {}, None)
        assert len(reg.names()) == 0
        client = TestClient(app)
        resp = client.get("/api/observe/sources")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_wire_closes_old_registry(self) -> None:
        app = FastAPI()
        old_reg = ConnectorRegistry.from_config(
            [{"name": "old-src", "kind": "logs", "factory": "closeable"}],
            factories=_FACTORIES,
        )
        app.state._connector_registry = old_reg

        with mock.patch.object(old_reg, "close") as mock_close:
            wire_observability(app, {}, [], factories=_FACTORIES)
            mock_close.assert_called_once()

    def test_wire_with_no_old_registry_close_method_is_safe(self) -> None:
        app = FastAPI()
        old_obj = object()
        app.state._connector_registry = old_obj
        wire_observability(app, {}, [], factories=_FACTORIES)  # must not raise

    def test_wire_calls_register_after_building(self) -> None:
        app = FastAPI()
        app.state._pricing_catalog = FakePricingCatalog()
        wire_observability(app, {}, [])
        client = TestClient(app)
        assert client.get("/api/observe/sources").status_code == 200
        assert client.get("/api/observe/health").status_code == 200
        assert client.get("/api/pricing").status_code == 200
        assert client.get("/api/pricing/compute").status_code == 200
        assert client.get("/api/pricing/catalog").status_code == 200
        assert client.get("/api/pricing/info").status_code == 200

    def test_wire_logs_errors_when_config_has_bad_entries(self, caplog: Any) -> None:
        app = FastAPI()
        with caplog.at_level(logging.WARNING):
            wire_observability(
                app,
                {},
                [
                    {"name": "", "kind": "logs", "factory": "fake"},
                ],
                factories=_FACTORIES,
            )
        assert any("connector config entr" in r.message for r in caplog.records)
        assert len(app.state._connector_registry.errors()) > 0

    def test_wire_logs_source_count_on_success(self, caplog: Any) -> None:
        app = FastAPI()
        with caplog.at_level(logging.INFO):
            wire_observability(
                app,
                {},
                [{"name": "src1", "kind": "logs", "factory": "fake"}],
                factories=_FACTORIES,
            )
        assert any("with 1 source(s)" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# ObserveQueryRequest model validation
# --------------------------------------------------------------------------- #


class TestObserveQueryRequestValidation:
    def test_empty_source_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            ObserveQueryRequest(source="")

    def test_source_string_too_long_rejected(self) -> None:
        with pytest.raises(ValueError):
            ObserveQueryRequest(source="x" * 257)

    def test_max_length_source_accepted(self) -> None:
        req = ObserveQueryRequest(source="x" * 256)
        assert len(req.source) == 256

    def test_spec_defaults_to_empty_dict(self) -> None:
        req = ObserveQueryRequest(source="test")
        assert req.spec == {}

    def test_extra_fields_ignored(self) -> None:
        req = ObserveQueryRequest(source="test", url="http://evil.com")  # type: ignore[call-arg]
        assert req.source == "test"
        assert not hasattr(req, "url")


# --------------------------------------------------------------------------- #
# Registry-absent degradation
# --------------------------------------------------------------------------- #


class TestHealthNoRegistry:
    def test_health_without_registry_returns_empty(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/observe/health")
        assert resp.status_code == 200
        assert resp.json() == {"health": {}, "count": 0}


class TestQueryNoRegistry:
    def test_query_without_registry_returns_404(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/observe/query", json={"source": "anything", "spec": {}})
        assert resp.status_code == 404


class TestQueryEdgeCase:
    def test_query_health_endpoint_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/observe/health")
        assert resp.status_code == 200

    def test_query_count_matches_source_count(self, client: TestClient) -> None:
        resp = client.post("/api/observe/query", json={"source": "prod-logs", "spec": {}})
        assert resp.json()["count"] == 1

    def test_query_source_without_healthy_field(self) -> None:
        app = FastAPI()
        src = _FakeSource({"name": "src", "kind": "logs", "_records": [{"x": 1}]})
        reg = ConnectorRegistry()
        reg._sources = {"src": src}  # type: ignore[assignment]
        app.state._connector_registry = reg
        register(app, {})
        client = TestClient(app)

        resp = client.post("/api/observe/query", json={"source": "src", "spec": {}})
        assert resp.status_code == 200
        assert resp.json()["records"] == [{"x": 1, "_spec": {}}]
