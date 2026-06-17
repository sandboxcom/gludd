"""Tests for security fixes in /api/status and GET /api/todos.

Covers:
- P1: db_url credential redaction (password hidden, host-paths absent)
- P1: config_dir / filestore_root keys absent from public /api/status
- P2: GET /api/todos respects ?limit= query param
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.todos import register


def _make_app(state: dict | None = None) -> FastAPI:
    app = FastAPI()
    _state: dict = state if state is not None else {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    register(app, _state)
    return app, _state


class TestStatusDbUrlRedaction:
    """P1: /api/status must never expose raw database passwords."""

    def test_postgres_password_not_in_db_url(self):
        """A postgres engine URL with a password must be rendered password-hidden."""
        from sqlalchemy import create_engine

        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)

        # Build a real SQLAlchemy URL object that carries a password.
        engine = create_engine("postgresql+psycopg2://admin:s3cr3tpassword@localhost/mydb")
        app.state._db_engine = engine

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        db_url = data["db_url"]
        # Password must not appear in clear text
        assert "s3cr3tpassword" not in db_url
        # SQLAlchemy render_as_string(hide_password=True) replaces password with ***
        assert "***" in db_url or db_url.endswith("@localhost/mydb")

    def test_sqlite_default_when_no_engine(self):
        """When no DB engine is set the db_url field falls back to 'sqlite'."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["db_url"] == "sqlite"


class TestStatusPathsAbsent:
    """P1: /api/status must not expose host absolute paths to unauthenticated callers."""

    def test_config_dir_key_absent(self):
        app, _ = _make_app()
        app.state._config_dir = "/etc/secret/config"

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")

        data = resp.json()
        assert "config_dir" not in data, "config_dir must not be in the public response"

    def test_config_files_key_absent(self):
        app, _ = _make_app()

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")

        data = resp.json()
        assert "config_files" not in data, "config_files must not be in the public response"

    def test_filestore_root_key_absent(self):
        app, _ = _make_app()

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")

        data = resp.json()
        assert "filestore_root" not in data, "filestore_root must not be in the public response"

    def test_replacement_keys_present(self):
        """Replacement boolean/count keys must be present instead of raw paths."""
        app, _ = _make_app()

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")

        data = resp.json()
        assert "config_file_count" in data
        assert isinstance(data["config_file_count"], int)
        assert "filestore_available" in data
        assert isinstance(data["filestore_available"], bool)


class TestTodosLimit:
    """P2: GET /api/todos must respect the ?limit= query parameter."""

    def _make_app_with_todos(self, n: int):
        app = FastAPI()
        todos = [
            {
                "todo_id": f"TODO-{i:08X}",
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
        state: dict = {"todos": todos, "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        return app

    def test_default_limit_100(self):
        """Without ?limit, default is 100 even if more todos exist."""
        app = self._make_app_with_todos(200)
        client = TestClient(app)
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        assert len(resp.json()) == 100

    def test_explicit_limit_respected(self):
        app = self._make_app_with_todos(50)
        client = TestClient(app)
        resp = client.get("/api/todos?limit=10")
        assert resp.status_code == 200
        assert len(resp.json()) == 10

    def test_limit_capped_at_500(self):
        """?limit=9999 is silently capped to 500."""
        app = self._make_app_with_todos(600)
        client = TestClient(app)
        resp = client.get("/api/todos?limit=9999")
        assert resp.status_code == 200
        assert len(resp.json()) == 500

    def test_limit_minimum_1(self):
        """?limit=0 is raised to 1."""
        app = self._make_app_with_todos(10)
        client = TestClient(app)
        resp = client.get("/api/todos?limit=0")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_limit_less_than_available(self):
        """When limit < available items, exactly limit items are returned."""
        app = self._make_app_with_todos(20)
        client = TestClient(app)
        resp = client.get("/api/todos?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    def test_limit_greater_than_available(self):
        """When limit > available items, all items are returned."""
        app = self._make_app_with_todos(3)
        client = TestClient(app)
        resp = client.get("/api/todos?limit=100")
        assert resp.status_code == 200
        assert len(resp.json()) == 3
