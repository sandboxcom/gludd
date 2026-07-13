"""H.3: /readyz conflation fix — TDD tests.

The readiness endpoint treated "task not yet set" (daemon mid-bootstrap)
identically to "task healthy" — both returned 200.

These tests pin the corrected behavior:
- Not-initialized → 503, not 200
- Healthy → 200
- Degraded → 503
- Event loop done/cancelled → 503
"""

from __future__ import annotations

import asyncio
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


class TestH3Readyz:
    """H.3: /readyz distinguishes "not yet initialized" from "healthy"."""

    @pytest.mark.asyncio
    async def test_readyz_503_when_daemon_not_initialized(self, transport):
        """Bare app (no lifespan) has no _event_loop_task → 503 not_initialized."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert "not_initialized" in data["reason"]

    @pytest.mark.asyncio
    async def test_readyz_200_when_all_subsystems_healthy(self, app):
        """When _event_loop_task is running (not done), return 200 ready."""
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
    async def test_readyz_503_when_degraded_flag_set(self, app):
        """_degraded flag set → 503 even if event loop is running."""
        async def _run_forever() -> None:
            while True:
                await asyncio.sleep(3600)

        task = asyncio.create_task(_run_forever())
        app.state._event_loop_task = task
        app.state._degraded = "db connection lost"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "degraded"
            assert "db connection lost" in data["reason"]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_readyz_503_when_event_loop_done(self, app):
        """Event loop completed → 503 not_ready, distinct from not_initialized."""
        async def _noop() -> None:
            return None

        task = asyncio.create_task(_noop())
        await task
        app.state._event_loop_task = task
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["reason"] == "event_loop_done"

    @pytest.mark.asyncio
    async def test_readyz_503_when_event_loop_cancelled(self, app):
        """Event loop cancelled → 503 not_ready, distinct from not_initialized."""
        async def _run_forever() -> None:
            while True:
                await asyncio.sleep(3600)

        task = asyncio.create_task(_run_forever())
        app.state._event_loop_task = task
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["reason"] == "event_loop_cancelled"

    @pytest.mark.asyncio
    async def test_not_initialized_reason_distinct_from_event_loop_done(self, transport):
        """H.3 core: "not_initialized" reason is distinct from "event_loop_done"."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["reason"] == "daemon_not_initialized"

    @pytest.mark.asyncio
    async def test_healthz_still_200_when_not_initialized(self, transport):
        """H.3: /healthz (liveness) is unaffected — still 200 when not initialized."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
