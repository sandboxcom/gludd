"""Verify /api/status endpoint robustness — V0.2 smoke fix proof.

When the filestore subsystem is unavailable (e.g., disk full, permission denied),
the status endpoint must still return a valid JSON response — not an empty body
or a crash that takes the daemon down.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.todos import register


def _unavailable_session_factory() -> object:
    raise ConnectionError("external database unavailable")


class TestTodoEndpoint:
    def test_create_fails_soft_when_database_unavailable(self) -> None:
        app = FastAPI()
        app.state._session_factory = _unavailable_session_factory
        state: dict[str, object] = {
            "todos": [],
            "tick_metrics": {},
            "quality_gate": {},
        }
        register(app, state)
        client = TestClient(app)

        response = client.post(
            "/api/todos",
            json={
                "title": "degraded-startup-job",
                "work_type": "noop",
                "queue": "core",
                "priority": "medium",
            },
        )

        assert response.status_code == 201
        assert response.json()["title"] == "degraded-startup-job"
        assert client.get("/api/todos").json() == [response.json()]

    def test_todos_fail_soft_when_database_unavailable(self) -> None:
        app = FastAPI()
        app.state._session_factory = _unavailable_session_factory
        state: dict[str, object] = {
            "todos": [],
            "tick_metrics": {},
            "quality_gate": {},
        }
        register(app, state)

        response = TestClient(app).get("/api/todos")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == []


class TestStatusEndpointFileStoreFailure:
    def test_status_returns_json_when_filestore_crashes(self):
        """Proof: /api/status returns valid JSON even when FileStore raises."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}

        register(app, state)

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("disk full")):
            from fastapi.testclient import TestClient
            client = TestClient(app)
            resp = client.get("/api/status")
            assert resp.status_code == 200
            data = json.loads(resp.text)
            assert isinstance(data, dict)
            assert "version" in data
            assert data["filestore_available"] is False
