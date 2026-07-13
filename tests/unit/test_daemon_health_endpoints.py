"""Coverage for the daemon health/readiness probes — C1 gap.

The daemon's liveness (``GET /healthz``), readiness (``GET /readyz``) and status
(``GET /api/status``) probes had no direct TestClient coverage against the real
``create_daemon_app`` factory (the existing endpoint-coverage suite only exercises
hand-built stand-in FastAPI apps for ``/api/status``, never the real handlers).

These tests drive the real factory-built app via ASGITransport (mirroring
``test_daemon_stats_endpoint.py``), without running the lifespan — so they are
hermetic: no DB session factory, no event loop task, no network or model calls.
With no PSK set the suite-wide ``_no_auth_opt_out`` conftest fixture opts into
open access, and all three probes are public read-only paths anyway.
"""

from __future__ import annotations

import contextlib

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestHealthzEndpoint:
    @pytest.mark.asyncio
    async def test_healthz_returns_200(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_healthz_reports_healthy_status(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            data = resp.json()
            # The liveness probe is "healthy" unless the lifespan failed
            # (_degraded). The lifespan never runs here, so _degraded is unset.
            assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_healthz_exposes_auth_posture_fields(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            data = resp.json()
            # A-3: the security posture rides on these boolean fields.
            for key in (
                "no_auth",
                "require_auth",
                "allow_no_auth",
                "auth_degraded",
                "budget_exhausted",
            ):
                assert key in data
                assert isinstance(data[key], bool)

    @pytest.mark.asyncio
    async def test_healthz_does_not_leak_numeric_budget(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            data = resp.json()
            # gateway-health-budget P1: the public probe must expose only the
            # coarse boolean, never the numeric spend posture.
            for leaked in ("daily_spend", "daily_limit", "daily_pct", "per_todo_limit"):
                assert leaked not in data

    @pytest.mark.asyncio
    async def test_healthz_is_public_without_psk(self, transport):
        # No Authorization header — the probe must still serve (public path).
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200


class TestReadyzEndpoint:
    @pytest.mark.asyncio
    async def test_readyz_503_when_daemon_not_initialized(self, transport):
        # H.3: bare app (no lifespan, no _event_loop_task) means the daemon
        # hasn't finished booting — readiness must fail closed (503).
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["reason"] == "daemon_not_initialized"

    @pytest.mark.asyncio
    async def test_readyz_reports_ready_body_when_initialized(self, app):
        # H.3: ready body returned only when _event_loop_task is running.
        import asyncio

        async def _run_forever() -> None:
            while True:
                await asyncio.sleep(3600)

        task = asyncio.create_task(_run_forever())
        app.state._event_loop_task = task
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ready"}
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_readyz_503_when_degraded(self, app):
        # When the lifespan failed, readiness fails closed (503) and surfaces
        # the reason — distinct from liveness, which keeps serving 200.
        app.state._degraded = "boom: startup failed"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "degraded"
            assert "boom" in data["reason"]

    @pytest.mark.asyncio
    async def test_readyz_503_when_event_loop_done(self, app):
        # A finished/cancelled event-loop task means the daemon can no longer
        # accept work: readiness reports not_ready (503).
        import asyncio

        async def _noop() -> None:
            return None

        task = asyncio.create_task(_noop())
        await task  # drive it to completion so .done() is True
        app.state._event_loop_task = task
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["reason"] == "event_loop_done"


class TestApiStatusEndpoint:
    @pytest.mark.asyncio
    async def test_api_status_returns_200(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_api_status_reports_core_health_keys(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            for key in (
                "version",
                "todos_total",
                "queue_depths",
                "tick_metrics",
                "quality_gate",
            ):
                assert key in data

    @pytest.mark.asyncio
    async def test_api_status_quality_gate_not_run_before_lifespan(self, transport):
        # No lifespan/preflight has run, so the quality gate is the "not_run"
        # placeholder rather than a stale or fabricated result.
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            qg = resp.json()["quality_gate"]
            assert qg["overall"] == "not_run"

    @pytest.mark.asyncio
    async def test_api_status_is_public_without_psk(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200
