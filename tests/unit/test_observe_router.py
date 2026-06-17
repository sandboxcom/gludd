"""Unit tests for routers/observe.py + the wire_observability() helper.

Follows the router test convention (TestClient over a bare FastAPI app with the
router registered and app.state primed), mirroring test_spend_router_coverage.py.

Coverage:
- GET  /api/observe/sources  -> registry.list() metadata (no secrets)
- GET  /api/observe/health   -> health_all() across sources
- POST /api/observe/query    -> query(name, spec) on a NAMED source
- least-privilege: query against an unregistered name -> 404 (no raw-URL egress);
  a request body cannot smuggle a target URL into the connector layer.
- wire_observability(app, state, config) builds the registry from config and
  registers the router in one call (the daemon hookup is then 2 lines).

Connectors are mocked via the registry's ``factories`` hook so no real network
occurs. Auth itself is the daemon middleware's job (these paths are PSK-gated,
NOT public) — verified separately in test_observe_auth_posture.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.connectors.registry import ConnectorRegistry
from general_ludd.routers.observe import register, wire_observability


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


_FACTORIES = {"fake": _FakeSource}


def _registry() -> ConnectorRegistry:
    return ConnectorRegistry.from_config(
        [
            {
                "name": "prod-logs",
                "kind": "logs",
                "factory": "fake",
                "_records": [{"message": "hello"}],
            },
            {"name": "prod-metrics", "kind": "metrics", "factory": "fake"},
            {"name": "down-src", "kind": "logs", "factory": "fake", "_healthy": False},
        ],
        factories=_FACTORIES,
    )


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.state._connector_registry = _registry()
    register(app, {})
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestSources:
    def test_lists_registered_sources_metadata(self, client: TestClient) -> None:
        resp = client.get("/api/observe/sources")
        assert resp.status_code == 200
        data = resp.json()
        names = {s["name"] for s in data["sources"]}
        assert names == {"prod-logs", "prod-metrics", "down-src"}
        assert data["count"] == 3
        # No secret material in the listing.
        assert "token" not in resp.text.lower() or "token_env" not in resp.text

    def test_sources_grouped_by_kind(self, client: TestClient) -> None:
        resp = client.get("/api/observe/sources")
        by_kind = resp.json()["by_kind"]
        assert sorted(by_kind["logs"]) == ["down-src", "prod-logs"]
        assert by_kind["metrics"] == ["prod-metrics"]

    def test_sources_empty_when_no_registry(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/api/observe/sources")
        assert resp.status_code == 200
        assert resp.json() == {"sources": [], "by_kind": {}, "count": 0}


class TestHealth:
    def test_health_reports_every_source(self, client: TestClient) -> None:
        resp = client.get("/api/observe/health")
        assert resp.status_code == 200
        health = resp.json()["health"]
        assert health["prod-logs"]["ok"] is True
        assert health["down-src"]["ok"] is False


class TestQuery:
    def test_query_named_source_returns_records(self, client: TestClient) -> None:
        resp = client.post(
            "/api/observe/query",
            json={"source": "prod-logs", "spec": {"q": "*"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "prod-logs"
        assert data["records"] == [{"message": "hello", "_spec": {"q": "*"}}]
        assert data["count"] == 1

    def test_query_unknown_source_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/observe/query",
            json={"source": "nope", "spec": {}},
        )
        assert resp.status_code == 404

    def test_query_missing_source_field_422(self, client: TestClient) -> None:
        resp = client.post("/api/observe/query", json={"spec": {}})
        assert resp.status_code == 422

    def test_query_default_spec_is_empty_dict(self, client: TestClient) -> None:
        # spec is optional; omitting it queries with {}.
        resp = client.post("/api/observe/query", json={"source": "prod-logs"})
        assert resp.status_code == 200
        assert resp.json()["records"] == [{"message": "hello", "_spec": {}}]


class TestLeastPrivilegeSsrf:
    def test_query_body_cannot_smuggle_a_target_url(self, client: TestClient) -> None:
        # Even if a caller puts a URL-shaped value in 'source', it is treated as
        # a (non-existent) registered NAME — there is NO fetch of that URL.
        resp = client.post(
            "/api/observe/query",
            json={"source": "http://169.254.169.254/latest/meta-data", "spec": {}},
        )
        assert resp.status_code == 404

    def test_query_extra_url_field_is_ignored(self, client: TestClient) -> None:
        # An attacker-supplied 'url' in the body must NOT become an egress target;
        # only the registered 'source' name selects the connector.
        resp = client.post(
            "/api/observe/query",
            json={
                "source": "prod-logs",
                "spec": {},
                "url": "http://attacker.example/exfil",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "prod-logs"


class TestWireObservability:
    def test_wire_builds_registry_and_registers_router(self) -> None:
        app = FastAPI()
        wire_observability(
            app,
            {},
            [
                {
                    "name": "wired-logs",
                    "kind": "logs",
                    "factory": "fake",
                    "_records": [{"message": "wired"}],
                }
            ],
            factories=_FACTORIES,
        )
        client = TestClient(app)
        resp = client.get("/api/observe/sources")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        # And the registry is reachable on app.state for facts/other consumers.
        assert isinstance(app.state._connector_registry, ConnectorRegistry)

    def test_wire_with_empty_config_registers_router_with_no_sources(self) -> None:
        app = FastAPI()
        wire_observability(app, {}, [])
        client = TestClient(app)
        resp = client.get("/api/observe/sources")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
