"""Security tests: /api/status must not leak host paths or db passwords."""
from __future__ import annotations

import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.todos import register


def _make_app(engine=None, config_dir=None) -> FastAPI:
    app = FastAPI()
    state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    register(app, state)
    if engine is not None:
        app.state._db_engine = engine
    if config_dir is not None:
        app.state._config_dir = config_dir
    return app


class TestStatusPathsAbsent:
    def test_path_keys_absent(self):
        app = _make_app()
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert "config_dir" not in data
        assert "config_files" not in data
        assert "filestore_root" not in data

    def test_config_file_count_present(self, tmp_path):
        (tmp_path / "a.yml").write_text("x: 1")
        (tmp_path / "b.yaml").write_text("y: 2")
        (tmp_path / "ignored.txt").write_text("nope")
        app = _make_app(config_dir=str(tmp_path))
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        data = json.loads(resp.text)
        assert "config_file_count" in data
        assert data["config_file_count"] == 2
        assert isinstance(data["config_file_count"], int)

    def test_filestore_available_false_when_fs_errors(self):
        app = _make_app()
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        data = json.loads(resp.text)
        assert "filestore_available" in data
        assert isinstance(data["filestore_available"], bool)
        assert data["filestore_available"] is False


class TestStatusDbUrlRedaction:
    def test_password_not_in_db_url(self):
        from unittest.mock import MagicMock

        from sqlalchemy.engine import make_url

        url = make_url("postgresql+psycopg://admin:s3cr3tpassword@localhost/mydb")
        mock_engine = MagicMock()
        mock_engine.url = url

        app = _make_app(engine=mock_engine)
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        data = json.loads(resp.text)
        db_url = data.get("db_url", "")
        assert "s3cr3tpassword" not in db_url
        assert ("***" in db_url) or (db_url.endswith("@localhost/mydb"))

    def test_sqlite_default_when_no_engine(self):
        app = _make_app()
        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("no fs")):
            client = TestClient(app)
            resp = client.get("/api/status")
        data = json.loads(resp.text)
        assert data.get("db_url") == "sqlite"
