"""Integration/e2e tests for the observability router wired into the daemon.

Proves wire_observability() registers all /api/observe/* endpoints and
they respond correctly with a real FastAPI app + ConnectorRegistry.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.observe import register, wire_observability


class TestObsRouterDaemonE2E:
    def test_wire_observability_registers_all_endpoints(self) -> None:
        app = FastAPI()
        wire_observability(app, {}, [])
        client = TestClient(app)

        r1 = client.get("/api/observe/sources")
        assert r1.status_code == 200
        body = r1.json()
        assert body["sources"] == []
        assert body["count"] == 0

        r2 = client.get("/api/observe/health")
        assert r2.status_code == 200
        assert r2.json()["health"] == {}

    def test_sources_endpoint_lists_registered_sources(self) -> None:
        app = FastAPI()
        wire_observability(app, {}, [])
        client = TestClient(app)

        r = client.get("/api/observe/sources")
        assert r.status_code == 200
        body = r.json()
        assert body["sources"] == []
        assert body["count"] == 0

    def test_query_returns_404_for_unregistered_source_in_empty_registry(self) -> None:
        app = FastAPI()
        wire_observability(app, {}, [])
        client = TestClient(app)

        r = client.post(
            "/api/observe/query",
            json={"source": "nonexistent", "spec": {"query": "up"}},
        )
        assert r.status_code == 404

    def test_query_unknown_source_returns_404(self) -> None:
        app = FastAPI()
        wire_observability(app, {}, [])
        client = TestClient(app)

        r = client.post(
            "/api/observe/query",
            json={"source": "nonexistent", "spec": {}},
        )
        assert r.status_code == 404
        assert "nonexistent" in r.json()["detail"]

    def test_query_invalid_request_body_returns_422(self) -> None:
        app = FastAPI()
        wire_observability(app, {}, [])
        client = TestClient(app)

        r = client.post("/api/observe/query", json={"spec": {}})
        assert r.status_code == 422

    def test_pricing_endpoints_degrade_when_no_catalog(self) -> None:
        app = FastAPI()
        app.state._connector_registry = None
        app.state._pricing_catalog = None
        register(app, {})
        client = TestClient(app)

        r1 = client.get("/api/pricing")
        assert r1.status_code == 200
        assert r1.json() == {"prices": [], "count": 0}

        r2 = client.get("/api/pricing/compute")
        assert r2.status_code == 200
        assert r2.json() == {"prices": [], "count": 0}

        r3 = client.get("/api/pricing/catalog")
        assert r3.status_code == 200
        assert r3.json()["counts"]["model_prices"] == 0

    def test_wire_observability_stores_registry_on_app_state(self) -> None:
        app = FastAPI()
        registry = wire_observability(app, {}, [])
        from general_ludd.connectors.registry import ConnectorRegistry

        assert isinstance(app.state._connector_registry, ConnectorRegistry)
        assert registry is app.state._connector_registry

    def test_health_endpoint_with_empty_registry(self) -> None:
        app = FastAPI()
        wire_observability(app, {}, [])
        client = TestClient(app)

        r = client.get("/api/observe/health")
        assert r.status_code == 200
        body = r.json()
        assert body["health"] == {}
        assert body["count"] == 0

    def test_register_without_registry_degrades_gracefully(self) -> None:
        app = FastAPI()
        app.state._connector_registry = None
        app.state._pricing_catalog = None
        register(app, {})
        client = TestClient(app)

        r1 = client.get("/api/observe/sources")
        assert r1.status_code == 200
        assert r1.json()["count"] == 0

        r2 = client.get("/api/observe/health")
        assert r2.status_code == 200
        assert r2.json()["count"] == 0

    def test_sources_returns_by_kind_grouping(self) -> None:
        app = FastAPI()
        wire_observability(app, {}, [])
        client = TestClient(app)

        r = client.get("/api/observe/sources")
        assert r.status_code == 200
        body = r.json()
        assert "by_kind" in body
        assert isinstance(body["by_kind"], dict)
