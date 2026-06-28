"""Security tests for /api/status: credential redaction and filestore availability.

Covers:
1. db_url and db_engine must NOT appear in the /api/status response at all (SEC-8).
2. filestore_available must be False for a configured-but-nonexistent path.
3. filestore_available must be True for a real existing directory.
- P1: config_dir / filestore_root keys absent from public /api/status
- P2: GET /api/todos respects ?limit= query param
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.todos import register


def _make_app(state: dict | None = None) -> tuple:
    app = FastAPI()
    _state: dict = state if state is not None else {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    register(app, _state)
    return app, _state


class TestDbCredentialRedaction:
    """SEC-8: db_url and db_engine must be absent from /api/status (not just masked)."""

    def test_db_url_absent_from_status(self):
        """db_url must not appear in /api/status — field removed entirely (SEC-8)."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        mock_engine = MagicMock()
        mock_engine.url.render_as_string.return_value = "postgresql+psycopg2://user:***@localhost/mydb"
        app.state._db_engine = mock_engine
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        assert resp.status_code == 200
        assert "db_url" not in resp.json(), "SEC-8: db_url must be absent from public /api/status"

    def test_db_engine_absent_from_status(self):
        """db_engine must not appear in /api/status — field removed entirely (SEC-8)."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        mock_engine = MagicMock()
        mock_engine.url.render_as_string.return_value = "postgresql+psycopg2://user:***@localhost/mydb"
        app.state._db_engine = mock_engine
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        assert resp.status_code == 200
        assert "db_engine" not in resp.json(), "SEC-8: db_engine must be absent from public /api/status"


class TestApiStatusDbFieldsNoEngine:
    """SEC-8: db_url and db_engine must be absent regardless of engine presence."""

    def test_db_fields_absent_when_no_engine(self):
        """With no DB engine set, db_url and db_engine must not appear in response."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        client = TestClient(app)
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "db_engine" not in data, "SEC-8: db_engine must be absent even without engine"
        assert "db_url" not in data, "SEC-8: db_url must be absent even without engine"


class TestFilestoreAvailability:
    def test_filestore_available_false_for_nonexistent_path(self, tmp_path):
        """filestore_available must be False when root_path does not exist on disk."""
        nonexistent = str(tmp_path / "does_not_exist")
        assert not os.path.isdir(nonexistent), "Precondition: path must not exist"

        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        client = TestClient(app)

        mock_store = MagicMock()
        mock_store.root_path = nonexistent
        mock_store.list_binaries_with_versions = MagicMock(return_value=[])

        mock_boot = MagicMock()
        mock_boot.list_binaries_with_versions.return_value = []
        mock_boot.get_known_versions.return_value = {}

        with (
            patch("general_ludd.routers.todos.FileStore", return_value=mock_store),
            patch("general_ludd.routers.todos.BinaryBootstrapper", return_value=mock_boot),
        ):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["filestore_available"] is False, (
            f"Expected False for nonexistent path, got: {data['filestore_available']!r}"
        )

    def test_filestore_available_true_for_existing_dir(self, tmp_path):
        """filestore_available must be True when root_path is a real directory."""
        existing_dir = str(tmp_path / "real_store")
        os.makedirs(existing_dir)
        assert os.path.isdir(existing_dir), "Precondition: dir must exist"

        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        client = TestClient(app)

        mock_store = MagicMock()
        mock_store.root_path = existing_dir

        mock_boot = MagicMock()
        mock_boot.list_binaries_with_versions.return_value = []
        mock_boot.get_known_versions.return_value = {}

        with (
            patch("general_ludd.routers.todos.FileStore", return_value=mock_store),
            patch("general_ludd.routers.todos.BinaryBootstrapper", return_value=mock_boot),
        ):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["filestore_available"] is True, (
            f"Expected True for existing dir, got: {data['filestore_available']!r}"
        )


class TestStatusDbUrlRedaction:
    """SEC-8: /api/status must not expose any database connection info."""

    def test_db_url_absent_no_engine(self):
        """When no DB engine is set, db_url must be absent (not 'sqlite')."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        assert resp.status_code == 200
        assert "db_url" not in resp.json(), "SEC-8: db_url must be absent from /api/status"

    def test_db_url_absent_with_postgres_engine(self):
        """Even with a real postgres engine, db_url must be absent from the response."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        mock_engine = MagicMock()
        mock_engine.url.render_as_string.return_value = "postgresql+psycopg2://admin:***@localhost/mydb"
        app.state._db_engine = mock_engine
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        assert resp.status_code == 200
        assert "db_url" not in resp.json(), "SEC-8: db_url must be absent even with engine set"


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
