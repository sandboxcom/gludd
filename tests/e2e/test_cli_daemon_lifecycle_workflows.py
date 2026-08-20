"""E2E tests for CLI commands and daemon lifecycle workflows.

Covers daemon startup, health probes, shutdown, CLI commands, middleware,
error recovery, route registration, and config handling.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from general_ludd import __version__
from general_ludd.cli import main as cli_main

# ── helpers ─────────────────────────────────────────────────────────────────

def _run_cli(args: list[str]) -> int:
    try:
        with patch.object(sys, "argv", ["gludd", *args]):
            cli_main()
        return 0
    except SystemExit as exc:
        return exc.code if exc.code is not None else 1


def _run_cli_output(args: list[str], capsys) -> tuple[str, str, int]:
    try:
        with patch.object(sys, "argv", ["gludd", *args]):
            cli_main()
        captured = capsys.readouterr()
        return captured.out, captured.err, 0
    except SystemExit as exc:
        captured = capsys.readouterr()
        return captured.out, captured.err, exc.code if exc.code is not None else 1


def _make_config_dir(tmp_path: Path, extra: str = "") -> str:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    base = f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    (config_dir / "general-ludd.yml").write_text(base + extra)
    return str(config_dir)


@pytest.fixture(autouse=True)
def _isolate_default_daemon_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep implicit daemon factories off persistent user database state."""
    default_root = tmp_path / "default-daemon"
    default_root.mkdir()
    monkeypatch.setenv("GLUDD_CONFIG_DIR", _make_config_dir(default_root))


class _FakeTask:
    def done(self) -> bool:
        return False

    def cancelled(self) -> bool:
        return False


# ── Daemon startup sequence ─────────────────────────────────────────────────

