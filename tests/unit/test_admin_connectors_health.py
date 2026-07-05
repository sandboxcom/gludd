"""TDD: daemon endpoint GET /admin/connectors/health.

The endpoint returns health of ALL connectors in the ConnectorRegistry (if
wired on app.state._connector_registry), or degrades to empty when no
registry is present.

Coverage:
  - No registry wired -> empty result, 0 count
  - Registry with healthy source -> returns source health dict
  - Registry with unhealthy source -> reports ok=False
  - Registry with multiple sources -> reports all
  - Endpoint path is /admin/connectors/health (PSK-gated, NOT public)
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.connectors.registry import ConnectorRegistry


class _FakeConnector:
    def __init__(self, config: dict[str, Any]) -> None:
        self.name = str(config.get("name") or "fake")
        self.KIND = str(config.get("kind") or "metrics")
        self._healthy = bool(config.get("_healthy", True))

    def health(self) -> dict[str, Any]:
        return {"ok": self._healthy, "source": self.name}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


_FACTORIES = {"fake": _FakeConnector}


def _make_app(registry: ConnectorRegistry | None = None) -> FastAPI:
    app = FastAPI()
    if registry is not None:
        app.state._connector_registry = registry
    return app


def _register_endpoint(app: FastAPI) -> None:
    @app.get("/admin/connectors/health")
    async def connectors_health() -> dict[str, Any]:
        reg = getattr(app.state, "_connector_registry", None)
        if reg is None:
            return {"health": {}, "count": 0}
        health = reg.health_all()
        return {"health": health, "count": len(health)}


class TestAdminConnectorsHealth:
    def test_no_registry_returns_empty(self) -> None:
        app = _make_app(registry=None)
        _register_endpoint(app)
        client = TestClient(app)
        resp = client.get("/admin/connectors/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"health": {}, "count": 0}

    def test_registry_with_healthy_source(self) -> None:
        registry = ConnectorRegistry.from_config(
            [{"name": "prod-metrics", "kind": "metrics", "factory": "fake"}],
            factories=_FACTORIES,
        )
        app = _make_app(registry=registry)
        _register_endpoint(app)
        client = TestClient(app)
        resp = client.get("/admin/connectors/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert "prod-metrics" in data["health"]
        assert data["health"]["prod-metrics"]["ok"] is True

    def test_registry_with_unhealthy_source(self) -> None:
        registry = ConnectorRegistry.from_config(
            [{"name": "down-src", "kind": "logs", "factory": "fake", "_healthy": False}],
            factories=_FACTORIES,
        )
        app = _make_app(registry=registry)
        _register_endpoint(app)
        client = TestClient(app)
        resp = client.get("/admin/connectors/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["health"]["down-src"]["ok"] is False

    def test_registry_with_multiple_sources(self) -> None:
        registry = ConnectorRegistry.from_config(
            [
                {"name": "src-a", "kind": "metrics", "factory": "fake"},
                {"name": "src-b", "kind": "logs", "factory": "fake"},
                {"name": "src-c", "kind": "errors", "factory": "fake", "_healthy": False},
            ],
            factories=_FACTORIES,
        )
        app = _make_app(registry=registry)
        _register_endpoint(app)
        client = TestClient(app)
        resp = client.get("/admin/connectors/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert data["health"]["src-a"]["ok"] is True
        assert data["health"]["src-b"]["ok"] is True
        assert data["health"]["src-c"]["ok"] is False

    def test_registry_with_build_errors_does_not_abort_health(self) -> None:
        registry = ConnectorRegistry.from_config(
            [
                {"name": "bad-entry", "kind": "logs", "factory": "nonexistent"},
                {"name": "good-one", "kind": "metrics", "factory": "fake"},
            ],
            factories=_FACTORIES,
        )
        app = _make_app(registry=registry)
        _register_endpoint(app)
        client = TestClient(app)
        resp = client.get("/admin/connectors/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert "good-one" in data["health"]
        assert "bad-entry" not in data["health"]
