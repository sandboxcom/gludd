"""Acceptance tests for structured task spec fields — criteria and definition-of-done.

POST /api/todos should accept acceptance_criteria (list of strings) and
definition_of_done (string), and should reject a todo with an empty title.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
