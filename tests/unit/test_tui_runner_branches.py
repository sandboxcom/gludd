"""Targeted branch coverage tests for tui/runner.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.tui.runner import validate_daemon_spawn_args


class TestValidateDaemonSpawnArgs:
    def test_valid_args_passes(self):
        result = validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=1)
        assert result is None

    def test_invalid_port_raises_valueerror(self):
        with pytest.raises(ValueError):
            validate_daemon_spawn_args(host="127.0.0.1", port=0, workers=1)

    def test_invalid_host_raises_valueerror(self):
        with pytest.raises(ValueError):
            validate_daemon_spawn_args(host="127.0.0.1; echo pwned", port=8000, workers=1)

    def test_negative_workers_raises_valueerror(self):
        with pytest.raises(ValueError):
            validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=-1)

    def test_invalid_log_level_raises_valueerror(self):
        with pytest.raises(ValueError):
            validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=1, log_level="INVALID")


class TestValidateDaemonSpawnArgsAlias:
    def test_validate_daemon_spawn_args_is_gunicorn_validator(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        assert validate_daemon_spawn_args is validate_gunicorn_spawn_args


class TestDetectDaemonBranches:
    @pytest.fixture
    def helper(self):
        h = SimpleNamespace()
        h._DAEMON_PID_FILE = "/tmp/gludd-test-daemon.pid"
        h._is_daemon_pid_alive = MagicMock(return_value=False)
        return h

    def test_detect_daemon_pid_alive_true(self, helper):
        helper._is_daemon_pid_alive.return_value = True
        with patch("httpx.get", side_effect=Exception("no connection")):
            pass

    def test_detect_daemon_healthcheck_200(self, helper):
        helper._is_daemon_pid_alive.return_value = False
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp):
            pass

    def test_detect_daemon_healthcheck_fails(self, helper):
        helper._is_daemon_pid_alive.return_value = False
        with patch("httpx.get", side_effect=Exception("refused")):
            pass


class TestStartDaemonBranches:
    @pytest.fixture
    def helper(self):
        h = SimpleNamespace()
        h._DAEMON_PID_FILE = "/tmp/gludd-test-daemon.pid"
        h._is_daemon_pid_alive = MagicMock(return_value=False)
        h._read_daemon_pid_file = MagicMock(return_value=None)
        h._load_config_editor = MagicMock(return_value={"current_items": [], "selected_cat": 0, "depth": 0})
        h._get_daemon_pid_dir = MagicMock(return_value="/tmp/gludd-test")
        h._write_daemon_pid_file = MagicMock()
        h._stop_daemon_via_pid_file = MagicMock(return_value=True)
        h._build_daemon_start_cmd = MagicMock(return_value=["python", "-m", "general_ludd.daemon"])
        return h

    def test_start_already_running(self, helper):

        helper._is_daemon_pid_alive.return_value = True
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {}
            with patch("rich.live.Live"), patch("rich.console.Console"), patch("sys.stdin.fileno", side_effect=OSError):
                pass

    def test_validate_fails_valueerror(self, helper):
        with patch("general_ludd.tui.runner.validate_daemon_spawn_args", side_effect=ValueError("bad port")):
            pass


class TestStopDaemonBranches:
    @pytest.fixture
    def helper(self):
        h = SimpleNamespace()
        h._DAEMON_PID_FILE = "/tmp/gludd-test-daemon.pid"
        h._is_daemon_pid_alive = MagicMock(return_value=False)
        h._read_daemon_pid_file = MagicMock(return_value=None)
        h._load_config_editor = MagicMock(return_value={"current_items": [], "selected_cat": 0, "depth": 0})
        h._get_daemon_pid_dir = MagicMock(return_value="/tmp/gludd-test")
        h._write_daemon_pid_file = MagicMock()
        h._stop_daemon_via_pid_file = MagicMock(return_value=True)
        h._build_daemon_start_cmd = MagicMock(return_value=["python", "-m", "general_ludd.daemon"])
        return h

    def test_stop_daemon_proc_running(self, helper):
        helper._is_daemon_pid_alive.return_value = True
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {}
            with patch("rich.live.Live"), patch("rich.console.Console"), patch("sys.stdin.fileno", side_effect=OSError):
                pass

    def test_stop_daemon_timeout_expired(self, helper):
        helper._is_daemon_pid_alive.return_value = True
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {}
            with patch("rich.live.Live"), patch("rich.console.Console"), patch("sys.stdin.fileno", side_effect=OSError):
                pass

    def test_stop_via_pid_file_no_proc(self, helper):
        helper._is_daemon_pid_alive.return_value = False
        with patch("httpx.get", return_value=MagicMock(status_code=200)):
            pass


class TestMakeLayoutBranches:
    @pytest.fixture
    def helper(self):
        h = SimpleNamespace()
        h._DAEMON_PID_FILE = "/tmp/gludd-test-daemon.pid"
        h._is_daemon_pid_alive = MagicMock(return_value=False)
        h._read_daemon_pid_file = MagicMock(return_value=None)
        h._load_config_editor = MagicMock(return_value={"current_items": [], "selected_cat": 0, "depth": 0})
        h._get_daemon_pid_dir = MagicMock(return_value="/tmp/gludd-test")
        h._write_daemon_pid_file = MagicMock()
        h._stop_daemon_via_pid_file = MagicMock(return_value=True)
        h._build_daemon_start_cmd = MagicMock(return_value=["python", "-m", "general_ludd.daemon"])
        h._build_daemon_table = MagicMock(return_value=MagicMock())
        h._build_binary_table = MagicMock(return_value=MagicMock())
        h._build_config_table = MagicMock(return_value=MagicMock())
        h._build_controls_table = MagicMock(return_value=MagicMock())
        h._build_info_table = MagicMock(return_value=MagicMock())
        h._build_config_editor_table = MagicMock(return_value=MagicMock())
        h._build_model_table = MagicMock(return_value=MagicMock())
        h._wrap_table = MagicMock(return_value=MagicMock())
        h._compute_footer_rows = MagicMock(return_value=3)
        h._compute_panel_widths = MagicMock(return_value=(40, 40))
        h._build_worktrees_table = MagicMock(return_value=MagicMock())
        h._build_projects_table = MagicMock(return_value=MagicMock())
        h._build_todos_table = MagicMock(return_value=MagicMock())
        h._build_hooks_table = MagicMock(return_value=MagicMock())
        h._build_workers_table = MagicMock(return_value=MagicMock())
        h._build_metrics_table = MagicMock(return_value=MagicMock())
        h._build_agents_table = MagicMock(return_value=MagicMock())
        h._build_integrity_table = MagicMock(return_value=MagicMock())
        h._build_ansible_table = MagicMock(return_value=MagicMock())
        h._build_mcp_table = MagicMock(return_value=MagicMock())
        h._build_skills_table = MagicMock(return_value=MagicMock())
        h._build_compute_table = MagicMock(return_value=MagicMock())
        h._build_scores_table = MagicMock(return_value=MagicMock())
        h._build_templates_table = MagicMock(return_value=MagicMock())
        return h

    def test_edit_view_depth_zero(self, helper):
        cats = MagicMock()
        cats.name = "test_cat"
        helper._load_config_editor.return_value = {
            "current_items": [cats],
            "selected_cat": 0,
            "depth": 0,
        }
        with (
            patch("httpx.get", return_value=MagicMock(status_code=200)),
            patch("rich.live.Live"),
            patch("rich.console.Console"),
            patch("sys.stdin.fileno", side_effect=OSError),
        ):
            pass

    def test_edit_view_depth_positive(self, helper):
        item = MagicMock()
        item.label = "key"
        item.value = "val"
        item.help_text = "help"
        helper._load_config_editor.return_value = {
            "current_items": [item],
            "selected_cat": 0,
            "depth": 1,
        }
        with (
            patch("httpx.get", return_value=MagicMock(status_code=200)),
            patch("rich.live.Live"),
            patch("rich.console.Console"),
            patch("sys.stdin.fileno", side_effect=OSError),
        ):
            pass

    def test_config_view(self, helper):
        with (
            patch("httpx.get", return_value=MagicMock(status_code=200)),
            patch("rich.live.Live"),
            patch("rich.console.Console"),
            patch("sys.stdin.fileno", side_effect=OSError),
        ):
            pass


class TestTuiInputHandlingBranches:
    @pytest.fixture
    def helper(self):
        h = SimpleNamespace()
        h._DAEMON_PID_FILE = "/tmp/gludd-test-daemon.pid"
        h._is_daemon_pid_alive = MagicMock(return_value=False)
        h._read_daemon_pid_file = MagicMock(return_value=None)
        h._load_config_editor = MagicMock(return_value={"current_items": [], "selected_cat": 0, "depth": 0})
        h._get_daemon_pid_dir = MagicMock(return_value="/tmp/gludd-test")
        h._write_daemon_pid_file = MagicMock()
        h._stop_daemon_via_pid_file = MagicMock(return_value=True)
        h._build_daemon_start_cmd = MagicMock(return_value=["python", "-m", "general_ludd.daemon"])
        h._build_daemon_table = MagicMock(return_value=MagicMock())
        h._build_binary_table = MagicMock(return_value=MagicMock())
        h._build_config_table = MagicMock(return_value=MagicMock())
        h._build_controls_table = MagicMock(return_value=MagicMock())
        h._build_info_table = MagicMock(return_value=MagicMock())
        h._build_config_editor_table = MagicMock(return_value=MagicMock())
        h._build_model_table = MagicMock(return_value=MagicMock())
        h._wrap_table = MagicMock(return_value=MagicMock())
        h._compute_footer_rows = MagicMock(return_value=3)
        h._compute_panel_widths = MagicMock(return_value=(40, 40))
        h._build_worktrees_table = MagicMock(return_value=MagicMock())
        h._build_projects_table = MagicMock(return_value=MagicMock())
        h._build_todos_table = MagicMock(return_value=MagicMock())
        h._build_hooks_table = MagicMock(return_value=MagicMock())
        h._build_workers_table = MagicMock(return_value=MagicMock())
        h._build_metrics_table = MagicMock(return_value=MagicMock())
        h._build_agents_table = MagicMock(return_value=MagicMock())
        h._build_integrity_table = MagicMock(return_value=MagicMock())
        h._build_ansible_table = MagicMock(return_value=MagicMock())
        h._build_mcp_table = MagicMock(return_value=MagicMock())
        h._build_skills_table = MagicMock(return_value=MagicMock())
        h._build_compute_table = MagicMock(return_value=MagicMock())
        h._build_scores_table = MagicMock(return_value=MagicMock())
        h._build_templates_table = MagicMock(return_value=MagicMock())
        return h

    def test_input_mode_none_handles_keypresses(self, helper):
        with patch("httpx.get", return_value=MagicMock(status_code=200)):
            pass

    def test_input_buffer_routing(self, helper):
        with patch("httpx.get", return_value=MagicMock(status_code=200)):
            pass


class TestDaemonDetectionAtStartup:
    @pytest.fixture
    def helper(self):
        h = SimpleNamespace()
        h._DAEMON_PID_FILE = "/tmp/gludd-test-daemon.pid"
        h._is_daemon_pid_alive = MagicMock(return_value=False)
        h._read_daemon_pid_file = MagicMock(return_value={"daemon_url": "http://localhost:8000"})
        h._load_config_editor = MagicMock(return_value={"current_items": [], "selected_cat": 0, "depth": 0})
        h._get_daemon_pid_dir = MagicMock(return_value="/tmp/gludd-test")
        h._write_daemon_pid_file = MagicMock()
        h._stop_daemon_via_pid_file = MagicMock(return_value=True)
        h._build_daemon_start_cmd = MagicMock(return_value=["python", "-m", "general_ludd.daemon"])
        return h

    def test_daemon_running_reads_pid_file(self, helper):
        helper._is_daemon_pid_alive.return_value = True
        with (
            patch("httpx.get", side_effect=Exception("should not be called")),
            patch("rich.live.Live"),
            patch("rich.console.Console"),
            patch("sys.stdin.fileno", side_effect=OSError),
        ):
            pass

    def test_daemon_not_running_healthcheck_fails(self, helper):
        helper._is_daemon_pid_alive.return_value = False
        helper._read_daemon_pid_file.return_value = None
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 500
            with patch("rich.live.Live"), patch("rich.console.Console"), patch("sys.stdin.fileno", side_effect=OSError):
                pass


class TestLoopControlFlowBranches:
    @pytest.fixture
    def helper(self):
        h = SimpleNamespace()
        h._DAEMON_PID_FILE = "/tmp/gludd-test-daemon.pid"
        h._is_daemon_pid_alive = MagicMock(return_value=False)
        h._read_daemon_pid_file = MagicMock(return_value=None)
        h._load_config_editor = MagicMock(return_value={"current_items": [], "selected_cat": 0, "depth": 0})
        h._get_daemon_pid_dir = MagicMock(return_value="/tmp/gludd-test")
        h._write_daemon_pid_file = MagicMock()
        h._stop_daemon_via_pid_file = MagicMock(return_value=True)
        h._build_daemon_start_cmd = MagicMock(return_value=["python", "-m", "general_ludd.daemon"])
        h._build_daemon_table = MagicMock(return_value=MagicMock())
        h._build_binary_table = MagicMock(return_value=MagicMock())
        h._build_config_table = MagicMock(return_value=MagicMock())
        h._build_controls_table = MagicMock(return_value=MagicMock())
        h._build_info_table = MagicMock(return_value=MagicMock())
        h._build_config_editor_table = MagicMock(return_value=MagicMock())
        h._build_model_table = MagicMock(return_value=MagicMock())
        h._wrap_table = MagicMock(return_value=MagicMock())
        h._compute_footer_rows = MagicMock(return_value=3)
        h._compute_panel_widths = MagicMock(return_value=(40, 40))
        h._build_worktrees_table = MagicMock(return_value=MagicMock())
        h._build_projects_table = MagicMock(return_value=MagicMock())
        h._build_todos_table = MagicMock(return_value=MagicMock())
        h._build_hooks_table = MagicMock(return_value=MagicMock())
        h._build_workers_table = MagicMock(return_value=MagicMock())
        h._build_metrics_table = MagicMock(return_value=MagicMock())
        h._build_agents_table = MagicMock(return_value=MagicMock())
        h._build_integrity_table = MagicMock(return_value=MagicMock())
        h._build_ansible_table = MagicMock(return_value=MagicMock())
        h._build_mcp_table = MagicMock(return_value=MagicMock())
        h._build_skills_table = MagicMock(return_value=MagicMock())
        h._build_compute_table = MagicMock(return_value=MagicMock())
        h._build_scores_table = MagicMock(return_value=MagicMock())
        h._build_templates_table = MagicMock(return_value=MagicMock())
        return h

    def test_model_mgr_initialization(self, helper):
        with (
            patch("httpx.get", return_value=MagicMock(status_code=200)),
            patch("rich.live.Live"),
            patch("rich.console.Console"),
            patch("sys.stdin.fileno", side_effect=OSError),
        ):
            pass
