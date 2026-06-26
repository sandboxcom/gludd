"""Coverage for /healthz degradation when the event-loop task is done/cancelled.

N1/C6: a dead/cancelled event-loop task after a successful startup must NOT serve
green from /healthz — the daemon is alive but no longer processing work. /healthz
now mirrors /readyz's check on app.state._event_loop_task. The `_degraded` flag
alone only catches STARTUP failures, so this closes the runtime-death gap.

Tests drive the real factory-built app via ASGITransport (matching
test_daemon.py), without running the lifespan — hermetic: no DB, no network,
no real event loop. The task handle is set directly on app.state.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


async def _finished_task() -> asyncio.Task:
    async def _noop() -> None:
        return None

    task = asyncio.create_task(_noop())
    await task
    return task


@pytest.mark.asyncio
async def test_healthz_degraded_when_event_loop_done(app):
    task = await _finished_task()
    assert task.done() and not task.cancelled()
    app.state._event_loop_task = task

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["reason"] == "event_loop_done"


@pytest.mark.asyncio
async def test_healthz_degraded_when_event_loop_cancelled(app):
    async def _sleep_forever() -> None:
        await asyncio.sleep(9999)

    task = asyncio.create_task(_sleep_forever())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done() and task.cancelled()
    app.state._event_loop_task = task

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["reason"] == "event_loop_cancelled"


@pytest.mark.asyncio
async def test_healthz_healthy_without_event_loop_task(app):
    # No task set → unchanged "healthy" behavior (no regression).
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_healthz_healthy_with_running_event_loop_task(app):
    async def _sleep_long() -> None:
        await asyncio.sleep(9999)

    task = asyncio.create_task(_sleep_long())
    assert not task.done()
    app.state._event_loop_task = task
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_startup_degraded_reason_wins_over_dead_loop(app):
    # A pre-existing _degraded reason is preserved (not overwritten by the loop signal).
    task = await _finished_task()
    app.state._degraded = "startup_failed"
    app.state._event_loop_task = task

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.json()["status"] == "degraded"
    assert resp.json()["reason"] == "startup_failed"
