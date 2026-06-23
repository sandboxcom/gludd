"""Security tests for /api/status: credential redaction and filestore availability.

Covers:
1. db_url field must have password masked as *** (not leaked).
2. db_engine field must also have password masked (uses render_as_string).
3. filestore_available must be False for a configured-but-nonexistent path.
4. filestore_available must be True for a real existing directory.
- P1: config_dir / filestore_root keys absent from public /api/status
- P2: GET /api/todos respects ?limit= query param
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.todos import register

_PASSWORD = "s3cr3tP@ssword"  # pragma: allowlist secret


def _make_app_with_engine(password: str) -> tuple[FastAPI, TestClient]:
    """Return an app + client whose _db_engine has a URL with the given password."""
    app = FastAPI()
    state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    register(app, state)

    # Build a mock engine whose .url behaves like a SQLAlchemy URL.
    mock_url = MagicMock()
    # render_as_string(hide_password=True) should mask the password with ***
    mock_url.render_as_string.return_value = (
        "postgresql+psycopg2://user:***@localhost/mydb"
    )
    # str(url) would reveal the password (simulate SQLAlchemy behaviour)
    mock_url.__str__ = MagicMock(
        return_value=f"postgresql+psycopg2://user:{password}@localhost/mydb"
    )

    mock_engine = MagicMock()
    mock_engine.url = mock_url

    app.state._db_engine = mock_engine
    client = TestClient(app)
    return app, client


def _make_app(state: dict | None = None) -> tuple:
    app = FastAPI()
    _state: dict = state if state is not None else {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    register(app, _state)
    return app, _state


class TestDbCredentialRedaction:
    def test_db_url_exact_masked_form(self):
        """db_url field must render with *** in place of the password."""
        _app, client = _make_app_with_engine(_PASSWORD)

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()

        db_url = data["db_url"]
        # The exact masked form must contain *** where the password was
        assert "***" in db_url, f"Expected *** in db_url but got: {db_url!r}"
        # The raw password must NOT appear anywhere in db_url
        assert _PASSWORD not in db_url, (
            f"Password leaked in db_url: {db_url!r}"
        )

    def test_db_engine_password_not_present(self):
        """db_engine field must use render_as_string(hide_password=True), not str(engine)."""
        _app, client = _make_app_with_engine(_PASSWORD)

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()

        db_engine = data["db_engine"]
        assert _PASSWORD not in db_engine, (
            f"Password leaked in db_engine field: {db_engine!r}"
        )
        # Confirm the field has the masked form (*** present)
        assert "***" in db_engine, (
            f"Expected *** mask in db_engine but got: {db_engine!r}"
        )

    def test_db_engine_calls_render_as_string_with_hide_password(self):
        """Verify render_as_string is called with hide_password=True (not str())."""
        _app, client = _make_app_with_engine(_PASSWORD)

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        # Retrieve the mock engine from app.state to inspect calls
        mock_engine = _app.state._db_engine
        # Both db_engine and db_url call render_as_string(hide_password=True)
        mock_engine.url.render_as_string.assert_called_with(hide_password=True)
        assert mock_engine.url.render_as_string.call_count >= 1


class TestApiStatusDbFieldsNoEngine:
    def test_db_engine_is_none_string_when_no_engine(self):
        """db_engine must be 'None' (str) when no engine is attached."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        client = TestClient(app)
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_engine"] == "None", f"Expected 'None', got {data['db_engine']!r}"

    def test_db_url_is_sqlite_when_no_engine(self):
        """db_url must be 'sqlite' when no engine is attached."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        client = TestClient(app)
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_url"] == "sqlite", f"Expected 'sqlite', got {data['db_url']!r}"

    def test_db_engine_and_db_url_differ_when_no_engine(self):
        """The two keys must have DIFFERENT values when no engine — collapsed bug returns same."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
        register(app, state)
        client = TestClient(app)
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            resp = client.get("/api/status")
        data = resp.json()
        assert data["db_engine"] != data["db_url"], (
            f"db_engine and db_url must differ when no engine, "
            f"but both are {data['db_engine']!r}"
        )


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
    """P1: /api/status must never expose raw database passwords."""

    def test_postgres_password_not_in_db_url(self):
        """A postgres engine URL with a password must be rendered password-hidden."""
        pytest.importorskip("psycopg2")
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
