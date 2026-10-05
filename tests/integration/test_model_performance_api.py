"""Integration tests for model performance API endpoints via the real daemon app.

Tests the four endpoints in routers/model_performance.py:
    - GET /admin/models/performance
    - GET /admin/models/ranking
    - GET /admin/models/router/status
    - PUT /admin/models/router/config

Uses ASGITransport with PSK auth enabled; wires a ModelPerformanceRouter
with mock repo for reproducible performance data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.models.performance_router import ModelPerformanceRouter

PSK = "test-psk-perf"
AUTH = {"Authorization": f"Bearer {PSK}"}

SAMPLE_RANKING = [
    {
        "service": "openai",
        "model_name": "gpt-4o",
        "success_rate": 0.95,
        "avg_latency_ms": 800.0,
        "avg_cost_usd": 0.03,
        "sample_count": 50,
    },
    {
        "service": "anthropic",
        "model_name": "claude-3-haiku",
        "success_rate": 0.90,
        "avg_latency_ms": 300.0,
        "avg_cost_usd": 0.005,
        "sample_count": 30,
    },
    {
        "service": "openai",
        "model_name": "gpt-3.5-turbo",
        "success_rate": 0.85,
        "avg_latency_ms": 200.0,
        "avg_cost_usd": 0.002,
        "sample_count": 100,
    },
]

SAMPLE_SUMMARY = [
    {
        "model_profile_id": "openai/gpt-4o",
        "service": "openai",
        "model_name": "gpt-4o",
        "total_calls": 50,
        "successful_calls": 48,
        "failed_calls": 2,
        "total_cost_usd": 1.50,
        "avg_duration_ms": 800.0,
    },
    {
        "model_profile_id": "anthropic/claude-3-haiku",
        "service": "anthropic",
        "model_name": "claude-3-haiku",
        "total_calls": 30,
        "successful_calls": 27,
        "failed_calls": 3,
        "total_cost_usd": 0.15,
        "avg_duration_ms": 300.0,
    },
]


async def _make_app(monkeypatch):
    """Build a real daemon app with PSK auth and a wired ModelPerformanceRouter."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory

    # Wire a ModelPerformanceRouter with a mock repo so endpoints return
    # deterministic data without needing a populated DB.
    mock_repo = AsyncMock()
    mock_repo.get_summary.return_value = SAMPLE_SUMMARY
    mock_repo.get_summary_with_filters = AsyncMock(return_value=SAMPLE_SUMMARY)
    mock_repo.get_ranking.return_value = SAMPLE_RANKING
    mock_repo.get_best_model.return_value = {
        "service": "openai",
        "model_name": "gpt-4o",
        "composite_score": 0.95,
    }

    router = ModelPerformanceRouter(perf_repo=mock_repo)
    app.state._model_performance_router = router

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


# ── GET /admin/models/performance ────────────────────────────────────────


class TestPerformanceEndpoint:
    @pytest.mark.asyncio
    async def test_performance_summary(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/performance", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "performance" in data
            assert len(data["performance"]) == 2
            assert data["performance"][0]["service"] == "openai"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_performance_with_service_filter(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/performance",
                params={"service": "openai"},
                headers=AUTH,
            )
            assert resp.status_code == 200
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_performance_with_task_type(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/performance",
                params={"task_type": "code"},
                headers=AUTH,
            )
            assert resp.status_code == 200
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_performance_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/performance")
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_performance_not_wired(self, monkeypatch):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=1.0)
        app.state._session_factory = factory
        # Do NOT wire the router — should get "not wired" note.
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        try:
            resp = await client.get("/admin/models/performance", headers=AUTH)
            assert resp.status_code == 200
            data = resp.json()
            assert data["performance"] == []
            assert "not wired" in data.get("note", "")
        finally:
            await client.aclose()
            await engine.dispose()


# ── GET /admin/models/ranking ────────────────────────────────────────────


