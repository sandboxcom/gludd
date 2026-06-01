"""End-to-end tests for multi-project isolation.

Simulates two projects running concurrently through the daemon,
verifying complete data, filesystem, and operational isolation.
Each test uses unique project IDs to avoid state leakage from
the shared global _daemon_state.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app


def _unique_project_id() -> str:
    return f"test-proj-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def client():
    app = create_daemon_app(tick_interval=999.0)
    return TestClient(app)


class TestMultiProjectDaemonE2E:
    def test_add_todos_to_different_projects(self, client):
        alpha = _unique_project_id()
        beta = _unique_project_id()

        resp_alpha = client.post(
            "/api/todos",
            json={
                "title": "Alpha task",
                "queue": "core",
                "project_id": alpha,
            },
        )
        assert resp_alpha.status_code == 201
        assert resp_alpha.json()["project_id"] == alpha

        resp_beta = client.post(
            "/api/todos",
            json={
                "title": "Beta task",
                "queue": "core",
                "project_id": beta,
            },
        )
        assert resp_beta.status_code == 201
        assert resp_beta.json()["project_id"] == beta

    def test_list_todos_filtered_by_project(self, client):
        alpha = _unique_project_id()
        beta = _unique_project_id()

        client.post("/api/todos", json={"title": "Alpha 1", "project_id": alpha})
        client.post("/api/todos", json={"title": "Alpha 2", "project_id": alpha})
        client.post("/api/todos", json={"title": "Beta 1", "project_id": beta})

        alpha_todos = client.get("/api/todos", params={"project_id": alpha}).json()
        assert len(alpha_todos) == 2
        assert all(t["project_id"] == alpha for t in alpha_todos)

        beta_todos = client.get("/api/todos", params={"project_id": beta}).json()
        assert len(beta_todos) == 1
        assert beta_todos[0]["project_id"] == beta

    def test_list_todos_no_filter_returns_all(self, client):
        alpha = _unique_project_id()
        beta = _unique_project_id()

        client.post("/api/todos", json={"title": "A", "project_id": alpha})
        client.post("/api/todos", json={"title": "B", "project_id": beta})

        all_todos = client.get("/api/todos").json()
        our_ids = {alpha, beta}
        ours = [t for t in all_todos if t["project_id"] in our_ids]
        assert len(ours) == 2

    def test_todo_without_project(self, client):
        resp = client.post("/api/todos", json={"title": "Unassigned task"})
        assert resp.status_code == 201
        assert resp.json()["project_id"] is None

    def test_cross_project_todo_isolation(self, client):
        alpha = _unique_project_id()
        beta = _unique_project_id()

        client.post(
            "/api/todos",
            json={
                "title": "Alpha secret task",
                "description": "secret-alpha",
                "project_id": alpha,
            },
        )
        client.post(
            "/api/todos",
            json={
                "title": "Beta secret task",
                "description": "secret-beta",
                "project_id": beta,
            },
        )

        alpha_todos = client.get("/api/todos", params={"project_id": alpha}).json()
        assert len(alpha_todos) == 1
        assert "Alpha" in alpha_todos[0]["title"]

        beta_todos = client.get("/api/todos", params={"project_id": beta}).json()
        assert len(beta_todos) == 1
        assert "Beta" in beta_todos[0]["title"]

    def test_queue_filter_with_project(self, client):
        alpha = _unique_project_id()
        beta = _unique_project_id()

        client.post(
            "/api/todos",
            json={"title": "Alpha core", "queue": "core", "project_id": alpha},
        )
        client.post(
            "/api/todos",
            json={"title": "Alpha infra", "queue": "infra", "project_id": alpha},
        )
        client.post(
            "/api/todos",
            json={"title": "Beta core", "queue": "core", "project_id": beta},
        )

        alpha_core = client.get(
            "/api/todos", params={"queue": "core", "project_id": alpha}
        ).json()
        assert len(alpha_core) == 1
        assert alpha_core[0]["title"] == "Alpha core"

    def test_get_todo_by_id(self, client):
        resp = client.post(
            "/api/todos",
            json={"title": "Specific todo", "project_id": _unique_project_id()},
        )
        todo_id = resp.json()["todo_id"]
        get_resp = client.get(f"/api/todos/{todo_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["todo_id"] == todo_id


class TestMultiProjectFullFlowE2E:
    def test_full_lifecycle_two_projects(self, client):
        alpha = _unique_project_id()
        beta = _unique_project_id()

        client.post(
            "/admin/projects",
            json={"name": "webapp", "weight": 60.0},
        )
        client.post(
            "/admin/projects",
            json={"name": "api", "weight": 40.0},
        )

        client.post(
            "/api/todos",
            json={
                "title": "Build homepage",
                "queue": "core",
                "project_id": alpha,
                "work_type": "code",
            },
        )
        client.post(
            "/api/todos",
            json={
                "title": "Build API endpoint",
                "queue": "core",
                "project_id": beta,
                "work_type": "code",
            },
        )

        alpha_todos = client.get("/api/todos", params={"project_id": alpha}).json()
        assert len(alpha_todos) == 1
        assert alpha_todos[0]["title"] == "Build homepage"

        beta_todos = client.get("/api/todos", params={"project_id": beta}).json()
        assert len(beta_todos) == 1
        assert beta_todos[0]["title"] == "Build API endpoint"

        projects = client.get("/admin/projects").json()
        assert projects["active_projects"] == 2
