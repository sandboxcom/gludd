"""Bounded-growth guard for the degraded-mode in-memory todo fallback.

Memory-leak follow-up to P3 (470253a). Without a session factory the daemon
serves ``POST /api/todos`` from an in-memory list on ``_daemon_state["todos"]``.
That list was unbounded — a long-lived degraded daemon would grow it forever.
``routers.todos.register`` now bounds it to ``_MAX_INMEMORY_TODOS`` via a
``deque(maxlen=...)`` with FIFO eviction. These tests drive the degraded POST
path past the cap and assert the bound holds + the most-recent todos survive.

Mirrors the P3 TestLedgerBounds style.
"""

from __future__ import annotations

import collections
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import todos as todos_router
from general_ludd.routers.todos import _MAX_INMEMORY_TODOS
from general_ludd.routers.web_search import SlidingWindowRateLimiter


def _build_degraded_app() -> tuple[FastAPI, dict[str, Any]]:
    """A bare app with the todos router registered and NO session factory.

    No ``app.state._session_factory`` => every request takes the in-memory
    degraded fallback. No auth middleware is installed, so requests reach the
    handler directly.
    """
    app = FastAPI()
    app.state._todo_rate_limiter = SlidingWindowRateLimiter(
        max_requests=_MAX_INMEMORY_TODOS + 200,
        window_seconds=60.0,
    )
    daemon_state: dict[str, Any] = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    todos_router.register(app, daemon_state)
    return app, daemon_state


def test_first_degraded_access_converts_todos_to_bounded_deque() -> None:
    app, daemon_state = _build_degraded_app()
    assert daemon_state["todos"] == []

    response = TestClient(app).get("/api/todos")

    assert response.status_code == 200
    assert isinstance(daemon_state["todos"], collections.deque)
    assert daemon_state["todos"].maxlen == _MAX_INMEMORY_TODOS


def test_register_preserves_preseeded_entries() -> None:
    app = FastAPI()
    daemon_state: dict[str, Any] = {"todos": [{"todo_id": "seed", "queue": "core"}]}
    todos_router.register(app, daemon_state)

    response = TestClient(app).get("/api/todos")

    assert response.status_code == 200
    assert isinstance(daemon_state["todos"], collections.deque)
    assert list(daemon_state["todos"]) == [{"todo_id": "seed", "queue": "core"}]


def test_register_is_idempotent_on_already_bounded_deque() -> None:
    app = FastAPI()
    existing: collections.deque[dict[str, object]] = collections.deque(
        maxlen=_MAX_INMEMORY_TODOS
    )
    existing.append({"todo_id": "x"})
    daemon_state: dict[str, Any] = {"todos": existing}
    todos_router.register(app, daemon_state)
    # Same object reused (not re-wrapped), contents preserved.
    assert daemon_state["todos"] is existing
    assert list(daemon_state["todos"]) == [{"todo_id": "x"}]


def test_degraded_post_caps_in_memory_todos_with_fifo_eviction() -> None:
    app, daemon_state = _build_degraded_app()
    client = TestClient(app)

    over = _MAX_INMEMORY_TODOS + 100
    for i in range(over):
        resp = client.post(
            "/api/todos",
            json={"title": f"todo-{i}", "queue": "core"},
        )
        assert resp.status_code == 201

    todos = daemon_state["todos"]
    # Bounded, not unbounded.
    assert len(todos) == _MAX_INMEMORY_TODOS

    titles = [t["title"] for t in todos]
    # FIFO eviction: the oldest are gone, the most-recent survive.
    assert "todo-0" not in titles
    assert f"todo-{over - 1}" in titles
    first_surviving = over - _MAX_INMEMORY_TODOS
    assert f"todo-{first_surviving - 1}" not in titles
    assert f"todo-{first_surviving}" in titles


def test_degraded_list_still_works_after_eviction() -> None:
    """GET /api/todos list/filter path stays correct against the deque."""
    app, _daemon_state = _build_degraded_app()
    client = TestClient(app)

    over = _MAX_INMEMORY_TODOS + 10
    for i in range(over):
        client.post("/api/todos", json={"title": f"t-{i}", "queue": "core"})

    resp = client.get("/api/todos", params={"queue": "core", "limit": 500})
    assert resp.status_code == 200
    body = resp.json()
    # Page is bounded by the limit and all returned items are queue=core.
    assert len(body) <= 500
    assert all(item["queue"] == "core" for item in body)


@pytest.mark.parametrize("limit", [1, 50])
def test_degraded_get_by_id_finds_surviving_todo(limit: int) -> None:
    app, daemon_state = _build_degraded_app()
    client = TestClient(app)
    over = _MAX_INMEMORY_TODOS + 5
    for i in range(over):
        client.post("/api/todos", json={"title": f"t-{i}", "queue": "core"})
    # A surviving (recent) todo is fetchable by id; an evicted one 404s.
    surviving_id = daemon_state["todos"][-1]["todo_id"]
    resp = client.get(f"/api/todos/{surviving_id}")
    assert resp.status_code == 200
    assert resp.json()["todo_id"] == surviving_id
