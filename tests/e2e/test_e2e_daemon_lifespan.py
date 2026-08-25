"""E2E tests for daemon lifespan: startup, healthz, todo CRUD, shutdown.

Covers the daemon lifecycle gap identified in E2E_AUDIT_2026-07-06:
verifies the FastAPI daemon boots cleanly through the ASGI transport,
serves its endpoints, and tears down without leaking resources.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import aiosqlite
import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.schemas.queue import INITIAL_QUEUES


@pytest.fixture(autouse=True)
def _reset_daemon_state() -> None:
    if daemon_mod._daemon_state is None:
        daemon_mod._daemon_state = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


def _make_db_config(tmp_path: Path) -> str:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    return str(config_dir)


def _track_driver_connections(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Return a live list of connections created through the public driver."""
    created_connections: list[Any] = []
    original_connect = aiosqlite.connect

    def tracked_connect(*args: Any, **kwargs: Any) -> Any:
        connection = original_connect(*args, **kwargs)
        created_connections.append(connection)
        return connection

    monkeypatch.setattr(aiosqlite, "connect", tracked_connect)
    return created_connections


class TestDaemonLifespanE2E:
    def test_repeated_daemons_close_every_owned_driver_connection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sequential app instances must not retain prior SQLite owners."""
        created_connections = _track_driver_connections(monkeypatch)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            for index in range(3):
                app_root = tmp_path / f"app-{index}"
                app_root.mkdir()
                config_dir = _make_db_config(app_root)
                app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
                with TestClient(app) as client:
                    assert client.get("/healthz").status_code == 200

        assert created_connections
        assert all(
            connection._connection is None
            for connection in created_connections
        )

    def test_daemon_closes_every_owned_driver_connection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Every SQLite connection acquired by the app closes at shutdown."""
        created_connections = _track_driver_connections(monkeypatch)
        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app) as client:
                assert client.get("/healthz").status_code == 200

        assert created_connections
        assert all(
            connection._connection is None
            for connection in created_connections
        )

    def test_daemon_boots_and_healthz_responds(self) -> None:
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200

    def test_daemon_lifespan_state_initialized(self) -> None:
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                assert app.state.event_loop is not None
                assert app.state._db_engine is not None
                assert app.state._session_factory is not None
                assert app.state._startup_config is not None

    def test_daemon_create_and_list_todo(self, tmp_path: Path) -> None:
        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.post("/api/todos", json={
                    "title": "E2E daemon lifespan todo",
                    "queue": "core",
                    "priority": "high",
                    "work_type": "code",
                })
                assert resp.status_code == 201
                data = resp.json()
                assert data["title"] == "E2E daemon lifespan todo"

                resp = client.get("/api/todos", params={"queue": "core"})
                assert resp.status_code == 200
                todos = resp.json()
                assert len(todos) == 1
                assert todos[0]["title"] == "E2E daemon lifespan todo"

    def test_daemon_get_status_endpoint(self, tmp_path: Path) -> None:
        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.get("/api/status")
                assert resp.status_code == 200
                data = resp.json()
                assert "todos_total" in data
                assert "version" in data

    def test_daemon_queues_seeded_in_db(self, tmp_path: Path) -> None:
        from sqlalchemy import text

        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                engine = app.state._db_engine
                assert engine is not None

                async def _check_queues() -> None:
                    async with engine.begin() as conn:
                        result = await conn.execute(
                            text("SELECT COUNT(*) FROM queues")
                        )
                        count = result.scalar()
                        assert count == len(INITIAL_QUEUES), (
                            f"Expected {len(INITIAL_QUEUES)} queues, got {count}"
                        )

                asyncio.run(_check_queues())

    def test_daemon_engine_disposed_on_shutdown(self) -> None:
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                engine = app.state._db_engine
                assert engine is not None

            status = engine.pool.status()
            assert (
                "Pool size: 0" in status
                or "Pool closed" in status
                or "Overflow: -5" in status
            )

    def test_daemon_multiple_todos_crud(self, tmp_path: Path) -> None:
        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app) as client:
                for i in range(3):
                    resp = client.post("/api/todos", json={
                        "title": f"Todo {i}",
                        "queue": "core",
                        "priority": "medium",
                        "work_type": "code",
                    })
                    assert resp.status_code == 201

                resp = client.get("/api/todos", params={"queue": "core"})
                assert resp.status_code == 200
                todos = resp.json()
                assert len(todos) >= 3

                ids = [t["todo_id"] for t in todos]

                resp = client.get(f"/api/todos/{ids[0]}")
                assert resp.status_code == 200
                assert resp.json()["title"] == "Todo 0"
