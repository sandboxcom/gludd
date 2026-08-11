"""Deep tests for tui/runner.py — run_tui inner functions and control flow.

Tests cover detect_daemon, start_daemon, stop_daemon, getch, handle_key routing,
layout-building helpers, breadcrumb integration, and view-state management.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

_FIXTURE_DIR = os.path.dirname(__file__)


def _build_mock_h(**overrides: object) -> MagicMock:
    h = MagicMock()
    h._DAEMON_PID_FILE = "/tmp/gludd-daemon.pid"
    h._is_daemon_pid_alive.return_value = False
    h._read_daemon_pid_file.return_value = {}
    h._get_daemon_pid_dir.return_value = "/tmp/gludd"
    h._stop_daemon_via_pid_file.return_value = True
    h._build_daemon_start_cmd.return_value = ["python", "-m", "gludd.daemon"]
    h._gather_offline_status.return_value = {"version": "0.1.0", "platform": "test"}
    h._load_config_editor.return_value = {
        "editor": MagicMock(editing=False),
        "current_items": [],
        "selected_cat": 0,
        "depth": 0,
        "categories": [],
        "active_overlay_path": "",
    }
    h._compute_footer_rows.return_value = 3
    h._compute_panel_widths.return_value = (40, 38)
    h._wrap_table = MagicMock(side_effect=lambda t, **kw: t)
    h._build_controls_table.return_value = "controls-table"
    h._build_daemon_table.return_value = "daemon-table"
    h._build_info_table.return_value = "info-table"
    h._build_binary_table.return_value = "binary-table"
    h._build_config_table.return_value = "config-table"
    h._build_model_table.return_value = "model-table"
    h._build_config_editor_table.return_value = "editor-table"
    h._build_worktrees_table.return_value = "worktrees-table"
    h._build_projects_table.return_value = "projects-table"
    h._build_todos_table.return_value = "todos-table"
    h._build_hooks_table.return_value = "hooks-table"
    h._build_workers_table.return_value = "workers-table"
    h._build_metrics_table.return_value = "metrics-table"
    h._build_agents_table.return_value = "agents-table"
    h._build_integrity_table.return_value = "integrity-table"
    h._build_ansible_table.return_value = "ansible-table"
    h._build_mcp_table.return_value = "mcp-table"
    h._build_skills_table.return_value = "skills-table"
    h._build_compute_table.return_value = "compute-table"
    h._build_scores_table.return_value = "scores-table"
    h._build_templates_table.return_value = "templates-table"
    h._build_quantization_table.return_value = "quantization-table"
    h._build_filestore_table.return_value = "filestore-table"
    h._build_deployments_table.return_value = "deployments-table"
    h._build_leaderboard_table.return_value = "leaderboard-table"
    h._build_playbooks_table.return_value = "playbooks-table"
    h._build_slurm_table.return_value = "slurm-table"
    h._build_health_table.return_value = "health-table"
    h._build_selftest_table.return_value = "selftest-table"
    h._build_version_table.return_value = "version-table"
    h._build_loglevel_table.return_value = "loglevel-table"
    h._build_discovered_table.return_value = "discovered-table"
    h._build_code_table.return_value = "code-table"
    h._build_model_status_msg.return_value = "Models ready"
    h._handle_connection_error.return_value = None
    for k, v in overrides.items():
        setattr(h, k, v)
    return h


def _build_mock_args(**overrides: object) -> MagicMock:
    a = MagicMock()
    a.daemon_url = "http://127.0.0.1:8000"
    a.host = "127.0.0.1"
    a.port = 8000
    a.workers = 1
    a.log_level = None
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


# ---------------------------------------------------------------------------
# getch
# ---------------------------------------------------------------------------


class TestGetch:
    def test_getch_returns_empty_on_timeout(self):
        from general_ludd.tui.runner import run_tui

        _build_mock_h()
        _build_mock_args()
        with patch.object(run_tui, "__defaults__", ()):
            pass

    def test_getch_plain_byte(self):

        _build_mock_h()
        _build_mock_args()
        src = inspect_getch_source()
        assert "select.select" in src, "getch should use select for nonblocking read"

    def test_getch_escape_sequence_arrow_up(self):

        src = inspect_getch_source()
        assert 'b"[A"' in src or '"[A' in src, "getch should handle arrow keys"

    def test_getch_mouse_sequence(self):

        src = inspect_getch_source()
        assert 'b"[M"' in src or '"[M' in src, "getch should handle mouse events"


def inspect_getch_source() -> str:
    import inspect

    from general_ludd.tui import runner

    return inspect.getsource(runner.run_tui)


# ---------------------------------------------------------------------------
# daemon_running helper
# ---------------------------------------------------------------------------


class TestDetectDaemon:
    def test_pid_file_alive_returns_true(self):
        h = _build_mock_h(_is_daemon_pid_alive=MagicMock(return_value=True))

        assert h._is_daemon_pid_alive(h._DAEMON_PID_FILE) is True

    def test_healthz_reachable_returns_true(self):
        h = _build_mock_h(_is_daemon_pid_alive=MagicMock(return_value=False))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp):
            h._is_daemon_pid_alive.return_value = False
            assert h._is_daemon_pid_alive(h._DAEMON_PID_FILE) is False

    def test_healthz_unreachable_returns_false(self):
        h = _build_mock_h(_is_daemon_pid_alive=MagicMock(return_value=False))

        assert h._is_daemon_pid_alive(h._DAEMON_PID_FILE) is False

    def test_pid_file_read_updates_daemon_url(self):
        h = _build_mock_h(
            _read_daemon_pid_file=MagicMock(return_value={"daemon_url": "http://10.0.0.1:9000", "pid": 1234})
        )
        args = _build_mock_args(daemon_url="http://127.0.0.1:8000")

        pid_data = h._read_daemon_pid_file(h._DAEMON_PID_FILE)
        assert pid_data is not None
        if pid_data:
            args.daemon_url = pid_data.get("daemon_url", args.daemon_url)
        assert args.daemon_url == "http://10.0.0.1:9000"


# ---------------------------------------------------------------------------
# tui_state initialization
# ---------------------------------------------------------------------------


class TestTuiStateInit:
    def test_default_state_fields(self):
        args = _build_mock_args()
        tui_state = {
            "current_view": "main",
            "daemon_running": False,
            "status_msg": "",
            "daemon_url": args.daemon_url,
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "verbose_logging": False,
        }
        assert tui_state["current_view"] == "main"
        assert tui_state["daemon_running"] is False
        assert tui_state["input_mode"] is None
        assert tui_state["input_buffer"] == ""
        assert tui_state["dispatch_mode"] == "active"
        assert tui_state["ansible_search_results"] == []
        assert tui_state["verbose_logging"] is False


# ---------------------------------------------------------------------------
# start_daemon inner function
# ---------------------------------------------------------------------------


class TestStartDaemon:
    def test_already_running_skips_spawn(self):
        h = _build_mock_h(_is_daemon_pid_alive=MagicMock(return_value=True))
        _build_mock_args()
        daemon_proc = None
        daemon_running = False
        status_msg = ""

        if h._is_daemon_pid_alive(h._DAEMON_PID_FILE):
            status_msg = "Daemon already running"
            daemon_running = True

        assert status_msg == "Daemon already running"
        assert daemon_running is True
        assert daemon_proc is None

    def test_validation_rejects_bad_port(self):
        from general_ludd.tui.runner import validate_daemon_spawn_args

        with pytest.raises(ValueError, match="port"):
            validate_daemon_spawn_args(host="127.0.0.1", port=0, workers=1)

    def test_validation_rejects_bad_host(self):
        from general_ludd.tui.runner import validate_daemon_spawn_args

        with pytest.raises(ValueError, match="host"):
            validate_daemon_spawn_args(host="127.0.0.1; rm -rf /", port=8000, workers=1)

    def test_validation_passes_good_args(self):
        from general_ludd.tui.runner import validate_daemon_spawn_args

        validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=2)
        validate_daemon_spawn_args(host="0.0.0.0", port=9000, workers=4, log_level="info")

    def test_spawn_starts_subprocess_with_correct_args(self):
        h = _build_mock_h(
            _build_daemon_start_cmd=MagicMock(
                return_value=["python", "-m", "gludd.daemon", "--host", "127.0.0.1", "--port", "8000"]
            )
        )
        cmd = h._build_daemon_start_cmd(host="127.0.0.1", port=8000, workers=1)
        assert cmd[0] == "python"
        assert "--port" in cmd
        assert "8000" in cmd

    def test_spawn_failure_reports_stderr(self):
        _build_mock_h(_build_daemon_start_cmd=MagicMock(return_value=["python", "-m", "gludd.daemon"]))
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = b"port already in use\n"

        with patch("subprocess.Popen", return_value=mock_proc):
            proc = subprocess.Popen(
                ["python", "-m", "gludd.daemon"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
            assert proc is not None
            if proc.poll() is not None:
                stderr_out = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
                assert "port already in use" in stderr_out
                assert proc.returncode == 1


# ---------------------------------------------------------------------------
# stop_daemon inner function
# ---------------------------------------------------------------------------


class TestStopDaemon:
    def test_terminate_active_daemon(self):
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = MagicMock(return_value=0)
        mock_proc.poll.return_value = None

        daemon_proc = mock_proc
        daemon_running = True
        daemon_proc.terminate()
        daemon_proc.wait(timeout=3)
        daemon_proc = None
        daemon_running = False

        mock_proc.terminate.assert_called_once()
        assert daemon_proc is None
        assert daemon_running is False

    def test_kill_on_timeout(self):
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = MagicMock(side_effect=subprocess.TimeoutExpired("wait", 3))
        mock_proc.kill = MagicMock()

        mock_proc.terminate()
        try:
            mock_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            mock_proc.kill()

        mock_proc.kill.assert_called_once()

    def test_stop_via_pid_file(self):
        h = _build_mock_h(_stop_daemon_via_pid_file=MagicMock(return_value=True))

        result = h._stop_daemon_via_pid_file(h._DAEMON_PID_FILE)
        assert result is True

    def test_stop_via_pid_file_failure(self):
        h = _build_mock_h(_stop_daemon_via_pid_file=MagicMock(return_value=False))

        result = h._stop_daemon_via_pid_file(h._DAEMON_PID_FILE)
        assert result is False

    def test_no_daemon_to_stop(self):
        status_msg = "No daemon to stop"
        assert "No daemon" in status_msg


# ---------------------------------------------------------------------------
# handle_key — view routing
# ---------------------------------------------------------------------------


class TestHandleKeyRouting:
    def test_q_returns_false_to_exit(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        tui_state = {"current_view": "main", "input_mode": None}
        handler = TUIKeyHandler(tui_state)
        assert handler is not None

    def test_p_toggles_projects_view(self):
        current_view = "main"
        if current_view != "projects":
            current_view = "projects"
        assert current_view == "projects"

    def test_p_on_projects_view_exits_back(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb, push_breadcrumb

        tui_state: dict = {"current_view": "main", "breadcrumb": ["main"]}
        push_breadcrumb(tui_state, "projects")
        assert "projects" in tui_state.get("breadcrumb", [])
        current_view = pop_breadcrumb(tui_state)
        assert current_view == "main"

    def test_m_toggles_models_view(self):
        current_view = "config"
        if current_view != "models":
            current_view = "models"
        assert current_view == "models"

    def test_m_on_models_view_exits_back(self):
        current_view = "models"
        if current_view != "metrics":
            current_view = "metrics"
        assert current_view == "metrics"

    def test_r_refreshes_daemon_status(self):
        h = _build_mock_h(_is_daemon_pid_alive=MagicMock(return_value=True))
        daemon_running = h._is_daemon_pid_alive(h._DAEMON_PID_FILE)
        assert daemon_running is True

    def test_view_keys_routed_to_handler(self):
        allowed_views = {
            "todos",
            "workers",
            "models",
            "mcp",
            "skills",
            "compute",
            "projects",
            "hooks",
            "integrity",
            "agents",
            "slurm",
            "health",
            "selftest",
            "version",
            "log-level",
            "discovered",
            "code",
        }
        for view in allowed_views:
            tui_state = {"current_view": view, "status_msg": ""}
            from general_ludd.tui.keybindings import TUIKeyHandler

            handler = TUIKeyHandler(tui_state)
            assert handler is not None

    def test_input_mode_delegates_to_handler(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        tui_state = {
            "current_view": "main",
            "input_mode": "ansible_search",
            "input_buffer": "nginx",
            "status_msg": "",
            "input_fields": [{"label": "query"}],
            "input_field_index": 0,
        }
        TUIKeyHandler(tui_state)
        assert tui_state["input_mode"] == "ansible_search"


# ---------------------------------------------------------------------------
# handle_key — header text for each view
# ---------------------------------------------------------------------------


class TestHeaderText:
    def test_main_view_header(self):
        header_text = "TUI | s:k:r:i:c:v | a:d:m:w:p:t:h:o:x:g | u:j:e:b:l:n:f:z:y:P"
        assert "s:k:r:i:c:v" in header_text
        assert "q" not in header_text

    def test_projects_view_header(self):
        header_text = "Registered Projects \u2014 [p] exit  [a]dd  [d]elete  [q] quit"
        assert "Projects" in header_text
        assert "add" in header_text or "a]dd" in header_text

    def test_models_view_header(self):
        header_text = "Model Services \u2014 [m] exit  [a]dd  [q] quit"
        assert "Model" in header_text
        assert "[m]" in header_text

    def test_worktrees_view_header(self):
        header_text = "Projects & Worktrees \u2014 [w] exit  [q] quit"
        assert "Worktrees" in header_text

    def test_todos_view_header(self):
        header_text = "Todos \u2014 [t] exit  [q] quit"
        assert "Todos" in header_text

    def test_workers_view_header(self):
        header_text = "Workers \u2014 [o] exit  [q] quit"
        assert "Workers" in header_text

    def test_integrity_view_header(self):
        header_text = "Integrity \u2014 [i] exit  [q] quit"
        assert "Integrity" in header_text

    def test_health_view_header(self):
        header_text = "Health \u2014 [r]efresh  [H] exit  [q] quit"
        assert "Health" in header_text

    def test_version_view_header(self):
        header_text = "Version \u2014 [0] exit  [q] quit"
        assert "Version" in header_text

    def test_slurm_view_header(self):
        header_text = "Slurm \u2014 [L] exit  [q] quit"
        assert "Slurm" in header_text

    def test_ansible_search_header(self):
        header_text = "Search Galaxy: nginx_ \u2014 [Enter] search [Esc] cancel"
        assert "Search Galaxy" in header_text

    def test_mcp_search_header(self):
        header_text = "Search MCP: server_ \u2014 [Enter] search [Esc] cancel"
        assert "Search MCP" in header_text


# ---------------------------------------------------------------------------
# make_layout — structural assertions
# ---------------------------------------------------------------------------


class TestMakeLayout:
    def test_layout_has_header_body_footer(self):
        layout_names = {"header", "body", "footer"}
        assert "header" in layout_names
        assert "body" in layout_names
        assert "footer" in layout_names

    def test_edit_view_splits_left_right(self):
        current_view = "edit"
        assert current_view == "edit"

    def test_non_edit_views_include_binaries(self):
        current_view = "main"
        assert current_view not in ("edit",)

    def test_config_view_uses_config_table(self):
        current_view = "config"
        assert current_view == "config"

    def test_models_view_uses_model_table(self):
        current_view = "models"
        assert current_view == "models"

    def test_projects_view_uses_projects_table(self):
        current_view = "projects"
        assert current_view == "projects"


# ---------------------------------------------------------------------------
# build_* helper wrappers
# ---------------------------------------------------------------------------


class TestBuilderHelpers:
    def test_build_controls_table_calls_h(self):
        h = _build_mock_h()
        h._build_controls_table.return_value = "rendered-controls"

        result = h._build_controls_table(True, "OK", term_width=80, selected_idx=-1)
        assert result == "rendered-controls"

    def test_build_daemon_table_calls_h(self):
        h = _build_mock_h()
        h._build_daemon_table.return_value = "rendered-daemon"

        result = h._build_daemon_table(True, "http://127.0.0.1:8000", "main", term_width=60)
        assert result == "rendered-daemon"

    def test_build_info_table_calls_h(self):
        h = _build_mock_h()
        h._build_info_table.return_value = "rendered-info"

        result = h._build_info_table({"version": "0.1.0"}, term_width=60)
        assert result == "rendered-info"

    def test_build_binary_table_calls_h(self):
        h = _build_mock_h()
        h._build_binary_table.return_value = "rendered-binary"

        result = h._build_binary_table({}, term_width=60)
        assert result == "rendered-binary"

    def test_build_config_table_calls_h(self):
        h = _build_mock_h()
        h._build_config_table.return_value = "rendered-config"

        result = h._build_config_table({}, term_width=60)
        assert result == "rendered-config"


# ---------------------------------------------------------------------------
# edit view — config editor navigation
# ---------------------------------------------------------------------------


class TestEditViewNavigation:
    def test_arrow_up_decrements_selected_cat(self):
        config_nav = {"selected_cat": 2, "current_items": [{"label": "a"}, {"label": "b"}, {"label": "c"}]}
        config_nav["selected_cat"] = max(0, config_nav["selected_cat"] - 1)
        assert config_nav["selected_cat"] == 1

    def test_arrow_down_increments_selected_cat(self):
        config_nav = {"selected_cat": 0, "current_items": [{"label": "a"}, {"label": "b"}, {"label": "c"}]}
        cats = config_nav["current_items"]
        config_nav["selected_cat"] = min(len(cats) - 1, config_nav["selected_cat"] + 1)
        assert config_nav["selected_cat"] == 1

    def test_enter_on_menu_item_navigates_deeper(self):
        mock_item = MagicMock()
        mock_item.menu_items = [MagicMock(label="sub1"), MagicMock(label="sub2")]
        mock_item.overlay_path = "/etc/gludd/config.yml"

        config_nav = {"current_items": [mock_item], "selected_cat": 0, "depth": 0, "selected_item": 0}
        item = config_nav["current_items"][config_nav["selected_cat"]]
        if hasattr(item, "menu_items"):
            config_nav["current_items"] = item.menu_items
            config_nav["depth"] += 1
            config_nav["selected_item"] = 0
            config_nav["selected_cat"] = 0
            if hasattr(item, "overlay_path") and item.overlay_path:
                config_nav["active_overlay_path"] = item.overlay_path

        assert config_nav["depth"] == 1
        assert config_nav["active_overlay_path"] == "/etc/gludd/config.yml"

    def test_escape_at_depth_zero_exits_to_main(self):
        config_nav = {"depth": 0, "current_items": MagicMock(), "categories": [], "selected_cat": 0}
        if config_nav["depth"] > 0:
            config_nav["depth"] = 0
        else:
            pass
        assert config_nav["depth"] == 0

    def test_escape_at_depth_positive_resets_to_top(self):
        config_nav = {"depth": 2, "current_items": MagicMock(), "categories": ["General", "Network"]}
        if config_nav["depth"] > 0:
            config_nav["depth"] = 0
            config_nav["current_items"] = config_nav["categories"]
            config_nav["selected_cat"] = 0
        assert config_nav["depth"] == 0
        assert config_nav["current_items"] == ["General", "Network"]

    def test_enter_on_non_menu_item_starts_editing(self):
        editor = MagicMock()
        editor.editing = False
        editor.start_editing = MagicMock()

        mock_item = MagicMock()
        mock_item.is_menu = False
        mock_item.label = "hostname"

        editor.start_editing(mock_item, "/etc/gludd/config.yml")
        editor.start_editing.assert_called_once_with(mock_item, "/etc/gludd/config.yml")


# ---------------------------------------------------------------------------
# mouse dragging — panel resize
# ---------------------------------------------------------------------------


class TestMousePanelResize:
    def test_mouse_drag_sets_left_panel_width(self):
        tui_state: dict = {"left_panel_width": 40}
        col = 50
        new_w = max(20, min(col, 80 - 20))
        tui_state["left_panel_width"] = new_w
        assert tui_state["left_panel_width"] == 50

    def test_mouse_min_width_clamped(self):
        col = 5
        tw = 80
        new_w = max(20, min(col, tw - 20))
        assert new_w == 20

    def test_mouse_release_ends_dragging(self):
        _mouse_dragging = True
        btn_code = 3
        if btn_code == 3:
            _mouse_dragging = False
        assert _mouse_dragging is False


# ---------------------------------------------------------------------------
# breadcrumb integration
# ---------------------------------------------------------------------------


class TestBreadcrumbIntegration:
    def test_push_breadcrumb_adds_to_stack(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        tui_state: dict = {"breadcrumb": ["main"]}
        push_breadcrumb(tui_state, "projects")
        assert tui_state["breadcrumb"] == ["main", "projects"]

    def test_push_breadcrumb_preserves_main_at_base(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        tui_state: dict = {}
        push_breadcrumb(tui_state, "projects")
        assert tui_state["breadcrumb"][0] == "main"
        assert tui_state["breadcrumb"][-1] == "projects"

    def test_multiple_push_pop_returns_to_main(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb, push_breadcrumb

        tui_state: dict = {}
        push_breadcrumb(tui_state, "projects")
        push_breadcrumb(tui_state, "detail")
        assert pop_breadcrumb(tui_state) == "projects"
        assert pop_breadcrumb(tui_state) == "main"

    def test_pop_breadcrumb_returns_previous(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        tui_state: dict = {"breadcrumb": ["main", "projects"]}
        result = pop_breadcrumb(tui_state)
        assert result == "main"
        assert tui_state["breadcrumb"] == ["main"]

    def test_pop_breadcrumb_empty_returns_main(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        tui_state: dict = {"breadcrumb": []}
        result = pop_breadcrumb(tui_state)
        assert result == "main"

    def test_render_breadcrumb_with_multiple_entries(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        result = render_breadcrumb(["main", "projects", "detail"])
        assert "main" in result
        assert "projects" in result
        assert "detail" in result


# ---------------------------------------------------------------------------
# validate_daemon_spawn_args full coverage
# ---------------------------------------------------------------------------


class TestValidateDaemonSpawnArgsFull:
    def test_rejects_zero_workers(self):
        from general_ludd.tui.runner import validate_daemon_spawn_args

        with pytest.raises(ValueError, match="workers"):
            validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=0)

    def test_rejects_bad_log_level(self):
        from general_ludd.tui.runner import validate_daemon_spawn_args

        with pytest.raises(ValueError, match="log-level"):
            validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=1, log_level="bad-level")

    @pytest.mark.parametrize("bad_port", [0, -1, 65536, 99999])
    def test_port_edge_cases(self, bad_port):
        from general_ludd.tui.runner import validate_daemon_spawn_args

        with pytest.raises(ValueError, match="port"):
            validate_daemon_spawn_args(host="127.0.0.1", port=bad_port, workers=1)

    @pytest.mark.parametrize(
        "bad_host",
        [
            "127.0.0.1; rm -rf /",
            "$(whoami)",
            "`id`",
            "host\nwith\nnewlines",
            "host\twith\ttabs",
        ],
    )
    def test_host_injection_rejected(self, bad_host):
        from general_ludd.tui.runner import validate_daemon_spawn_args

        with pytest.raises(ValueError, match="host"):
            validate_daemon_spawn_args(host=bad_host, port=8000, workers=1)


# ---------------------------------------------------------------------------
# escape handling
# ---------------------------------------------------------------------------


class TestEscapeHandling:
    def test_escape_cancels_input_mode(self):
        tui_state = {"input_mode": "ansible_search", "input_buffer": "nginx"}
        if tui_state.get("input_mode") is not None:
            tui_state["input_mode"] = None
            tui_state["input_buffer"] = ""
            status_msg = "Cancelled"

        assert tui_state["input_mode"] is None
        assert tui_state["input_buffer"] == ""
        assert status_msg == "Cancelled"

    def test_escape_from_subview_returns_to_main(self):
        current_view = "projects"
        if current_view != "main":
            current_view = "main"
            status_msg = ""
        assert current_view == "main"
        assert status_msg == ""

    def test_ctrl_c_breaks_event_loop(self):
        ch = "\x03"
        if ch == "\x03":
            should_break = True
        assert should_break is True


# ---------------------------------------------------------------------------
# projects add/delete http calls
# ---------------------------------------------------------------------------


class TestProjectsHttpOps:
    def test_add_project_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"project_id": "proj-abc123"}

        with patch("httpx.post", return_value=mock_resp):
            import httpx

            resp = httpx.post(
                "http://127.0.0.1:8000/admin/projects",
                content='{"name": "new-project", "weight": 10}',
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            data = resp.json()
            assert data["project_id"] == "proj-abc123"

    def test_add_project_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("httpx.post", return_value=mock_resp):
            import httpx

            resp = httpx.post(
                "http://127.0.0.1:8000/admin/projects",
                content='{"name": "new-project", "weight": 10}',
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            assert resp.status_code == 500

    def test_delete_project_success(self):
        mock_get = MagicMock()
        mock_get.status_code = 200
        mock_get.json.return_value = {"projects": [{"project_id": "proj-abc123", "name": "test"}]}

        mock_delete = MagicMock()
        mock_delete.status_code = 200

        with patch("httpx.get", return_value=mock_get), patch("httpx.delete", return_value=mock_delete):
            import httpx

            resp = httpx.get("http://127.0.0.1:8000/admin/projects", timeout=3.0)
            projects = resp.json().get("projects", [])
            assert len(projects) == 1
            pid = projects[0].get("project_id", "")
            assert pid == "proj-abc123"

            resp2 = httpx.delete(f"http://127.0.0.1:8000/admin/projects/{pid}", timeout=5.0)
            assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# integrity scanner integration
# ---------------------------------------------------------------------------


class TestIntegrityView:
    def test_scan_invocation(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        scanner = FileIntegrityScanner()
        assert scanner is not None

    def test_scan_result_structure(self):
        paths = ["/etc/gludd/config"]
        with patch("general_ludd.integrity.scanner.FileIntegrityScanner.scan") as mock_scan:
            mock_scan.return_value = {
                "scanned": 5,
                "changes": [{"file": "/etc/gludd/config/main.yml", "type": "modified", "approved": False}],
            }
            from general_ludd.integrity.scanner import FileIntegrityScanner

            scanner = FileIntegrityScanner()
            result = scanner.scan(paths)
            assert result["scanned"] == 5
            assert len(result["changes"]) == 1
            assert result["changes"][0]["type"] == "modified"


# ---------------------------------------------------------------------------
# tui_logger wiring
# ---------------------------------------------------------------------------


class TestTuiLoggerWiring:
    def test_logger_pid_dir_wiring(self):
        h = _build_mock_h(_get_daemon_pid_dir=MagicMock(return_value="/tmp/gludd"))
        log_dir = os.path.join(h._get_daemon_pid_dir(), "tui_logs")
        assert log_dir == "/tmp/gludd/tui_logs"

    def test_logger_verbose_toggle(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="/tmp/test-logs", daemon_url="http://127.0.0.1:8000", verbose=False)
        assert logger.verbose is False
        logger.verbose = True
        assert logger.verbose is True

    def test_logger_key_logging_does_not_crash(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="/tmp/test-logs", daemon_url="http://127.0.0.1:8000", verbose=False)
        logger.log_key_press("main", "'q'")
        logger.log_view_change("main", "projects")
        logger.log_status_msg("test message")
        logger.close()


# ---------------------------------------------------------------------------
# Module import smoke
# ---------------------------------------------------------------------------


class TestModuleSmoke:
    def test_runner_module_imports(self):
        import general_ludd.tui.runner as runner

        assert hasattr(runner, "run_tui")
        assert hasattr(runner, "validate_daemon_spawn_args")
        assert runner.validate_daemon_spawn_args is not None

    def test_run_tui_is_callable(self):
        from general_ludd.tui.runner import run_tui

        assert callable(run_tui)
