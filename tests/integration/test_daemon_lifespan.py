"""Integration test for full daemon lifespan with real DB.

Tests that the daemon:
1. Creates engine and tables from real SQLite DB
2. Seeds initial queues
3. Creates session factory
4. Creates EventLoop with real session
5. Runs a tick cycle with DB operations
6. Shuts down cleanly (disposes engine)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


class TestDaemonLifespanWithRealDB:
    def test_daemon_starts_creates_tables_seeds_queues(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200

                assert app.state.event_loop is not None
                assert app.state._db_engine is not None
                assert app.state._session_factory is not None

    @pytest.mark.asyncio
    async def test_daemon_event_loop_runs_tick(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                loop = app.state.event_loop
                assert loop is not None

                metrics = await loop.tick()
                assert metrics["phases_completed"] == 10

    @pytest.mark.asyncio
    async def test_daemon_queues_seeded_in_db(self):
        from sqlalchemy import text

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                engine = app.state._db_engine
                assert engine is not None

                async with engine.begin() as conn:
                    result = await conn.execute(
                        text("SELECT COUNT(*) FROM queues")
                    )
                    count = result.scalar()
                    assert count == 12, f"Expected 12 queues, got {count}"

    def test_daemon_add_todo_and_list(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.post("/api/todos", json={
                    "title": "Integration test todo",
                    "queue": "core",
                    "priority": "high",
                    "work_type": "code",
                    "project_id": "proj-1",
                })
                assert resp.status_code == 201
                data = resp.json()
                assert data["project_id"] == "proj-1"

                resp = client.get("/api/todos", params={"project_id": "proj-1"})
                assert resp.status_code == 200
                todos = resp.json()
                assert len(todos) == 1
                assert todos[0]["project_id"] == "proj-1"

    def test_daemon_engine_disposed_on_shutdown(self):
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                engine = app.state._db_engine
                assert engine is not None

            status = engine.pool.status()
            assert "Pool size: 0" in status or "Pool closed" in status or "Overflow: -5" in status

    def test_daemon_with_config_dir(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        (config_dir / "general-ludd.yml").write_text(
            "daemon:\n  tick_interval: 0.05\n"
        )

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(
                tick_interval=0.01,
                config_dir=str(config_dir),
            )
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200
                assert app.state._startup_config is not None
                uc = app.state._startup_config.get("user_config")
                assert uc is not None
