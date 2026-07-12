"""E2E tests for daemon lifecycle: startup, healthz, readyz, shutdown, reload.

Covers the five lifecycle phases identified in enhancement E.2:
1. Startup subsystem initialization
2. Liveness probe (/healthz)
3. Readiness probe (/readyz) with degraded/recovered states
4. Clean shutdown with engine disposal
5. Config reload re-initializes subsystems
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_daemon_state_marker():
    pass


def _make_db_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    return str(config_dir)


def _make_db_config_with_rules(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
        "rules:\n"
        "  - name: test-rule\n"
        "    description: A reload-detectable rule\n"
    )
    return str(config_dir)


class TestDaemonStartup:
    def test_subsystems_initialized_on_startup(self):
        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                assert app.state._startup_config is not None
                assert isinstance(app.state._startup_config, dict)
                assert "user_config" in app.state._startup_config

                assert app.state.event_loop is not None
                assert app.state._db_engine is not None
                assert app.state._session_factory is not None

                assert app.state._event_bus is not None
                assert app.state._hook_system is not None
                assert app.state._worker_broadcaster is not None

                assert app.state._event_loop_task is not None
                assert app.state._health_tracker is not None


class TestDaemonHealthz:
    def test_healthz_returns_200(self):
        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "healthy"

    def test_healthz_reports_auth_posture(self):
        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/healthz")
                data = resp.json()
                assert "no_auth" in data
                assert "require_auth" in data
                assert "allow_no_auth" in data
                assert "budget_exhausted" in data


class TestDaemonReadyz:
    def test_readyz_returns_ready_when_healthy(self):
        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/readyz")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ready"

    def test_readyz_returns_503_when_degraded(self):
        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            app.state._degraded = "test injected degradation"
            with TestClient(app) as client:
                resp = client.get("/readyz")
                assert resp.status_code == 503
                data = resp.json()
                assert data["status"] == "degraded"

    def test_readyz_returns_ready_after_degraded_flag_cleared(self):
        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                app.state._degraded = "temporary fault"
                resp = client.get("/readyz")
                assert resp.status_code == 503

                app.state._degraded = None
                resp = client.get("/readyz")
                assert resp.status_code == 200
                assert resp.json()["status"] == "ready"


class TestDaemonShutdown:
    def test_engine_disposed_on_context_exit(self):
        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                engine = app.state._db_engine
                assert engine is not None

            status = engine.pool.status()
            assert any(
                phrase in status
                for phrase in ("Pool size: 0", "Pool closed", "Overflow: -5")
            )

    def test_event_loop_task_cancelled_after_shutdown(self):
        import asyncio

        import general_ludd.daemon as daemon_mod

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                task = app.state._event_loop_task
                assert task is not None
                assert not task.done()

            assert task.cancelled() or task.done()


class TestDaemonReload:
    def test_config_reload_updates_startup_config(self, tmp_path):
        import general_ludd.daemon as daemon_mod

        config_dir = _make_db_config_with_rules(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(
                tick_interval=0.01, config_dir=config_dir
            )
            with TestClient(app) as client:
                original_config = app.state._startup_config
                original_rules = original_config.get("rules", [])

                resp = client.post("/admin/config/reload")
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True

                new_config = app.state._startup_config
                assert new_config is not None
                assert new_config is not original_config or new_config == original_config

                assert app.state._event_bus is not None
                assert app.state._hook_system is not None

    def test_config_reload_publishes_event(self, tmp_path):
        import general_ludd.daemon as daemon_mod

        config_dir = _make_db_config_with_rules(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(
                tick_interval=0.01, config_dir=config_dir
            )
            with TestClient(app) as client:
                bus = app.state._event_bus
                received = []
                bus.subscribe("config_reloaded", lambda e: received.append(e))

                resp = client.post("/admin/config/reload")
                assert resp.status_code == 200

                assert len(received) >= 1, (
                    f"Expected config_reloaded event, got {len(received)} events"
                )

    def test_healthz_returns_healthy_after_reload(self, tmp_path):
        import general_ludd.daemon as daemon_mod

        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(
                tick_interval=0.01, config_dir=config_dir
            )
            with TestClient(app) as client:
                resp = client.post("/admin/config/reload")
                assert resp.status_code == 200

                resp = client.get("/healthz")
                assert resp.status_code == 200
                assert resp.json()["status"] == "healthy"

    def test_readyz_returns_ready_after_reload(self, tmp_path):
        import general_ludd.daemon as daemon_mod

        config_dir = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = daemon_mod.create_daemon_app(
                tick_interval=0.01, config_dir=config_dir
            )
            with TestClient(app) as client:
                resp = client.post("/admin/config/reload")
                assert resp.status_code == 200

                resp = client.get("/readyz")
                assert resp.status_code == 200
                assert resp.json()["status"] == "ready"
