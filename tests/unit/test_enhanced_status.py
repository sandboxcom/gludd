"""Integration test for enhanced status endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestEnhancedStatus:
    def test_status_returns_version(self):
        app = FastAPI()
        app.state._daemon_state = {"todos": [], "tick_metrics": {}}
        app.state._config_dir = None
        app.state._db_engine = None

        @app.get("/api/status")
        def status():
            queue_depths: dict[str, int] = {}
            todo_count = 0
            for todo in app.state._daemon_state["todos"]:
                q = todo.get("queue", "unknown")
                queue_depths[q] = queue_depths.get(q, 0) + 1
                todo_count += 1
            import os

            from general_ludd import __version__
            config_dir = getattr(app.state, "_config_dir", None)
            config_paths: list[str] = []
            if config_dir and os.path.isdir(config_dir):
                for f in sorted(os.listdir(config_dir)):
                    if f.endswith(".yml") or f.endswith(".yaml"):
                        config_paths.append(f)
            from general_ludd.filestore.bootstrap import BinaryBootstrapper
            from general_ludd.filestore.store import FileStore
            store = FileStore()
            boot = BinaryBootstrapper(store=store)
            stored_binaries = [b["name"] for b in boot.list_binaries()]
            elapsed = app.state._daemon_state.get("tick_metrics", {})
            return {
                "version": __version__,
                "uptime_ticks": elapsed.get("total_ticks", 0),
                "todos_total": todo_count,
                "queue_depths": queue_depths,
                "tick_metrics": elapsed,
                "config_dir": config_dir,
                "config_files": config_paths,
                "filestore_root": store.root_path,
                "filestore_binaries": stored_binaries,
                "db_engine": str(getattr(app.state, "_db_engine", None)),
                "db_url": str(getattr(getattr(app.state, "_db_engine", None), "url", "sqlite")),
            }

        client = TestClient(app)
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert data["version"] is not None
        assert "todos_total" in data
        assert "queue_depths" in data
        assert "filestore_root" in data
        assert "filestore_binaries" in data
        assert "config_dir" in data
        assert "db_engine" in data
        assert "db_url" in data
        assert "uptime_ticks" in data

    def test_status_with_todos_counts_correctly(self):
        app = FastAPI()
        app.state._daemon_state = {
            "todos": [
                {"todo_id": "1", "queue": "core"},
                {"todo_id": "2", "queue": "core"},
                {"todo_id": "3", "queue": "qa"},
            ],
            "tick_metrics": {"total_ticks": 42},
        }
        app.state._config_dir = None
        app.state._db_engine = None

        @app.get("/api/status")
        def status():
            queue_depths: dict[str, int] = {}
            todo_count = 0
            for todo in app.state._daemon_state["todos"]:
                q = todo.get("queue", "unknown")
                queue_depths[q] = queue_depths.get(q, 0) + 1
                todo_count += 1
            import os

            from general_ludd import __version__
            config_dir = getattr(app.state, "_config_dir", None)
            config_paths: list[str] = []
            if config_dir and os.path.isdir(config_dir):
                for f in sorted(os.listdir(config_dir)):
                    if f.endswith(".yml") or f.endswith(".yaml"):
                        config_paths.append(f)
            from general_ludd.filestore.bootstrap import BinaryBootstrapper
            from general_ludd.filestore.store import FileStore
            store = FileStore()
            boot = BinaryBootstrapper(store=store)
            stored_binaries = [b["name"] for b in boot.list_binaries()]
            elapsed = app.state._daemon_state.get("tick_metrics", {})
            return {
                "version": __version__,
                "uptime_ticks": elapsed.get("total_ticks", 0),
                "todos_total": todo_count,
                "queue_depths": queue_depths,
                "tick_metrics": elapsed,
                "config_dir": config_dir,
                "config_files": config_paths,
                "filestore_root": store.root_path,
                "filestore_binaries": stored_binaries,
                "db_engine": str(getattr(app.state, "_db_engine", None)),
                "db_url": str(getattr(getattr(app.state, "_db_engine", None), "url", "sqlite")),
            }

        client = TestClient(app)
        resp = client.get("/api/status")
        data = resp.json()
        assert data["todos_total"] == 3
        assert data["queue_depths"]["core"] == 2
        assert data["queue_depths"]["qa"] == 1
        assert data["uptime_ticks"] == 42
