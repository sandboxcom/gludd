"""Branch coverage for general_ludd/routers/todos.py.

Covers: db_engine None/set, filestore_available True/False, config_file_count
branches, in-memory todo CRUD, filters, limit clamping, 404, admin endpoints,
log-level valid/invalid, project_id guard, deployments.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.todos import register


def _make_app(extra_state: dict | None = None) -> tuple[FastAPI, dict, TestClient]:
    app = FastAPI()
    state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    if extra_state:
        state.update(extra_state)
    register(app, state)
    client = TestClient(app)
    return app, state, client


# ---------------------------------------------------------------------------
# /api/status — db_engine branch
# ---------------------------------------------------------------------------

class TestStatusDbEngine:
    def test_db_engine_none_returns_sqlite_string(self):
        """_db_engine is None → db_engine field is 'None' string, db_url is 'sqlite'."""
        _app, _state, client = _make_app()
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError):
            resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_engine"] == "None"
        assert data["db_url"] == "sqlite"

    def test_db_engine_set_renders_url(self):
        """_db_engine is set → db_engine field contains rendered URL."""
        app, _state, client = _make_app()
        mock_engine = MagicMock()
        mock_engine.url.render_as_string.return_value = "postgresql://user:***@host/db"
        mock_engine.url.__str__ = lambda self: "postgresql://user:secret@host/db"
        app.state._db_engine = mock_engine

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError):
            resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        # db_engine comes from str(app.state._db_engine) — the mock's __str__
        assert data["db_engine"] is not None
        # db_url goes through make_url(str(url)).render_as_string; just verify it's a string
        assert isinstance(data["db_url"], str)


# ---------------------------------------------------------------------------
# /api/status — filestore_available branch
# ---------------------------------------------------------------------------

class TestStatusFilestoreAvailable:
    def test_filestore_available_true_when_root_path_set(self):
        """FileStore succeeds + root_path truthy → filestore_available = True."""
        _app, _state, client = _make_app()

        mock_store = MagicMock()
        mock_store.root_path = "/some/path"
        mock_boot = MagicMock()
        mock_boot.list_binaries_with_versions.return_value = [
            {"binary_name": "rg", "version": "13.0"}
        ]
        mock_boot.get_known_versions.return_value = {"rg": "13.0"}

        with patch("general_ludd.routers.todos.FileStore", return_value=mock_store), \
             patch("general_ludd.routers.todos.BinaryBootstrapper", return_value=mock_boot):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["filestore_available"] is True
        assert data["filestore_binaries"] == [{"name": "rg", "version": "13.0"}]
        assert data["binary_versions"] == {"rg": "13.0"}

    def test_filestore_available_false_when_root_path_empty(self):
        """FileStore succeeds but root_path is falsy → filestore_available = False."""
        _app, _state, client = _make_app()

        mock_store = MagicMock()
        mock_store.root_path = ""
        mock_boot = MagicMock()
        mock_boot.list_binaries_with_versions.return_value = []
        mock_boot.get_known_versions.return_value = {}

        with patch("general_ludd.routers.todos.FileStore", return_value=mock_store), \
             patch("general_ludd.routers.todos.BinaryBootstrapper", return_value=mock_boot):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["filestore_available"] is False


# ---------------------------------------------------------------------------
# /api/status — config_file_count branches
# ---------------------------------------------------------------------------

class TestStatusConfigFileCount:
    def test_config_dir_none_count_zero(self):
        """No _config_dir on app.state → config_file_count = 0."""
        _app, _state, client = _make_app()
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError):
            resp = client.get("/api/status")
        assert resp.json()["config_file_count"] == 0

    def test_config_dir_not_a_dir_count_zero(self):
        """_config_dir set but isdir returns False → config_file_count = 0."""
        app, _state, client = _make_app()
        app.state._config_dir = "/nonexistent/path"
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError), \
             patch("general_ludd.routers.todos.os.path.isdir", return_value=False):
            resp = client.get("/api/status")
        assert resp.json()["config_file_count"] == 0

    def test_config_dir_with_yaml_files(self):
        """_config_dir is a real dir with .yml and .yaml files → correct count."""
        app, _state, client = _make_app()
        app.state._config_dir = "/fake/config"
        files = ["a.yml", "b.yaml", "c.txt", "d.yml"]
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError), \
             patch("general_ludd.routers.todos.os.path.isdir", return_value=True), \
             patch("general_ludd.routers.todos.os.listdir", return_value=files):
            resp = client.get("/api/status")
        assert resp.json()["config_file_count"] == 3  # a.yml, b.yaml, d.yml

    def test_config_dir_no_yaml_files_count_zero(self):
        """_config_dir exists but contains no yaml files → config_file_count = 0."""
        app, _state, client = _make_app()
        app.state._config_dir = "/fake/config"
        files = ["README.md", "notes.txt"]
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError), \
             patch("general_ludd.routers.todos.os.path.isdir", return_value=True), \
             patch("general_ludd.routers.todos.os.listdir", return_value=files):
            resp = client.get("/api/status")
        assert resp.json()["config_file_count"] == 0


# ---------------------------------------------------------------------------
# POST /api/todos — in-memory path + project_id guard
# ---------------------------------------------------------------------------

class TestAddTodo:
    def test_add_todo_no_factory_appends_to_state(self):
        """No session factory → todo appended to _daemon_state['todos']."""
        _app, state, client = _make_app()
        resp = client.post("/api/todos", json={"title": "My task"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "My task"
        assert data["status"] == "queued"
        assert len(state["todos"]) == 1

    def test_add_todo_default_fields(self):
        """Defaults: queue=core, priority=medium, work_type=code."""
        _app, state, client = _make_app()
        resp = client.post("/api/todos", json={"title": "Defaults check"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["queue"] == "core"
        assert data["work_type"] == "code"

    def test_add_todo_project_id_guard_unknown_id_rejected(self):
        """project_id set + pm has active projects + unknown id → 422."""
        app, _state, client = _make_app()
        active = MagicMock()
        active.project_id = "proj-known"
        pm = MagicMock()
        pm.list_active.return_value = [active]
        app.state._project_manager = pm

        resp = client.post("/api/todos", json={"title": "T", "project_id": "proj-unknown"})
        assert resp.status_code == 422
        assert "Unknown project_id" in resp.json()["detail"]

    def test_add_todo_project_id_guard_empty_active_allowed(self):
        """project_id set + pm has NO active projects → allowed through."""
        app, _state, client = _make_app()
        pm = MagicMock()
        pm.list_active.return_value = []
        app.state._project_manager = pm

        resp = client.post("/api/todos", json={"title": "T", "project_id": "any-id"})
        assert resp.status_code == 201

    def test_add_todo_project_id_guard_no_pm_allowed(self):
        """project_id set + no _project_manager on state → allowed through."""
        _app, _state, client = _make_app()
        resp = client.post("/api/todos", json={"title": "T", "project_id": "any-id"})
        assert resp.status_code == 201

    def test_add_todo_project_id_guard_known_id_allowed(self):
        """project_id matches an active project → allowed through."""
        app, _state, client = _make_app()
        active = MagicMock()
        active.project_id = "proj-known"
        pm = MagicMock()
        pm.list_active.return_value = [active]
        app.state._project_manager = pm

        resp = client.post("/api/todos", json={"title": "T", "project_id": "proj-known"})
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /api/todos — filters and limit
# ---------------------------------------------------------------------------

class TestListTodos:
    def _seed(self, client: TestClient, todos: list[dict]) -> None:
        for t in todos:
            client.post("/api/todos", json=t)

    def test_list_todos_no_filter(self):
        _app, _state, client = _make_app()
        self._seed(client, [{"title": "A"}, {"title": "B"}])
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_todos_filter_queue(self):
        _app, _state, client = _make_app()
        self._seed(client, [
            {"title": "A", "queue": "core"},
            {"title": "B", "queue": "research"},
        ])
        resp = client.get("/api/todos?queue=core")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["queue"] == "core"

    def test_list_todos_filter_status(self):
        _app, _state, client = _make_app()
        self._seed(client, [{"title": "A"}, {"title": "B"}])
        resp = client.get("/api/todos?status=queued")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        resp2 = client.get("/api/todos?status=done")
        assert resp2.status_code == 200
        assert len(resp2.json()) == 0

    def test_list_todos_filter_project_id(self):
        _app, state, client = _make_app()
        client.post("/api/todos", json={"title": "A", "project_id": "proj-1"})
        client.post("/api/todos", json={"title": "B"})
        resp = client.get("/api/todos?project_id=proj-1")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_todos_limit_clamped_to_max(self):
        _app, _state, client = _make_app()
        # Seed 3 todos; limit=1000 should be clamped to 500
        for i in range(3):
            client.post("/api/todos", json={"title": f"T{i}"})
        resp = client.get("/api/todos?limit=1000")
        # All 3 returned (under 500); just verify clamping didn't error
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_todos_limit_clamped_to_min(self):
        _app, _state, client = _make_app()
        for i in range(3):
            client.post("/api/todos", json={"title": f"T{i}"})
        resp = client.get("/api/todos?limit=0")
        assert resp.status_code == 200
        # limit clamped to 1
        assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# GET /api/todos/{todo_id} — 404
# ---------------------------------------------------------------------------

class TestGetTodo:
    def test_get_todo_found(self):
        _app, _state, client = _make_app()
        create_resp = client.post("/api/todos", json={"title": "Find me"})
        todo_id = create_resp.json()["todo_id"]
        resp = client.get(f"/api/todos/{todo_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Find me"

    def test_get_todo_not_found(self):
        _app, _state, client = _make_app()
        resp = client.get("/api/todos/TODO-NOPE0000")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Todo not found"


# ---------------------------------------------------------------------------
# GET /admin/todos — filters
# ---------------------------------------------------------------------------

class TestAdminListTodos:
    def test_admin_list_todos_no_filter(self):
        _app, _state, client = _make_app()
        client.post("/api/todos", json={"title": "X"})
        resp = client.get("/admin/todos")
        assert resp.status_code == 200
        data = resp.json()
        assert "todos" in data
        assert data["count"] == 1

    def test_admin_list_todos_filter_status(self):
        _app, _state, client = _make_app()
        client.post("/api/todos", json={"title": "X"})
        resp = client.get("/admin/todos?status=queued")
        assert resp.json()["count"] == 1

        resp2 = client.get("/admin/todos?status=done")
        assert resp2.json()["count"] == 0

    def test_admin_list_todos_filter_project_id(self):
        _app, _state, client = _make_app()
        client.post("/api/todos", json={"title": "X", "project_id": "p1"})
        client.post("/api/todos", json={"title": "Y"})
        resp = client.get("/admin/todos?project_id=p1")
        assert resp.json()["count"] == 1


# ---------------------------------------------------------------------------
# POST /admin/log-level
# ---------------------------------------------------------------------------

class TestAdminLogLevel:
    def test_valid_level(self):
        _app, _state, client = _make_app()
        resp = client.post("/admin/log-level", json={"level": "DEBUG"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "level": "DEBUG"}

    def test_invalid_level(self):
        _app, _state, client = _make_app()
        resp = client.post("/admin/log-level", json={"level": "VERBOSE"})
        assert resp.status_code == 422
        assert "Invalid log level" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/deployments
# ---------------------------------------------------------------------------

class TestApiDeployments:
    def test_deployments_none_returns_empty(self):
        _app, _state, client = _make_app()
        # No _compute_deployments on state
        resp = client.get("/api/deployments")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_deployments_with_entries(self):
        app, _state, client = _make_app()
        inst = MagicMock()
        inst.instance_id = "i-abc"
        inst.status = "running"
        app.state._compute_deployments = {"i-abc": inst}
        resp = client.get("/api/deployments")
        assert resp.status_code == 200
        assert resp.json() == [{"instance_id": "i-abc", "status": "running"}]


# ---------------------------------------------------------------------------
# /api/status — quality_gate default when empty
# ---------------------------------------------------------------------------

class TestStatusQualityGate:
    def test_quality_gate_default_when_not_set(self):
        """quality_gate empty dict → filled with not_run defaults."""
        _app, _state, client = _make_app()
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError):
            resp = client.get("/api/status")
        qg = resp.json()["quality_gate"]
        assert qg["overall"] == "not_run"
        assert qg["passed_count"] == 0

    def test_quality_gate_preserved_when_set(self):
        """quality_gate already set → returned as-is."""
        _app, state, client = _make_app()
        state["quality_gate"] = {"overall": "pass", "passed_count": 10, "total_count": 10}
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError):
            resp = client.get("/api/status")
        qg = resp.json()["quality_gate"]
        assert qg["overall"] == "pass"


# ---------------------------------------------------------------------------
# /api/status — todos in-memory path contributes to queue_depths
# ---------------------------------------------------------------------------

class TestStatusTodoCounts:
    def test_queue_depths_from_inmemory_todos(self):
        _app, _state, client = _make_app()
        client.post("/api/todos", json={"title": "A", "queue": "core"})
        client.post("/api/todos", json={"title": "B", "queue": "core"})
        client.post("/api/todos", json={"title": "C", "queue": "research"})

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError):
            resp = client.get("/api/status")
        data = resp.json()
        assert data["todos_total"] == 3
        assert data["queue_depths"]["core"] == 2
        assert data["queue_depths"]["research"] == 1