class TestRankingEndpoint:
    @pytest.mark.asyncio
    async def test_ranking_balanced(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/ranking",
                params={"task_type": "code"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["task_type"] == "code"
            assert data["strategy"] == "balanced"
            assert len(data["ranking"]) == 3
            assert data["ranking"][0]["score"] >= data["ranking"][1]["score"]
            for entry in data["ranking"]:
                assert "service" in entry
                assert "model_name" in entry
                assert "score" in entry
                assert "strategy" in entry
                assert entry["strategy"] == "balanced"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ranking_with_strategy(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/ranking",
                params={"task_type": "code", "strategy": "quality"},
                headers=AUTH,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["strategy"] == "quality"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ranking_missing_task_type_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/ranking", headers=AUTH)
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ranking_invalid_strategy_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/ranking",
                params={"task_type": "code", "strategy": "nonexistent"},
                headers=AUTH,
            )
            assert resp.status_code == 422
            assert "Unknown strategy" in resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ranking_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/ranking",
                params={"task_type": "code"},
            )
            assert resp.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ranking_not_wired_returns_503(self, monkeypatch):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=1.0)
        app.state._session_factory = factory
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        try:
            resp = await client.get(
                "/admin/models/ranking",
                params={"task_type": "code"},
                headers=AUTH,
            )
            assert resp.status_code == 503
            assert "not wired" in resp.text
        finally:
            await client.aclose()
            await engine.dispose()


# ── GET /admin/models/router/status ──────────────────────────────────────


class TestRouterStatusEndpoint:
    @pytest.mark.asyncio
    async def test_router_status_active(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/router/status", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "active"
            assert "config" in data
            assert "strategies" in data["config"]
            assert "defaults" in data["config"]
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_router_status_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/router/status")
            assert resp.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_router_status_not_wired(self, monkeypatch):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=1.0)
        app.state._session_factory = factory
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        try:
            resp = await client.get("/admin/models/router/status", headers=AUTH)
            assert resp.status_code == 200
            assert resp.json()["status"] == "not_initialized"
        finally:
            await client.aclose()
            await engine.dispose()


# ── PUT /admin/models/router/config ──────────────────────────────────────


class TestRouterConfigEndpoint:
    @pytest.mark.asyncio
    async def test_set_strategy(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.put(
                "/admin/models/router/config",
                json={"task_type": "code", "strategy": "fastest"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["task_type"] == "code"
            assert data["strategy"] == "fastest"
            assert data["updated"] is True

            # Verify the change is reflected in status
            status = await client.get("/admin/models/router/status", headers=AUTH)
            assert status.json()["config"]["strategies"]["code"] == "fastest"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_set_strategy_default(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.put(
                "/admin/models/router/config",
                json={"task_type": "code"},
                headers=AUTH,
            )
            assert resp.status_code == 200
            assert resp.json()["strategy"] == "balanced"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_set_strategy_empty_body_defaults(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.put(
                "/admin/models/router/config",
                json={},
                headers=AUTH,
            )
            # Empty body means no task_type → 422
            assert resp.status_code == 422
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_set_strategy_missing_task_type_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.put(
                "/admin/models/router/config",
                json={"strategy": "quality"},
                headers=AUTH,
            )
            assert resp.status_code == 422
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_set_strategy_invalid_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.put(
                "/admin/models/router/config",
                json={"task_type": "code", "strategy": "bad"},
                headers=AUTH,
            )
            assert resp.status_code == 422
            assert "Unknown strategy" in resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_set_strategy_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.put(
                "/admin/models/router/config",
                json={"task_type": "code", "strategy": "quality"},
            )
            assert resp.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_set_strategy_not_wired_returns_503(self, monkeypatch):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=1.0)
        app.state._session_factory = factory
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        try:
            resp = await client.put(
                "/admin/models/router/config",
                json={"task_type": "code", "strategy": "quality"},
                headers=AUTH,
            )
            assert resp.status_code == 503
            assert "not wired" in resp.text
        finally:
            await client.aclose()
            await engine.dispose()
