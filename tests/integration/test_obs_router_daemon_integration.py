"""Integration tests for the observe router daemon integration.

Exercises wire_observability(), the ConnectorRegistry on app.state, and the
/api/observe/sources and /api/observe/health endpoints through the real
daemon app via ASGITransport.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.connectors.registry import ConnectorRegistry
from general_ludd.db.models import Base

PSK = "test-psk-secret"
AUTH = {"Authorization": f"Bearer {PSK}"}


class FakeConnector:
    """A minimal _SourceLike-compatible connector for integration tests."""

    name: str
    KIND: str = "test_dummy"

    def __init__(self, config: dict[str, Any]) -> None:
        self.name = str(config.get("name") or "fake")
        self._connected = True

    def health(self) -> dict[str, Any]:
        return {"ok": True, "name": self.name, "kind": self.KIND}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "ts": 1000000,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "info",
                "message": "test record",
                "value": 42,
                "labels": {"test": "true"},
                "raw": {},
            }
        ]


def _build_connector_configs() -> list[dict[str, Any]]:
    return [
        {
            "name": "dummy-alpha",
            "kind": "test_dummy",
            "factory": "fake",
        },
        {
            "name": "dummy-beta",
            "kind": "test_dummy",
            "factory": "fake",
        },
    ]


async def _make_app(monkeypatch):
    """Build the real daemon app with PSK auth and observability wired."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory

    from general_ludd.routers.observe import wire_observability

    connector_config = _build_connector_configs()
    wire_observability(
        app,
        {},
        connector_config,
        factories={"fake": FakeConnector},
    )

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


# --------------------------------------------------------------------------- #
# ConnectorRegistry on app.state
# --------------------------------------------------------------------------- #
class TestConnectorRegistryOnAppState:
    @pytest.mark.asyncio
    async def test_registry_on_app_state_after_wire(self, monkeypatch) -> None:
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            reg = getattr(app.state, "_connector_registry", None)
            assert isinstance(reg, ConnectorRegistry)
            assert len(reg.names()) == 2
            assert "dummy-alpha" in reg.names()
            assert "dummy-beta" in reg.names()
            assert len(reg.list_sources()) == 2
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_registry_by_kind_groups_connectors(self, monkeypatch) -> None:
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            reg: ConnectorRegistry = app.state._connector_registry
            by_kind = reg.by_kind()
            assert "test_dummy" in by_kind
            assert len(by_kind["test_dummy"]) == 2
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_registry_get_returns_source(self, monkeypatch) -> None:
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            reg: ConnectorRegistry = app.state._connector_registry
            src = reg.get("dummy-alpha")
            assert src is not None
            h = src.health()
            assert h["ok"] is True
            assert h["name"] == "dummy-alpha"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_registry_health_all(self, monkeypatch) -> None:
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            reg: ConnectorRegistry = app.state._connector_registry
            health = reg.health_all()
            assert len(health) == 2
            assert health["dummy-alpha"]["ok"] is True
            assert health["dummy-beta"]["ok"] is True
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_registry_direct_query(self, monkeypatch) -> None:
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            reg: ConnectorRegistry = app.state._connector_registry
            records = reg.query("dummy-alpha", {"limit": 1})
            assert len(records) == 1
            assert records[0]["source"] == "dummy-alpha"
            assert records[0]["value"] == 42
        finally:
            await client.aclose()
            await engine.dispose()


# --------------------------------------------------------------------------- #
# /api/observe endpoints
# --------------------------------------------------------------------------- #
class TestObserveSourcesEndpoint:
    @pytest.mark.asyncio
    async def test_get_sources_returns_200_with_source_list(self, monkeypatch) -> None:
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/observe/sources", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["count"] == 2
            assert len(data["sources"]) == 2
            names = {s["name"] for s in data["sources"]}
            assert names == {"dummy-alpha", "dummy-beta"}
            assert "test_dummy" in data["by_kind"]
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_health_returns_200(self, monkeypatch) -> None:
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/observe/health", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["count"] == 2
            assert data["health"]["dummy-alpha"]["ok"] is True
            assert data["health"]["dummy-beta"]["ok"] is True
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sources_requires_psk(self, monkeypatch) -> None:
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/observe/sources")
            assert resp.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_health_requires_psk(self, monkeypatch) -> None:
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/observe/health")
            assert resp.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_query_endpoint_unknown_source_404(self, monkeypatch) -> None:
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/observe/query",
                json={"source": "nonexistent", "spec": {}},
                headers=AUTH,
            )
            assert resp.status_code == 404
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_query_endpoint_returns_records(self, monkeypatch) -> None:
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/api/observe/query",
                json={"source": "dummy-alpha", "spec": {"limit": 1}},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["source"] == "dummy-alpha"
            assert data["count"] == 1
            assert len(data["records"]) == 1
            assert data["records"][0]["value"] == 42
        finally:
            await client.aclose()
            await engine.dispose()


# --------------------------------------------------------------------------- #
# wire_observability with no config
# --------------------------------------------------------------------------- #
class TestWireObservabilityEmpty:
    @pytest.mark.asyncio
    async def test_empty_config_returns_empty_sources(self, monkeypatch) -> None:
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=1.0)
        app.state._session_factory = factory

        from general_ludd.routers.observe import wire_observability

        wire_observability(app, {}, None)

        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        try:
            resp = await client.get("/api/observe/sources", headers=AUTH)
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 0
            assert data["sources"] == []

            health_resp = await client.get("/api/observe/health", headers=AUTH)
            assert health_resp.status_code == 200
            assert health_resp.json()["count"] == 0
        finally:
            await client.aclose()
            await engine.dispose()
