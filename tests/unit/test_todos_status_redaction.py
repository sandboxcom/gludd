"""Security tests for /api/status: credential redaction and filestore availability.

Covers:
1. db_url field must have password masked as *** (not leaked).
2. db_engine field must also have password masked (uses render_as_string).
3. filestore_available must be False for a configured-but-nonexistent path.
4. filestore_available must be True for a real existing directory.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

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
