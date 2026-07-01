"""Regression tests for F6b: GET /api/todos must be bounded to [1, 500]."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


def _make_app_with_todos(n: int):
    import collections

    import general_ludd.daemon as dm

    app = dm.create_daemon_app(tick_interval=0.01)
    # No session factory — use in-memory daemon state.
    todos = [
        {
            "todo_id": f"TODO-{i:04d}",
            "title": f"Todo {i}",
            "description": "",
            "queue": "core",
            "priority": 1,
            "work_type": "code",
            "status": "queued",
            "project_id": None,
        }
        for i in range(n)
    ]
    # After register(), _daemon_state["todos"] is a bounded deque — not a list.
    # Slice assignment ([:]) fails on deque. Replace it wholesale instead.
    dm._daemon_state["todos"] = collections.deque(todos, maxlen=1000)
    return app, todos


@pytest.fixture(autouse=True)
def _clear_daemon_state():
    import collections

    from general_ludd.daemon import _daemon_state
    original = list(_daemon_state["todos"])
    yield
    _daemon_state["todos"] = collections.deque(original, maxlen=1000)


@pytest.mark.asyncio
async def test_default_limit_caps_at_100():
    app, _ = _make_app_with_todos(150)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/api/todos")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) <= 100, f"F6b: default limit must cap at 100, got {len(data)}"


@pytest.mark.asyncio
async def test_explicit_limit_10():
    app, _ = _make_app_with_todos(50)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/api/todos?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 10, f"F6b: ?limit=10 must return exactly 10, got {len(data)}"


@pytest.mark.asyncio
async def test_overlarge_limit_capped_at_500():
    app, _ = _make_app_with_todos(600)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/api/todos?limit=9999")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) <= 500, f"F6b: ?limit=9999 must be capped at 500, got {len(data)}"


@pytest.mark.asyncio
async def test_offset_pages_are_disjoint():
    app, _ = _make_app_with_todos(30)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp1 = await c.get("/api/todos?limit=10&offset=0")
        resp2 = await c.get("/api/todos?limit=10&offset=10")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    ids1 = {t["todo_id"] for t in resp1.json()}
    ids2 = {t["todo_id"] for t in resp2.json()}
    assert len(ids1) == 10
    assert len(ids2) == 10
    assert ids1.isdisjoint(ids2), f"F6b: pages must be disjoint; overlap={ids1 & ids2}"
