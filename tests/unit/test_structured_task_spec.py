"""Acceptance tests for structured task spec fields — criteria and definition-of-done.

POST /api/todos should accept acceptance_criteria (list of strings) and
definition_of_done (string), and should reject a todo with an empty title.

POST /api/todos/scheduled should also preserve acceptance_criteria and
definition_of_done (the scheduled-create endpoint was dropping them).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import todos as todos_mod
from general_ludd.routers.todos import register


def _make_app() -> FastAPI:
    app = FastAPI()
    register(app, {"todos": [], "tick_metrics": {}, "quality_gate": {}})
    return app


class TestStructuredTaskSpec:
    def test_todo_accepts_criteria(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/todos",
            json={
                "title": "Implement login",
                "acceptance_criteria": ["User can log in with email", "Invalid password shows error"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Implement login"

    def test_todo_accepts_done(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/todos",
            json={
                "title": "Implement login",
                "definition_of_done": "All tests pass and code is reviewed",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Implement login"
        assert data["definition_of_done"] == "All tests pass and code is reviewed"

    def test_todo_pydantic_model_accepts_definition_of_done(self):
        from general_ludd.schemas.todo import Todo
        todo = Todo(
            title="Implement login",
            definition_of_done="All tests pass and code is reviewed",
        )
        assert todo.definition_of_done == "All tests pass and code is reviewed"

    def test_criteria_min_length(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/todos",
            json={"title": "", "acceptance_criteria": []},
        )
        assert resp.status_code == 422

    def test_scheduled_todo_round_trips_criteria_and_done(self):
        """Scheduled todo creation preserves acceptance_criteria and definition_of_done."""
        app = FastAPI()
        register(app, {"todos": [], "tick_metrics": {}, "quality_gate": {}})

        fake_todo = MagicMock()
        fake_todo.todo_id = "TODO-SCHED01"
        fake_todo.title = "Implement login"
        fake_todo.description = ""
        fake_todo.queue = "core"
        fake_todo.priority = 1
        fake_todo.work_type = "code"
        fake_todo.status = "scheduled"
        fake_todo.project_id = None
        fake_todo.version = 1
        fake_todo.created_at = None
        fake_todo.acceptance_criteria = json.dumps(["User can log in", "Error on bad password"])
        fake_todo.definition_of_done = "All tests pass"
        fake_todo.scheduled_at = None
        fake_todo.cron = None
        fake_todo.schedule_timezone = "UTC"
        fake_todo.next_run_at = None
        fake_todo.last_run_at = None
        fake_todo.run_count = 0
        fake_todo.max_runs = None
        fake_todo.schedule_paused = False

        repo = MagicMock()
        repo.create = AsyncMock(return_value=fake_todo)

        @asynccontextmanager
        async def factory() -> Any:
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = factory

        with patch.object(todos_mod, "TodoRepository", return_value=repo):
            client = TestClient(app)
            resp = client.post(
                "/api/todos/scheduled",
                json={
                    "title": "Implement login",
                    "acceptance_criteria": ["User can log in", "Error on bad password"],
                    "definition_of_done": "All tests pass",
                    "cron": "0 9 * * 1-5",
                },
            )

        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["title"] == "Implement login"
        assert data["acceptance_criteria"] == '["User can log in", "Error on bad password"]'
        assert data["definition_of_done"] == "All tests pass"

    def test_get_todo_returns_criteria_and_done(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/todos",
            json={
                "title": "Implement login",
                "acceptance_criteria": ["User can log in", "Error on bad password"],
                "definition_of_done": "All tests pass and code is reviewed",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["acceptance_criteria"] == '["User can log in", "Error on bad password"]'
        assert data["definition_of_done"] == "All tests pass and code is reviewed"

    def test_update_todo_criteria(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/todos",
            json={
                "title": "Original title",
                "acceptance_criteria": ["Initial criteria"],
                "definition_of_done": "Initial done",
            },
        )
        assert resp.status_code == 201
        todo_id = resp.json()["todo_id"]

        resp = client.put(
            f"/api/todos/{todo_id}",
            json={
                "title": "Original title",
                "acceptance_criteria": ["Updated criteria 1", "Updated criteria 2"],
                "definition_of_done": "Updated done",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "Updated criteria 1" in data["acceptance_criteria"]
        assert data["definition_of_done"] == "Updated done"

    def test_criteria_max_items(self):
        app = FastAPI()
        register(app, {"todos": [], "tick_metrics": {}, "quality_gate": {}})
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/todos",
            json={
                "title": "Many criteria",
                "acceptance_criteria": [f"criteria_{i}" for i in range(25)],
            },
        )
        assert resp.status_code == 422

    def test_done_max_length(self):
        app = FastAPI()
        register(app, {"todos": [], "tick_metrics": {}, "quality_gate": {}})
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/todos",
            json={
                "title": "Long done",
                "definition_of_done": "x" * 5000,
            },
        )
        assert resp.status_code == 422

    def test_criteria_persisted_in_db(self):
        app = FastAPI()
        register(app, {"todos": [], "tick_metrics": {}, "quality_gate": {}})

        fake_todo = MagicMock()
        fake_todo.todo_id = "TODO-DBTEST01"
        fake_todo.title = "DB test"
        fake_todo.description = ""
        fake_todo.queue = "core"
        fake_todo.priority = 1
        fake_todo.work_type = "code"
        fake_todo.status = "queued"
        fake_todo.project_id = None
        fake_todo.version = 1
        fake_todo.created_at = None
        fake_todo.acceptance_criteria = json.dumps(["Criteria A", "Criteria B"])
        fake_todo.definition_of_done = "All tests pass"
        fake_todo.scheduled_at = None
        fake_todo.cron = None
        fake_todo.schedule_timezone = "UTC"
        fake_todo.next_run_at = None
        fake_todo.last_run_at = None
        fake_todo.run_count = 0
        fake_todo.max_runs = None
        fake_todo.schedule_paused = False

        repo = MagicMock()
        repo.create = AsyncMock(return_value=fake_todo)

        @asynccontextmanager
        async def factory() -> Any:
            s = AsyncMock()
            s.commit = AsyncMock()
            yield s

        app.state._session_factory = factory

        with patch.object(todos_mod, "TodoRepository", return_value=repo):
            client = TestClient(app)
            resp = client.post(
                "/api/todos",
                json={
                    "title": "DB test",
                    "acceptance_criteria": ["Criteria A", "Criteria B"],
                    "definition_of_done": "All tests pass",
                },
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["acceptance_criteria"] == '["Criteria A", "Criteria B"]'
        assert data["definition_of_done"] == "All tests pass"
