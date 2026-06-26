"""Regression test for XT-9: the GET /api/messages degraded in-memory fallback
must enforce tenant isolation by `project_id`, exactly like the primary DB path
(`repo.inbox(project_id=...)`).

Before the fix, the fallback loop iterated `_daemon_state["messages"]` filtering
only by recipient/unread/broadcast and ignored `project_id`, so a caller scoped
to project A received project B's messages whenever the daemon ran without a DB
session factory (degraded boot). This pins the filter and the no-project-id
back-compat behaviour.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.messages import register


def _build_client() -> TestClient:
    """A FastAPI app with the message routes but NO `_session_factory`, so every
    request takes the degraded in-memory fallback path."""
    app = FastAPI()
    state: dict = {
        "messages": [
            {
                "recipient": "agentA",
                "project_id": "proj-1",
                "read_at": None,
                "created_at": "2026-06-26T00:00:00Z",
                "body": "for project 1",
            },
            {
                "recipient": "agentA",
                "project_id": "proj-2",
                "read_at": None,
                "created_at": "2026-06-26T00:00:01Z",
                "body": "for project 2",
            },
        ]
    }
    register(app, state)
    # Intentionally do NOT set app.state._session_factory -> fallback path.
    return TestClient(app)


def test_fallback_inbox_scopes_to_project_id() -> None:
    client = _build_client()
    resp = client.get(
        "/api/messages", params={"recipient": "agentA", "project_id": "proj-1"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert [m["project_id"] for m in data["messages"]] == ["proj-1"]


def test_fallback_inbox_other_project_does_not_leak() -> None:
    client = _build_client()
    resp = client.get(
        "/api/messages", params={"recipient": "agentA", "project_id": "proj-2"}
    )
    data = resp.json()
    assert data["count"] == 1
    assert data["messages"][0]["body"] == "for project 2"


def test_fallback_inbox_without_project_id_returns_all() -> None:
    # Back-compat: an unscoped query (no project_id) still returns every message
    # for the recipient — the filter only engages when project_id is provided.
    client = _build_client()
    resp = client.get("/api/messages", params={"recipient": "agentA"})
    data = resp.json()
    assert data["count"] == 2