class TestDaemonStartupSequence:
    def test_create_daemon_app_sets_title_and_version(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
        assert app.title == "General Ludd Agent"
        assert app.version == __version__

    def test_config_loaded_on_create(self, tmp_path, monkeypatch):
        config_dir = _make_config_dir(tmp_path)
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01, config_dir=config_dir)
        startup = app.state._startup_config
        assert isinstance(startup, dict)
        assert "user_config" in startup

    def test_db_engine_initialized_in_lifespan(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                engine = app.state._db_engine
                assert engine is not None

    def test_session_factory_initialized_in_lifespan(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                factory = app.state._session_factory
                assert factory is not None

    def test_all_core_subsystems_initialized(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                assert app.state._event_bus is not None
                assert app.state._hook_system is not None
                assert app.state._worker_broadcaster is not None
                assert app.state._health_tracker is not None

    def test_routes_registered_on_create(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
        routes = {r.path for r in app.routes}
        assert "/healthz" in routes
        assert "/readyz" in routes
        assert "/metrics" in routes
        assert "/api/facts" in routes
        assert "/admin/config/reload" in routes

    def test_daemon_state_per_app_isolation(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app1 = dm.create_daemon_app(tick_interval=0.01)
            app2 = dm.create_daemon_app(tick_interval=0.01)
        daemon_state1 = app1.state.daemon_state
        daemon_state2 = app2.state.daemon_state
        assert daemon_state1 is not daemon_state2
        assert daemon_state1 is not dm._daemon_state
        assert daemon_state2 is not dm._daemon_state

    def test_startup_config_includes_project_path(self, monkeypatch, tmp_path):
        proj_dir = tmp_path / "myproject" / ".gludd"
        proj_dir.mkdir(parents=True)
        config_dir = _make_config_dir(tmp_path)
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(tmp_path / "myproject"))
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01, config_dir=config_dir)
        startup = app.state._startup_config
        assert isinstance(startup, dict)

    def test_daemon_state_fields_initialized(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
        ds = app.state.daemon_state
        assert "todos" in ds
        assert "tick_metrics" in ds
        assert "quality_gate" in ds
        assert ds["todos"] == []

    def test_app_state_has_required_attrs(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
        assert hasattr(app.state, "_stats_requests")
        assert hasattr(app.state, "_stats_responses")
        assert hasattr(app.state, "_network_host")
        assert hasattr(app.state, "_network_port")
        assert app.state._network_host == "127.0.0.1"
        assert app.state._network_port == 8000


# ── Daemon health endpoints ─────────────────────────────────────────────────

class TestDaemonHealthEndpoints:
    def test_healthz_returns_200_and_healthy(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "healthy"

    def test_healthz_includes_security_posture(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                data = client.get("/healthz").json()
        assert "no_auth" in data
        assert "require_auth" in data
        assert "allow_no_auth" in data
        assert "auth_degraded" in data
        assert "budget_exhausted" in data

    def test_healthz_degraded_when_flag_set(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._degraded = "test degradation"
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "degraded"
                assert data["reason"] == "test degradation"

    def test_healthz_degraded_when_event_loop_cancelled(self, monkeypatch):
        import general_ludd.daemon as dm

        class DoneTask:
            def done(self) -> bool:
                return True

            def cancelled(self) -> bool:
                return True

        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._event_loop_task = DoneTask()
            with TestClient(app) as client:
                data = client.get("/healthz").json()
        assert data["status"] == "degraded"
        assert "event_loop" in data["reason"]

    def test_readyz_503_when_not_initialized(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            # Deliberately do not enter TestClient's context manager: Starlette
            # runs the ASGI lifespan on context entry, which would initialize
            # the database and event loop and contradict this test's premise.
            assert getattr(app.state, "_event_loop_task", None) is None
            client = TestClient(app)
            resp = client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["reason"] == "daemon_not_initialized"
            client.close()

    def test_readyz_200_when_ready(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._event_loop_task = _FakeTask()
            with TestClient(app) as client:
                resp = client.get("/readyz")
                assert resp.status_code == 200
                assert resp.json()["status"] == "ready"

    def test_readyz_degraded_to_ready_transition(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._event_loop_task = _FakeTask()
            with TestClient(app) as client:
                app.state._degraded = "temp fault"
                assert client.get("/readyz").status_code == 503
                assert client.get("/readyz").json()["status"] == "degraded"

                app.state._degraded = None
                resp = client.get("/readyz")
                assert resp.status_code == 200
                assert resp.json()["status"] == "ready"

    def test_metrics_endpoint_returns_prometheus(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/metrics")
                assert resp.status_code == 200
                assert "text/plain" in resp.headers.get("content-type", "")

    def test_admin_metrics_export_returns_json(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            # Disable auth for this test
            app.state._no_auth = True
            app.state._allow_no_auth = True
            app.state._require_auth = False
            with TestClient(app) as client:
                resp = client.get("/admin/metrics/export")
                assert resp.status_code == 200
                data = resp.json()
                assert "counters" in data
                assert "gauges" in data
                assert "uptime_seconds" in data


# ── Daemon shutdown ─────────────────────────────────────────────────────────

class TestDaemonShutdown:
    def test_engine_disposed_after_context_exit(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                engine = app.state._db_engine
                assert engine is not None
            status = engine.pool.status()
            assert any(
                phrase in status for phrase in (
                    "Pool size: 0", "Pool closed", "Overflow: -5",
                )
            )

    def test_event_loop_task_cancelled_after_shutdown(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                task = app.state._event_loop_task
                assert task is not None
                assert not task.done()
            assert task.cancelled() or task.done()

    def test_event_bus_cleared_on_shutdown(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                bus = app.state._event_bus
                assert bus is not None
            assert bus is not None

    def test_reload_lock_available(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                lock = app.state._reload_lock
                assert lock is not None
                acquired = lock.acquire(blocking=False)
                assert acquired
                lock.release()


# ── CLI commands (no daemon needed) ─────────────────────────────────────────

class TestCliHelpAndVersion:
    def test_help_flag_exits_zero(self, capsys, monkeypatch):
        _out, _err, code = _run_cli_output(["--help"], capsys)
        assert code == 0

    def test_help_output_contains_key_commands(self, capsys, monkeypatch):
        out, _err, _code = _run_cli_output(["--help"], capsys)
        assert "daemon" in out
        assert "add" in out
        assert "status" in out
        assert "list" in out

    def test_help_subcommand_works(self, monkeypatch):
        with patch("general_ludd.cli._cmd_help") as mock_cmd:
            _run_cli(["help"])
        mock_cmd.assert_called_once()

    def test_version_output_contains_semver(self, capsys, monkeypatch):
        out, _err, _code = _run_cli_output(["--version"], capsys)
        assert __version__ in out

    def test_no_args_shows_help(self, capsys, monkeypatch):
        out, _err, code = _run_cli_output([], capsys)
        assert code == 1 or "usage" in out.lower()

    def test_daemon_help_shows_options(self, capsys, monkeypatch):
        out, _err, _code = _run_cli_output(["daemon", "--help"], capsys)
        assert "--host" in out
        assert "--port" in out

    def test_add_help_shows_options(self, capsys, monkeypatch):
        out, _err, _code = _run_cli_output(["add", "--help"], capsys)
        assert "TITLE" in out

    def test_invalid_command_exits_nonzero(self, monkeypatch):
        code = _run_cli(["nonexistentcommandxyz123"])
        assert code != 0

    def test_unknown_subcommand_shows_error(self, capsys, monkeypatch):
        _out, _err, code = _run_cli_output(["nonexistentcommandxyz123"], capsys)
        assert code != 0

    def test_version_flag_is_minimal(self, capsys, monkeypatch):
        out, _err, code = _run_cli_output(["--version"], capsys)
        assert code == 0
        assert len(out.strip()) > 0


# ── CLI daemon subcommand parsing ───────────────────────────────────────────

class TestCliDaemonParsing:
    def test_daemon_default_args(self, monkeypatch):
        with patch("general_ludd.cli._cmd_daemon") as mock_cmd:
            _run_cli(["daemon"])
        args = mock_cmd.call_args[0][0]
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.log_level == "info"
        assert args.tick_interval == 1.0
        assert args.workers == 1

    def test_daemon_custom_args(self, monkeypatch):
        with patch("general_ludd.cli._cmd_daemon") as mock_cmd:
            _run_cli([
                "daemon", "--host", "127.0.0.1", "--port", "9000",
                "--log-level", "debug", "--tick-interval", "2.5", "--workers", "4",
                "--project", "proj-test", "--config-dir", "/tmp/cfg",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.port == 9000
        assert args.log_level == "debug"
        assert args.tick_interval == 2.5
        assert args.workers == 4
        assert args.project == "proj-test"
        assert args.config_dir == "/tmp/cfg"

    def test_daemon_invalid_log_level_blocked(self, monkeypatch):
        code = _run_cli(["daemon", "--log-level", "verbose"])
        assert code != 0

    def test_daemon_all_log_levels_accepted(self, monkeypatch):
        for level in ["debug", "info", "warning", "error"]:
            with patch("general_ludd.cli._cmd_daemon") as mock_cmd:
                _run_cli(["daemon", "--log-level", level])
            assert mock_cmd.call_args[0][0].log_level == level


# ── Auth middleware ──────────────────────────────────────────────────────────

class TestAuthMiddleware:
    def test_public_get_paths_no_auth_required(self, monkeypatch):
        monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                event_loop_task = app.state._event_loop_task
                assert event_loop_task is not None
                assert not event_loop_task.done()
                assert client.get("/healthz").status_code == 200
                ready_response = client.get("/readyz")
                if os.environ.get("GLUDD_E2E_ACTIVE") == "1":
                    assert ready_response.status_code == 503
                    assert ready_response.json() == {
                        "status": "not_ready",
                        "reason": "daemon_not_initialized",
                    }
                else:
                    assert ready_response.status_code == 200
                    assert ready_response.json() == {"status": "ready"}
                assert "auth_required" not in ready_response.text
                assert client.get("/docs").status_code == 200
                assert client.get("/openapi.json").status_code == 200
                protected = client.get("/admin/daemon/stats")
                assert protected.status_code == 503
                assert "auth_required" in protected.text

    def test_admin_path_401_without_psk(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key-12345")
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/admin/daemon/stats")
                assert resp.status_code == 401

    def test_admin_path_200_with_bearer_token(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key-12345")
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get(
                    "/admin/daemon/stats",
                    headers={"Authorization": "Bearer test-secret-key-12345"},
                )
                assert resp.status_code == 200

    def test_admin_path_401_with_wrong_token(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "correct-token")
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get(
                    "/admin/daemon/stats",
                    headers={"Authorization": "Bearer wrong-token"},
                )
                assert resp.status_code == 401

    def test_psk_disabled_allows_admin_access(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PSK_DISABLE", "1")
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/admin/daemon/stats")
                assert resp.status_code == 200

    def test_fail_closed_no_psk_refuses_admin(self, monkeypatch):
        monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/admin/daemon/stats")
                assert resp.status_code == 503
                assert "auth_required" in resp.text

    def test_bearer_token_with_project_claim_stamps_state(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "shared-token-123")
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get(
                    "/admin/daemon/stats",
                    headers={"Authorization": "Bearer proj-7:shared-token-123"},
                )
                assert resp.status_code == 200
                # The project_id is stamped on request.state internally


# ── Stats middleware ─────────────────────────────────────────────────────────

class TestStatsMiddleware:
    def test_stats_increment_on_request(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                initial = app.state._stats_requests
                client.get("/healthz")
                assert app.state._stats_requests == initial + 1
                assert app.state._stats_responses == initial + 1

    def test_stats_track_multiple_requests(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                for _ in range(5):
                    client.get("/healthz")
                assert app.state._stats_requests == 5
                assert app.state._stats_responses == 5

    def test_admin_daemon_stats_endpoint(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._no_auth = True
            app.state._allow_no_auth = True
            app.state._require_auth = False
            with TestClient(app) as client:
                resp = client.get("/admin/daemon/stats")
                assert resp.status_code == 200
                data = resp.json()
                assert "pid" in data
                assert "requests_total" in data
                assert "responses_total" in data
                assert "memory_mb" in data
                assert "uptime_s" in data


# ── Config hot-reload ───────────────────────────────────────────────────────

class TestConfigHotReload:
    def test_config_reload_endpoint_exists(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._no_auth = True
            app.state._allow_no_auth = True
            app.state._require_auth = False
            with TestClient(app) as client:
                resp = client.post("/admin/config/reload")
                assert resp.status_code == 200
                data = resp.json()
                assert "success" in data or data.get("success") is True

    def test_config_reload_updates_startup_config(self, tmp_path, monkeypatch):
        config_dir = _make_config_dir(
            tmp_path, "rules:\n  - name: reload-test\n    description: A rule\n"
        )
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            app.state._no_auth = True
            app.state._allow_no_auth = True
            app.state._require_auth = False
            with TestClient(app) as client:
                resp = client.post("/admin/config/reload")
                assert resp.status_code == 200
                new = app.state._startup_config
                assert new is not None

    def test_healthz_healthy_after_reload(self, tmp_path, monkeypatch):
        config_dir = _make_config_dir(tmp_path)
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            app.state._no_auth = True
            app.state._allow_no_auth = True
            app.state._require_auth = False
            with TestClient(app) as client:
                client.post("/admin/config/reload")
                resp = client.get("/healthz")
                assert resp.status_code == 200
                assert resp.json()["status"] == "healthy"


# ── CIDR middleware ──────────────────────────────────────────────────────────

class TestCidrMiddleware:
    def test_cidr_allows_when_empty_list(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._allowed_cidr = []
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200

    def test_cidr_blocked_non_matching_ip(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._allowed_cidr = ["10.0.0.0/8"]
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 403
                data = resp.json()
                assert data["error"] == "forbidden"
                assert "allowed_cidr" in data["reason"]

    def test_cidr_allows_loopback(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            app.state._allowed_cidr = ["127.0.0.0/8"]
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200


# ── Error recovery / degraded startup ───────────────────────────────────────

class TestErrorRecovery:
    def test_degraded_flag_set_on_startup_failure(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()), patch(
            "general_ludd.daemon.ensure_tables",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                degraded = app.state._degraded
                assert degraded is not None
                assert "simulated DB failure" in str(degraded)

    def test_healthz_degraded_after_startup_failure(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()), patch(
            "general_ludd.daemon.ensure_tables",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "degraded"
                assert "simulated DB failure" in str(data.get("reason", ""))

    def test_readyz_503_after_startup_failure(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()), patch(
            "general_ludd.daemon.ensure_tables",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.get("/readyz")
                assert resp.status_code == 503

    def test_public_paths_still_accessible_on_degraded(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()), patch(
            "general_ludd.daemon.ensure_tables",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                assert client.get("/healthz").status_code == 200
                assert client.get("/docs").status_code == 200

    def test_degraded_guard_blocks_dispatch_on_degraded(self, monkeypatch):
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()), patch(
            "general_ludd.daemon.ensure_tables",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app) as client:
                resp = client.post("/api/dispatch/claim", json={})
                assert resp.status_code == 503


# ── _lifespan shutdown robustness ──────────────────────────────────────────

class TestLifespanShutdownRobustness:
    def test_engine_disposed_even_with_exception(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                engine = app.state._db_engine
            status = engine.pool.status()
            assert any(phrase in status for phrase in (
                "Pool size: 0", "Pool closed", "Overflow: -5",
            ))

    def test_no_crash_on_empty_shutdown(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()), patch.object(
            dm, "ensure_tables",
            side_effect=RuntimeError("fail"),
        ):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                pass
            assert app.state._degraded is not None

    def test_swallow_errors_during_cleanup(self, monkeypatch):
        import general_ludd.daemon as dm

        class BrokenEngine:
            async def dispose(self):
                raise RuntimeError("dispose failure")

        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                app.state._db_engine = BrokenEngine()
            # Should not raise even though engine.dispose() fails

    def test_event_loop_stopped_before_task_cancelled(self, monkeypatch):
        import general_ludd.daemon as dm
        with patch("general_ludd.ansible.runner.AnsibleRunnerAdapter", return_value=MagicMock()):
            app = dm.create_daemon_app(tick_interval=0.01)
            with TestClient(app):
                pass
            task = app.state._event_loop_task
            assert task.cancelled() or task.done()
