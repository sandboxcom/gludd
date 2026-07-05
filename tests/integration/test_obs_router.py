"""Integration tests for the observability router wired into a daemon app."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.observe import wire_observability


def test_get_sources_returns_200() -> None:
    """GET /api/observe/sources returns 200 with empty registry."""
    app = FastAPI()
    wire_observability(app, {}, [])
    client = TestClient(app)
    response = client.get("/api/observe/sources")
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert data["count"] == 0


def test_get_health_returns_200() -> None:
    """GET /api/observe/health returns 200 with empty registry."""
    app = FastAPI()
    wire_observability(app, {}, [])
    client = TestClient(app)
    response = client.get("/api/observe/health")
    assert response.status_code == 200
    data = response.json()
    assert "health" in data
    assert data["count"] == 0


def test_post_query_unknown_source_returns_404() -> None:
    """POST /api/observe/query with unknown source name returns 404."""
    app = FastAPI()
    wire_observability(app, {}, [])
    client = TestClient(app)
    response = client.post(
        "/api/observe/query",
        json={"source": "nonexistent", "spec": {}},
    )
    assert response.status_code == 404
