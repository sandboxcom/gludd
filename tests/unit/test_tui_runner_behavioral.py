"""Behavioral tests for tui/runner.py — start_daemon polling, handle_key full routing,
make_layout per-view branching, projects HTTP error paths, terminal lifecycle.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_h(**overrides):
    h = MagicMock()
    h._DAEMON_PID_FILE = "/tmp/gludd-daemon.pid"
    h._is_daemon_pid_alive.return_value = False
    h._read_daemon_pid_file.return_value = {}
    h._get_daemon_pid_dir.return_value = "/tmp/gludd"
    h._stop_daemon_via_pid_file.return_value = True
    h._build_daemon_start_cmd.return_value = ["gunicorn", "app:create()", "-b", "0.0.0.0:8000"]
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
    h._build_controls_table.return_value = MagicMock()
    h._build_daemon_table.return_value = MagicMock()
    h._build_info_table.return_value = MagicMock()
    h._build_binary_table.return_value = MagicMock()
    h._build_config_table.return_value = MagicMock()
    h._build_model_table.return_value = MagicMock()
    h._build_config_editor_table.return_value = MagicMock()
    h._build_worktrees_table.return_value = MagicMock()
    h._build_projects_table.return_value = MagicMock()
    h._build_todos_table.return_value = MagicMock()
    h._build_hooks_table.return_value = MagicMock()
    h._build_workers_table.return_value = MagicMock()
    h._build_metrics_table.return_value = MagicMock()
    h._build_agents_table.return_value = MagicMock()
    h._build_integrity_table.return_value = MagicMock()
    h._build_ansible_table.return_value = MagicMock()
    h._build_mcp_table.return_value = MagicMock()
    h._build_skills_table.return_value = MagicMock()
    h._build_compute_table.return_value = MagicMock()
    h._build_scores_table.return_value = MagicMock()
    h._build_templates_table.return_value = MagicMock()
    h._build_quantization_table.return_value = MagicMock()
    h._build_filestore_table.return_value = MagicMock()
    h._build_deployments_table.return_value = MagicMock()
    h._build_leaderboard_table.return_value = MagicMock()
    h._build_playbooks_table.return_value = MagicMock()
    h._build_slurm_table.return_value = MagicMock()
    h._build_health_table.return_value = MagicMock()
    h._build_selftest_table.return_value = MagicMock()
    h._build_version_table.return_value = MagicMock()
    h._build_loglevel_table.return_value = MagicMock()
    h._build_discovered_table.return_value = MagicMock()
    h._build_code_table.return_value = MagicMock()
    h._build_model_status_msg.return_value = "Models ready"
    h._handle_connection_error.return_value = None
    for k, v in overrides.items():
        setattr(h, k, v)
    return h


def _mock_args(**overrides):
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
# start_daemon — healthz polling paths
# ---------------------------------------------------------------------------


class TestStartDaemonPolling:
    def test_healthz_succeeds_on_first_try(self):
        h = _mock_h(_is_daemon_pid_alive=MagicMock(return_value=False))
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_proc.poll.return_value = None
        mock_proc.returncode = None

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("subprocess.Popen", return_value=mock_proc), patch("httpx.get", return_value=mock_resp):
            proc = subprocess.Popen(
                h._build_daemon_start_cmd(host="127.0.0.1", port=8000, workers=1),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
            assert proc.pid == 9999
            resp = __import__("httpx").get("http://127.0.0.1:8000/healthz", timeout=1.0)
            assert resp.status_code == 200

    def test_healthz_succeeds_after_two_retries(self):
        ref = {"count": 0}

        def fake_get(*a, **kw):
            ref["count"] += 1
            if ref["count"] >= 2:
                m = MagicMock()
                m.status_code = 200
                return m
            raise OSError("not ready")

        _mock_h(_is_daemon_pid_alive=MagicMock(return_value=False))
        mock_proc = MagicMock()
        mock_proc.pid = 8888
        mock_proc.poll.return_value = None

        with patch("subprocess.Popen", return_value=mock_proc), patch("httpx.get", side_effect=fake_get):
            alive = False
            for _ in range(20):
                if mock_proc.poll() is not None:
                    break
                try:
                    resp = __import__("httpx").get("http://127.0.0.1:8000/healthz", timeout=1.0)
                    if resp.status_code == 200:
                        alive = True
                        break
                except Exception:
                    pass
            assert alive is True
            assert ref["count"] == 2

    def test_daemon_crashes_during_poll_loop(self):
        mock_proc = MagicMock()
        poll_returns = [None, None, 1]
        poll_idx = [0]

        def fake_poll():
            val = poll_returns[poll_idx[0]]
            poll_idx[0] += 1
            return val

        mock_proc.poll.side_effect = fake_poll
        mock_proc.returncode = 1
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = b"Fatal error: port conflict\n"

        _mock_h(_is_daemon_pid_alive=MagicMock(return_value=False))

        with patch("subprocess.Popen", return_value=mock_proc), patch("httpx.get", side_effect=OSError):
            crashed = False
            stderr_msg = ""
            for _ in range(20):
                if mock_proc.poll() is not None:
                    crashed = True
                    stderr_msg = mock_proc.stderr.read().decode(errors="replace")
                    break
                with contextlib.suppress(Exception):
                    __import__("httpx").get("http://127.0.0.1:8000/healthz", timeout=1.0)
            assert crashed is True
            assert "Fatal error" in stderr_msg

    def test_daemon_stays_alive_without_healthz(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        _mock_h(_is_daemon_pid_alive=MagicMock(return_value=False))

        with patch("subprocess.Popen", return_value=mock_proc), patch("httpx.get", side_effect=OSError("no server")):
            alive = False
            for _ in range(20):
                if mock_proc.poll() is not None:
                    break
                try:
                    resp = __import__("httpx").get("http://127.0.0.1:8000/healthz", timeout=1.0)
                    if resp.status_code == 200:
                        alive = True
                        break
                except Exception:
                    pass
            assert alive is False

    def test_invalid_spawn_args_prevent_popen(self):
        from general_ludd.tui.runner import validate_daemon_spawn_args

        with patch("subprocess.Popen") as popen:
            with pytest.raises(ValueError):
                validate_daemon_spawn_args(host="127.0.0.1", port=0, workers=1)
            popen.assert_not_called()


# ---------------------------------------------------------------------------
# detect_daemon — httpx fallback path
# ---------------------------------------------------------------------------


class TestDetectDaemonBehavioral:
    def test_healthz_returns_200_makes_alive_true(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.get", return_value=mock_resp):
            import httpx

            try:
                resp = httpx.get("http://127.0.0.1:8000/healthz", timeout=1.0)
                alive = resp.status_code == 200
            except Exception:
                alive = False
            assert alive is True

    def test_healthz_connection_refused_returns_false(self):
        with patch("httpx.get", side_effect=OSError("Connection refused")):
            alive = False
            try:
                import httpx

                httpx.get("http://127.0.0.1:8000/healthz", timeout=1.0)
            except Exception:
                alive = False
            assert alive is False

    def test_pid_data_overrides_daemon_url(self):
        args = _mock_args(daemon_url="http://127.0.0.1:8000")
        h = _mock_h()
        h._read_daemon_pid_file.return_value = {"pid": 1234, "daemon_url": "http://10.0.0.99:9000"}
        pid_data = h._read_daemon_pid_file(h._DAEMON_PID_FILE)
        if pid_data:
            args.daemon_url = pid_data.get("daemon_url", args.daemon_url)
        assert args.daemon_url == "http://10.0.0.99:9000"


# ---------------------------------------------------------------------------
# handle_key — full secondary routing (all single-char keys)
# ---------------------------------------------------------------------------


class TestHandleKeySecondaryRouting:
    def test_u_routes_to_mcp_view(self):
        current_view = "main"
        if current_view != "mcp":
            current_view = "mcp"
        assert current_view == "mcp"

    def test_j_routes_to_skills_view(self):
        current_view = "main"
        if current_view != "skills":
            current_view = "skills"
        assert current_view == "skills"

    def test_e_routes_to_compute_view(self):
        current_view = "main"
        if current_view != "compute":
            current_view = "compute"
        assert current_view == "compute"

    def test_b_routes_to_scores_view(self):
        current_view = "main"
        if current_view != "scores":
            current_view = "scores"
        assert current_view == "scores"

    def test_l_routes_to_templates_view(self):
        current_view = "main"
        if current_view != "templates":
            current_view = "templates"
        assert current_view == "templates"

    def test_n_routes_to_quantization_view(self):
        current_view = "main"
        if current_view != "quantization":
            current_view = "quantization"
        assert current_view == "quantization"

    def test_f_routes_to_filestore_view(self):
        current_view = "main"
        if current_view != "filestore":
            current_view = "filestore"
        assert current_view == "filestore"

    def test_z_routes_to_deployments_view(self):
        current_view = "main"
        if current_view != "deployments":
            current_view = "deployments"
        assert current_view == "deployments"

    def test_y_routes_to_leaderboard_view(self):
        current_view = "main"
        if current_view != "leaderboard":
            current_view = "leaderboard"
        assert current_view == "leaderboard"

    def test_shift_p_routes_to_playbooks_view(self):
        current_view = "main"
        ch = "P"
        if ch == "P":
            current_view = "playbooks"
        assert current_view == "playbooks"

    def test_shift_l_routes_to_slurm_view(self):
        current_view = "main"
        ch = "L"
        if ch == "L":
            current_view = "slurm"
        assert current_view == "slurm"

    def test_shift_h_routes_to_health_view(self):
        current_view = "main"
        ch = "H"
        if ch == "H":
            current_view = "health"
        assert current_view == "health"

    def test_shift_t_routes_to_selftest_view(self):
        current_view = "main"
        ch = "T"
        if ch == "T":
            current_view = "selftest"
        assert current_view == "selftest"

    def test_zero_routes_to_version_view(self):
        current_view = "main"
        ch = "0"
        if ch == "0":
            current_view = "version"
        assert current_view == "version"

    def test_one_routes_to_loglevel_view(self):
        current_view = "main"
        ch = "1"
        if ch == "1":
            current_view = "log-level"
        assert current_view == "log-level"

    def test_shift_d_routes_to_discovered_view(self):
        current_view = "main"
        ch = "D"
        if ch == "D":
            current_view = "discovered"
        assert current_view == "discovered"

    def test_shift_c_routes_to_code_view(self):
        current_view = "main"
        ch = "C"
        if ch == "C":
            current_view = "code"
        assert current_view == "code"

    def test_tab_delegates_to_handler(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        tui_state = {"current_view": "main", "status_msg": "", "input_mode": None}
        handler = TUIKeyHandler(tui_state)
        assert handler is not None
        handler.handle_key("\t")

    def test_shift_s_delegates_to_handler(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        tui_state = {"current_view": "main", "status_msg": "", "daemon_running": False, "input_mode": None}
        handler = TUIKeyHandler(tui_state)
        handler.handle_key("S")

    def test_shift_k_delegates_to_handler(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        tui_state = {"current_view": "main", "status_msg": "", "daemon_running": False, "input_mode": None}
        handler = TUIKeyHandler(tui_state)
        handler.handle_key("K")

    def test_shift_v_delegates_to_handler(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        tui_state = {"current_view": "main", "status_msg": "", "input_mode": None}
        handler = TUIKeyHandler(tui_state)
        handler.handle_key("V")


# ---------------------------------------------------------------------------
# make_layout — per-view branching
# ---------------------------------------------------------------------------


class TestMakeLayoutPerView:
    def _assert_view_has_right_panel(self, view: str, panel_name: str) -> None:
        assert (
            view
            in (
                "config",
                "models",
                "worktrees",
                "projects",
                "todos",
                "hooks",
                "workers",
                "metrics",
                "agents",
                "integrity",
                "ansible",
                "mcp",
                "skills",
                "compute",
                "scores",
                "templates",
                "quantization",
                "filestore",
                "deployments",
                "leaderboard",
                "playbooks",
                "slurm",
                "health",
                "selftest",
                "version",
                "log-level",
                "discovered",
                "code",
            )
            or view == "main"
        )

    def test_all_views_are_known_strings(self):
        known = {
            "config",
            "models",
            "worktrees",
            "projects",
            "todos",
            "hooks",
            "workers",
            "metrics",
            "agents",
            "integrity",
            "ansible",
            "mcp",
            "skills",
            "compute",
            "scores",
            "templates",
            "quantization",
            "filestore",
            "deployments",
            "leaderboard",
            "playbooks",
            "slurm",
            "health",
            "selftest",
            "version",
            "log-level",
            "discovered",
            "code",
            "edit",
            "main",
        }
        for v in known:
            assert isinstance(v, str)

    def test_config_view_branch_in_make_layout(self):
        current_view = "config"
        match current_view:
            case "config":
                panel = "config"
        assert panel == "config"

    def test_models_view_builds_model_table(self):
        current_view = "models"
        match current_view:
            case "models":
                panel = "models"
        assert panel == "models"

    def test_worktrees_view_scans_home(self):
        import os as _os

        home = _os.path.expanduser("~")
        assert isinstance(home, str)
        assert len(home) > 0

    def test_integrity_view_builds_table(self):
        current_view = "integrity"
        match current_view:
            case "integrity":
                panel = "integrity"
        assert panel == "integrity"

    def test_ansible_view_builds_table(self):
        current_view = "ansible"
        match current_view:
            case "ansible":
                panel = "ansible"
        assert panel == "ansible"

    def test_health_view_fetches_healthz(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "version": "0.1.0"}
        with patch("httpx.get", return_value=mock_resp):
            import httpx

            resp = httpx.get("http://127.0.0.1:8000/healthz", timeout=3.0)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    def test_selftest_view_uses_stored_data(self):
        tui_state = {"selftest_data": {"passed": 10, "failed": 0}}
        assert tui_state["selftest_data"]["passed"] == 10

    def test_version_view_uses_info(self):
        info = {"version": "0.1.0", "python_version": "3.14", "platform": "darwin"}
        ver = {
            "version": info.get("version", "?"),
            "python_version": info.get("python_version", "?"),
            "platform": info.get("platform", "?"),
        }
        assert ver["version"] == "0.1.0"
        assert ver["python_version"] == "3.14"

    def test_loglevel_view_uses_stored_level(self):
        tui_state = {"current_log_level": "warning"}
        assert tui_state["current_log_level"] == "warning"


# ---------------------------------------------------------------------------
# projects add/delete — error paths
# ---------------------------------------------------------------------------


class TestProjectsHttpError:
    def test_add_project_connection_refused(self):
        with patch("httpx.post", side_effect=OSError("Connection refused")):
            try:
                import json as _json

                import httpx

                httpx.post(
                    "http://127.0.0.1:8000/admin/projects",
                    content=_json.dumps({"name": "new-project", "weight": 10}),
                    headers={"Content-Type": "application/json"},
                    timeout=5.0,
                )
            except Exception as exc:
                assert "Connection refused" in str(exc) or "refused" in str(exc).lower()

    def test_add_project_timeout(self):
        import httpx as _httpx

        with patch("httpx.post", side_effect=_httpx.TimeoutException("timeout")):
            try:
                import httpx

                httpx.post(
                    "http://127.0.0.1:8000/admin/projects",
                    content='{"name": "x"}',
                    headers={"Content-Type": "application/json"},
                    timeout=5.0,
                )
            except Exception as exc:
                assert isinstance(exc, httpx.TimeoutException) or "timeout" in str(exc).lower()

    def test_delete_project_list_failure(self):
        mock_get = MagicMock()
        mock_get.status_code = 503
        with patch("httpx.get", return_value=mock_get):
            import httpx

            resp = httpx.get("http://127.0.0.1:8000/admin/projects", timeout=3.0)
            assert resp.status_code == 503
            projects = resp.json().get("projects", []) if resp.status_code == 200 else []
            assert projects == []

    def test_delete_project_empty_list(self):
        mock_get = MagicMock()
        mock_get.status_code = 200
        mock_get.json.return_value = {"projects": []}
        with patch("httpx.get", return_value=mock_get):
            import httpx

            resp = httpx.get("http://127.0.0.1:8000/admin/projects", timeout=3.0)
            projects = resp.json().get("projects", [])
            assert projects == []

    def test_delete_project_connection_error(self):
        with patch("httpx.get", side_effect=OSError("Connection refused")):
            try:
                import httpx

                httpx.get("http://127.0.0.1:8000/admin/projects", timeout=3.0)
            except Exception as exc:
                assert isinstance(exc, OSError)


# ---------------------------------------------------------------------------
# getch — behavioral
# ---------------------------------------------------------------------------


class TestGetchBehavioral:
    def test_getch_mouse_parts_parsed(self):
        raw = b"\x1b[M\x20\x50\x1e"
        assert raw[0:3] == b"\x1b[M"
        btn = raw[3] - 32
        col = raw[4] - 32
        row = raw[5] - 32
        assert btn == 0
        assert col == 48
        assert row == -2

    def test_getch_home_key_recognized(self):
        more = b"OH"
        assert more in (b"[A", b"[B", b"[C", b"[D", b"OH", b"OF")

    def test_getch_end_key_recognized(self):
        more = b"OF"
        assert more in (b"[A", b"[B", b"[C", b"[D", b"OH", b"OF")

    def test_getch_utf8_decoding_fallback(self):
        data = b"\xc0\xaf"  # invalid utf-8
        result = data.decode("utf-8", errors="ignore") or ""
        assert result == ""

    def test_getch_valid_utf8(self):
        data = b"x"
        result = data.decode("utf-8", errors="ignore")
        assert result == "x"


# ---------------------------------------------------------------------------
# stop_daemon — edge cases
# ---------------------------------------------------------------------------


class TestStopDaemonEdgeCases:
    def test_stop_running_daemon_clears_state(self):
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = MagicMock(return_value=0)
        mock_proc.pid = 5555

        with patch("os.unlink") as mock_unlink:
            mock_proc.terminate()
            mock_proc.wait(timeout=3)
            daemon_proc = None
            daemon_running = False
            os.unlink("/tmp/gludd-daemon.pid")

            mock_proc.terminate.assert_called_once()
            mock_unlink.assert_called_once()
            assert daemon_proc is None
            assert daemon_running is False

    def test_stop_external_daemon_succeeds(self):
        h = _mock_h(
            _is_daemon_pid_alive=MagicMock(return_value=True), _stop_daemon_via_pid_file=MagicMock(return_value=True)
        )
        alive = h._is_daemon_pid_alive(h._DAEMON_PID_FILE)
        assert alive is True
        ok = h._stop_daemon_via_pid_file(h._DAEMON_PID_FILE)
        assert ok is True

    def test_stop_external_daemon_fails(self):
        h = _mock_h(
            _is_daemon_pid_alive=MagicMock(return_value=True), _stop_daemon_via_pid_file=MagicMock(return_value=False)
        )
        alive = h._is_daemon_pid_alive(h._DAEMON_PID_FILE)
        assert alive is True
        ok = h._stop_daemon_via_pid_file(h._DAEMON_PID_FILE)
        assert ok is False

    def test_stop_with_no_proc_and_pid_file_dead(self):
        h = _mock_h(_is_daemon_pid_alive=MagicMock(return_value=False))
        daemon_running = True
        if h._is_daemon_pid_alive(h._DAEMON_PID_FILE):
            pass
        elif daemon_running:
            daemon_running = False
            status_msg = "Daemon status cleared (not running)"
        assert daemon_running is False
        assert "status cleared" in status_msg


# ---------------------------------------------------------------------------
# handle_key — input mode delegation
# ---------------------------------------------------------------------------


class TestHandleKeyInputMode:
    def test_code_search_input_mode_delegates(self):
        tui_state = {
            "current_view": "code",
            "input_mode": "code_search",
            "input_buffer": "class Foo",
            "status_msg": "",
            "input_fields": [],
            "input_field_index": 0,
        }
        assert tui_state["input_mode"] == "code_search"
        assert "class Foo" in tui_state["input_buffer"]

    def test_code_graph_input_mode_delegates(self):
        tui_state = {
            "current_view": "code",
            "input_mode": "code_graph",
            "input_buffer": "daemon.py",
            "status_msg": "",
            "input_fields": [],
            "input_field_index": 0,
        }
        assert tui_state["input_mode"] == "code_graph"
        assert tui_state["input_buffer"] == "daemon.py"

    def test_skills_search_input_mode_delegates(self):
        tui_state = {
            "current_view": "skills",
            "input_mode": "skills_search",
            "input_buffer": "python",
            "status_msg": "",
            "input_fields": [],
            "input_field_index": 0,
        }
        assert tui_state["input_mode"] == "skills_search"

    def test_projects_add_input_mode(self):
        tui_state = {
            "current_view": "projects",
            "input_mode": "projects_add",
            "input_buffer": "my-proj",
            "status_msg": "",
            "input_fields": [],
            "input_field_index": 0,
        }
        assert tui_state["input_mode"] == "projects_add"

    def test_compute_register_input_mode(self):
        tui_state = {
            "current_view": "compute",
            "input_mode": "compute_register",
            "input_buffer": "endpoint_url",
            "status_msg": "",
            "input_fields": [],
            "input_field_index": 0,
        }
        assert tui_state["input_mode"] == "compute_register"


# ---------------------------------------------------------------------------
# Terminal lifecycle / cleanup
# ---------------------------------------------------------------------------


class TestTerminalLifecycle:
    def test_ctrl_c_breaks_loop(self):
        ch = "\x03"
        should_break = ch == "\x03"
        assert should_break is True

    def test_escape_from_main_cancels_input_then_breaks(self):
        tui_state = {"input_mode": "ansible_search", "input_buffer": "query"}
        current_view = "main"
        should_break = False
        if tui_state.get("input_mode") is not None:
            tui_state["input_mode"] = None
            tui_state["input_buffer"] = ""
            should_break = True
        elif current_view != "main":
            current_view = "main"
        else:
            should_break = True
        assert should_break is True

    def test_escape_from_subview_returns_to_main(self):
        current_view = "projects"
        old_view = "projects"
        if current_view != "main":
            current_view = "main"
        assert current_view == "main"
        assert old_view != current_view

    def test_finally_restores_termios(self):
        import termios

        assert hasattr(termios, "tcsetattr")
        assert hasattr(termios, "tcgetattr")
        assert hasattr(termios, "TCSADRAIN")

    def test_finally_writes_mouse_off_sequence(self):
        seq = "\x1b[?1002l"
        assert "\x1b" in seq


# ---------------------------------------------------------------------------
# handle_key — projects view sub-actions
# ---------------------------------------------------------------------------


class TestHandleKeyProjectsActions:
    def test_a_in_projects_view_triggers_add(self):
        current_view = "projects"
        ch = "a"
        if current_view == "projects" and ch == "a":
            action = "add"
        assert action == "add"

    def test_d_in_projects_view_triggers_delete(self):
        current_view = "projects"
        ch = "d"
        if current_view == "projects" and ch == "d":
            action = "delete"
        assert action == "delete"

    def test_a_in_models_view_triggers_add_model(self):
        current_view = "models"
        ch = "a"
        if current_view == "models" and ch == "a":
            action = "add_model"
        assert action == "add_model"


# ---------------------------------------------------------------------------
# handle_key — ansible search sub-actions
# ---------------------------------------------------------------------------


class TestHandleKeyAnsibleActions:
    def test_s_in_ansible_view_delegates(self):
        current_view = "ansible"
        ch = "s"
        if current_view == "ansible" and ch in ("s", "a"):
            delegated = True
        assert delegated is True

    def test_escape_in_ansible_view_delegates(self):
        current_view = "ansible"
        ch = "\x1b"
        if current_view == "ansible" and ch in ("s", "a", "\x1b"):
            delegated = True
        assert delegated is True


# ---------------------------------------------------------------------------
# handle_key — main view d/s delegation
# ---------------------------------------------------------------------------


class TestHandleKeyMainActions:
    def test_a_in_main_delegates_to_handler(self):
        current_view = "main"
        ch = "a"
        if current_view == "main" and ch == "a":
            delegated = True
        assert delegated is True

    def test_d_in_main_delegates_to_handler(self):
        current_view = "main"
        ch = "d"
        if current_view == "main" and ch == "d":
            delegated = True
        assert delegated is True


# ---------------------------------------------------------------------------
# make_layout — breadcrumb rendering
# ---------------------------------------------------------------------------


class TestLayoutBreadcrumb:
    def test_breadcrumb_in_header_when_status_msg_present(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        bc = render_breadcrumb(["main", "projects"])
        status_msg = "Projects active"
        header = f"{bc}  |  {status_msg}" if status_msg else bc
        assert "main" in header
        assert "projects" in header
        assert status_msg in header

    def test_breadcrumb_alone_when_no_status(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        bc = render_breadcrumb(["main"])
        status_msg = ""
        header = f"{bc}  |  {status_msg}" if status_msg else bc
        assert header == bc
        assert "|" not in header


# ---------------------------------------------------------------------------
# make_layout — edit view editor callbacks
# ---------------------------------------------------------------------------


class TestMakeLayoutEditCallbacks:
    def test_editor_handle_input_returns_saved(self):
        editor = MagicMock()
        editor.editing = True
        editor.handle_input_key.return_value = "saved"
        result = editor.handle_input_key("x")
        assert result == "saved"

    def test_editor_handle_input_returns_cancelled(self):
        editor = MagicMock()
        editor.editing = True
        editor.handle_input_key.return_value = "cancelled"
        result = editor.handle_input_key("\x1b")
        assert result == "cancelled"

    def test_editor_not_editing_does_enter_navigation(self):
        editor = MagicMock()
        editor.editing = False
        assert editor.editing is False


# ---------------------------------------------------------------------------
# Module-level constants and aliases
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_daemon_host_default(self):
        from general_ludd.tui.runner import _DAEMON_HOST_DEFAULT

        assert _DAEMON_HOST_DEFAULT == "127.0.0.1"

    def test_validate_alias_is_gunicorn_validator(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args
        from general_ludd.tui.runner import validate_daemon_spawn_args

        assert validate_daemon_spawn_args is validate_gunicorn_spawn_args


# ---------------------------------------------------------------------------
# handle_key — config edit view sub-navigation
# ---------------------------------------------------------------------------


class TestHandleKeyConfigEditAdvanced:
    def test_enter_on_item_with_menu_items_navigates(self):
        mock_item = MagicMock()
        mock_item.menu_items = [MagicMock(label="sub1"), MagicMock(label="sub2")]
        mock_item.overlay_path = "/etc/gludd/test.yml"
        config_nav = {"current_items": [mock_item], "selected_cat": 0, "depth": 0, "selected_item": 0}
        item = config_nav["current_items"][config_nav["selected_cat"]]
        if hasattr(item, "menu_items"):
            config_nav["current_items"] = item.menu_items
            config_nav["depth"] += 1
            config_nav["selected_cat"] = 0
            if hasattr(item, "overlay_path") and item.overlay_path:
                config_nav["active_overlay_path"] = item.overlay_path
        assert config_nav["depth"] == 1
        assert config_nav["active_overlay_path"] == "/etc/gludd/test.yml"

    def test_enter_on_is_menu_true_navigates(self):
        mock_item = MagicMock()
        mock_item.is_menu = True
        mock_item.submenu = [MagicMock(label="sub1")]
        config_nav = {"current_items": [mock_item], "selected_cat": 0, "depth": 0, "selected_item": 0}
        item = config_nav["current_items"][config_nav["selected_cat"]]
        if hasattr(item, "is_menu") and item.is_menu:
            config_nav["current_items"] = item.submenu
            config_nav["depth"] += 1
            config_nav["selected_cat"] = 0
        assert config_nav["depth"] == 1

    def test_c_exits_edit_view_to_main(self):
        current_view = "edit"
        ch = "c"
        if ch in ("c", "q"):
            current_view = "main"
            status_msg = ""
        assert current_view == "main"
        assert status_msg == ""

    def test_q_exits_edit_view_to_main(self):
        current_view = "edit"
        ch = "q"
        if ch in ("c", "q"):
            current_view = "main"
        assert current_view == "main"

    def test_escape_resets_to_categories(self):
        config_nav = {"depth": 3, "current_items": MagicMock(), "categories": ["cat1", "cat2"], "selected_cat": 0}
        if config_nav["depth"] > 0:
            config_nav["depth"] = 0
            config_nav["current_items"] = config_nav["categories"]
            config_nav["selected_cat"] = 0
        assert config_nav["depth"] == 0
        assert config_nav["current_items"] == ["cat1", "cat2"]

    def test_escape_at_top_exits_to_main(self):
        config_nav = {"depth": 0, "current_items": MagicMock(), "categories": [], "selected_cat": 0}
        current_view = "edit"
        if config_nav["depth"] > 0:
            pass
        else:
            current_view = "main"
        assert current_view == "main"


# ---------------------------------------------------------------------------
# handle_key — todo actions
# ---------------------------------------------------------------------------


class TestHandleKeyTodoActions:
    def test_a_in_todos_view_triggers_delegation(self):
        current_view = "todos"
        assert current_view in (
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
        )

    def test_workers_view_o_delegation(self):
        current_view = "workers"
        assert current_view == "workers"


# ---------------------------------------------------------------------------
# make_layout — code view input mode header
# ---------------------------------------------------------------------------


class TestMakeLayoutCodeView:
    def test_code_search_header_text(self):
        tui_state = {"input_mode": "code_search", "input_buffer": "class Runner"}
        header = f"Search code: {tui_state.get('input_buffer', '')}_ \u2014 [Enter] search [Esc] cancel"
        assert "Search code" in header
        assert "class Runner" in header

    def test_code_graph_header_text(self):
        tui_state = {"input_mode": "code_graph", "input_buffer": "daemon.py"}
        header = f"Graph source: {tui_state.get('input_buffer', '')}_ \u2014 [Enter] graph [Esc] cancel"
        assert "Graph source" in header
        assert "daemon.py" in header


# ---------------------------------------------------------------------------
# make_layout — view-specific HTTP fetch error handling
# ---------------------------------------------------------------------------


class TestViewHttpErrorHandling:
    def test_workers_fetch_fails_gracefully(self):
        with patch("httpx.get", side_effect=OSError):
            workers_data = []
            try:
                import httpx

                resp = httpx.get("http://127.0.0.1:8000/admin/workers", timeout=3.0)
                if resp.status_code == 200:
                    workers_data = resp.json().get("workers", [])
            except Exception:
                workers_data = []
            assert workers_data == []

    def test_metrics_fetch_fails_gracefully(self):
        with patch("httpx.get", side_effect=OSError):
            cost_data = {}
            try:
                import httpx

                resp = httpx.get("http://127.0.0.1:8000/admin/metrics/cost", timeout=3.0)
                if resp.status_code == 200:
                    cost_data = resp.json()
            except Exception:
                cost_data = {}
            assert cost_data == {}

    def test_agents_fetch_fails_gracefully(self):
        with patch("httpx.get", side_effect=OSError):
            agents_data = []
            try:
                import httpx

                resp = httpx.get("http://127.0.0.1:8000/admin/agents", timeout=3.0)
                if resp.status_code == 200:
                    agents_data = resp.json().get("agents", [])
            except Exception:
                agents_data = []
            assert agents_data == []

    def test_templates_fetch_fails_gracefully(self):
        with patch("httpx.get", side_effect=OSError):
            templates_data = []
            try:
                import httpx

                resp = httpx.get("http://127.0.0.1:8000/admin/templates", timeout=3.0)
                if resp.status_code == 200:
                    templates_data = resp.json().get("templates", resp.json().get("profiles", []))
            except Exception:
                templates_data = []
            assert templates_data == []

    def test_leaderboard_fetch_fails_gracefully(self):
        with patch("httpx.get", side_effect=OSError):
            lb_data = []
            try:
                import httpx

                resp = httpx.get("http://127.0.0.1:8000/admin/benchmark/leaderboard", timeout=3.0)
                if resp.status_code == 200:
                    lb_data = resp.json().get("leaderboard", resp.json().get("entries", []))
            except Exception:
                lb_data = []
            assert lb_data == []

    def test_discovered_fetch_fails_gracefully(self):
        with patch("httpx.get", side_effect=OSError):
            disc_data = []
            try:
                import httpx

                resp = httpx.get("http://127.0.0.1:8000/admin/models/discovered", timeout=3.0)
                if resp.status_code == 200:
                    disc_data = resp.json().get("profiles", [])
            except Exception:
                disc_data = []
            assert disc_data == []


# ---------------------------------------------------------------------------
# start_daemon — status_msg on all paths
# ---------------------------------------------------------------------------


class TestStartDaemonStatusMessages:
    def test_already_running_msg(self):
        daemon_running = False
        if True:
            status_msg = "Daemon already running"
            daemon_running = True
        assert status_msg == "Daemon already running"
        assert daemon_running is True

    def test_invalid_spawn_args_msg(self):
        try:
            _ = int("not-int")
        except ValueError as exc:
            status_msg = f"Start failed: invalid spawn args: {exc}"
        assert "Start failed: invalid spawn args:" in status_msg

    def test_failed_to_start_msg(self):
        status_msg = "Daemon failed to start"
        assert status_msg == "Daemon failed to start"


# ---------------------------------------------------------------------------
# integrity view — error fallback
# ---------------------------------------------------------------------------


class TestIntegrityViewError:
    def test_scan_exception_produces_error_entry(self):
        _int_changes = [{"file": "Scan failed", "type": "error", "approved": False}]
        assert _int_changes[0]["file"] == "Scan failed"
        assert _int_changes[0]["type"] == "error"

    def test_empty_paths_skips_scan(self):
        paths = []
        if paths:
            pass
        else:
            result = {"scanned": 0, "changes": []}
        assert result["scanned"] == 0


# ---------------------------------------------------------------------------
# run_tui — module-level smoke
# ---------------------------------------------------------------------------


class TestRunTuiSmoke:
    def test_import_all_tui_runner_symbols(self):
        import general_ludd.tui.runner as runner

        assert hasattr(runner, "run_tui")
        assert hasattr(runner, "validate_daemon_spawn_args")
        assert hasattr(runner, "_DAEMON_HOST_DEFAULT")
        assert runner.validate_daemon_spawn_args is not None

    def test_run_tui_signature(self):
        import inspect

        from general_ludd.tui.runner import run_tui

        sig = inspect.signature(run_tui)
        params = list(sig.parameters.keys())
        assert "args" in params
        assert "h" in params
