"""Bounded-growth guard for the degraded-mode in-memory message fallback.

Memory-leak follow-up to P3 (470253a). Without a session factory the daemon
serves ``POST /api/messages`` from an in-memory list on
``_daemon_state["messages"]``. That list was unbounded — a long-lived degraded
daemon would grow it forever. ``routers.messages.register`` now bounds it to
``_MAX_INMEMORY_MESSAGES`` via a ``deque(maxlen=...)`` with FIFO eviction. These
tests drive the degraded POST path past the cap and assert the bound holds + the
most-recent messages survive.

Mirrors the P3 TestLedgerBounds style.
"""

from __future__ import annotations

import collections
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import messages as messages_router
from general_ludd.routers.messages import _MAX_INMEMORY_MESSAGES


def _build_degraded_app() -> tuple[FastAPI, dict[str, Any]]:
    """A bare app with the messages router registered and NO session factory."""
    app = FastAPI()
    daemon_state: dict[str, Any] = {}
    messages_router.register(app, daemon_state)
    return app, daemon_state


def test_register_initializes_bounded_deque():
    _app, daemon_state = _build_degraded_app()
    assert isinstance(daemon_state["messages"], collections.deque)
    assert daemon_state["messages"].maxlen == _MAX_INMEMORY_MESSAGES


def test_register_preserves_preseeded_entries():
    app = FastAPI()
    daemon_state: dict[str, Any] = {"messages": [{"id": "seed", "recipient": "a"}]}
    messages_router.register(app, daemon_state)
    assert isinstance(daemon_state["messages"], collections.deque)
    assert list(daemon_state["messages"]) == [{"id": "seed", "recipient": "a"}]


def test_register_is_idempotent_on_already_bounded_deque():
    app = FastAPI()
    existing = collections.deque(maxlen=_MAX_INMEMORY_MESSAGES)
    existing.append({"id": "x"})
    daemon_state: dict[str, Any] = {"messages": existing}
    messages_router.register(app, daemon_state)
    assert daemon_state["messages"] is existing
    assert list(daemon_state["messages"]) == [{"id": "x"}]


def test_degraded_post_caps_in_memory_messages_with_fifo_eviction():
    app, daemon_state = _build_degraded_app()
    client = TestClient(app)

    over = _MAX_INMEMORY_MESSAGES + 100
    for i in range(over):
        resp = client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "agent-1", "body": f"msg-{i}"},
        )
        assert resp.status_code == 201

    msgs = daemon_state["messages"]
    # Bounded, not unbounded.
    assert len(msgs) == _MAX_INMEMORY_MESSAGES

    bodies = [m["body"] for m in msgs]
    # FIFO eviction: oldest gone, most-recent survive.
    assert "msg-0" not in bodies
    assert f"msg-{over - 1}" in bodies
    first_surviving = over - _MAX_INMEMORY_MESSAGES
    assert f"msg-{first_surviving - 1}" not in bodies
    assert f"msg-{first_surviving}" in bodies


def test_degraded_inbox_still_works_after_eviction():
    """GET /api/messages inbox path stays correct against the deque."""
    app, _daemon_state = _build_degraded_app()
    client = TestClient(app)

    over = _MAX_INMEMORY_MESSAGES + 10
    for i in range(over):
        client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "agent-1", "body": f"m-{i}"},
        )

    resp = client.get("/api/messages", params={"recipient": "agent-1", "unread": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["recipient"] == "agent-1"
    # All returned messages are addressed to the recipient (or broadcast).
    assert all(
        m["recipient"] in ("agent-1", "broadcast") for m in body["messages"]
    )


def test_degraded_ack_marks_surviving_message_read():
    app, daemon_state = _build_degraded_app()
    client = TestClient(app)
    over = _MAX_INMEMORY_MESSAGES + 5
    for i in range(over):
        client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "agent-1", "body": f"m-{i}"},
        )
    surviving_id = daemon_state["messages"][-1]["id"]
    resp = client.post(f"/api/messages/{surviving_id}/ack")
    assert resp.status_code == 200
    assert resp.json()["acked"] is True
    assert resp.json()["id"] == surviving_id
