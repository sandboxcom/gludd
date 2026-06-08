from __future__ import annotations

from typing import Any
from unittest.mock import patch


def _make_handler(view: str = "main") -> tuple[Any, dict[str, Any]]:
    from general_ludd.tui.keybindings import TUIKeyHandler

    state: dict[str, Any] = {
        "current_view": view,
        "daemon_running": True,
        "status_msg": "",
        "daemon_url": "http://localhost:8000",
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "dispatch_mode": "active",
        "hooks_data": [],
        "integrity_changes": [],
        "models_data": [
            {"model_id": "gpt-4", "provider": "openai"},
        ],
        "ansible_search_results": [],
        "skills_search_results": [],
        "projects_data": [
            {"project_id": "p1", "name": "alpha", "weight": 10},
            {"project_id": "p2", "name": "beta", "weight": 20},
            {"project_id": "p3", "name": "gamma", "weight": 30},
        ],
        "selected_hook_idx": 0,
        "selected_integrity_idx": 0,
        "selected_model_idx": 0,
        "selected_project_idx": 0,
    }
    return TUIKeyHandler(state), state


class TestDaemonStartDoesNotInterceptSearchKey:
    def test_s_key_in_ansible_view_does_NOT_start_daemon(self) -> None:
        handler, state = _make_handler("ansible")
        with patch("httpx.post") as mock_post:
            handler.handle_key("s")
        for call_args in mock_post.call_args_list:
            url = str(call_args[0][0]) if call_args[0] else ""
            assert "gunicorn" not in url and "start" not in url
        assert state["input_mode"] == "ansible_search"

    def test_s_key_in_mcp_view_does_NOT_start_daemon(self) -> None:
        handler, state = _make_handler("mcp")
        with patch("httpx.post") as mock_post:
            handler.handle_key("s")
        for call_args in mock_post.call_args_list:
            url = str(call_args[0][0]) if call_args[0] else ""
            assert "gunicorn" not in url and "start" not in url
        assert state["input_mode"] == "mcp_search"

    def test_s_key_in_skills_view_does_NOT_start_daemon(self) -> None:
        handler, state = _make_handler("skills")
        with patch("httpx.post") as mock_post:
            handler.handle_key("s")
        for call_args in mock_post.call_args_list:
            url = str(call_args[0][0]) if call_args[0] else ""
            assert "gunicorn" not in url and "start" not in url
        assert state["input_mode"] == "skills_search"

    def test_s_key_in_models_view_does_NOT_start_daemon(self) -> None:
        handler, state = _make_handler("models")
        with patch("httpx.post") as mock_post:
            handler.handle_key("s")
        for call_args in mock_post.call_args_list:
            url = str(call_args[0][0]) if call_args[0] else ""
            assert "gunicorn" not in url and "start" not in url
        assert state["input_mode"] == "models_search"

    def test_shift_S_starts_daemon_from_main(self) -> None:
        handler, state = _make_handler("main")
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {}
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value.poll.return_value = None
                mock_popen.return_value.pid = 12345
                handler.handle_key("S")
        assert "Daemon started" in state["status_msg"] or "Daemon already running" in state["status_msg"]


class TestArrowKeyNavigation:
    def test_arrow_down_in_projects_moves_selection(self) -> None:
        handler, state = _make_handler("projects")
        assert state["selected_project_idx"] == 0
        handler.handle_key("\x1b[B")
        assert state["selected_project_idx"] == 1

    def test_arrow_up_in_projects_moves_selection(self) -> None:
        handler, state = _make_handler("projects")
        state["selected_project_idx"] = 1
        handler.handle_key("\x1b[A")
        assert state["selected_project_idx"] == 0

    def test_arrow_down_wraps_in_projects(self) -> None:
        handler, state = _make_handler("projects")
        state["selected_project_idx"] = 2
        handler.handle_key("\x1b[B")
        assert state["selected_project_idx"] == 0


class TestEnterKeyAction:
    def test_enter_in_main_does_not_crash(self) -> None:
        handler, _state = _make_handler("main")
        result = handler.handle_key("\r")
        assert result is True

    def test_enter_in_projects_view_activates_selected(self) -> None:
        handler, state = _make_handler("projects")
        state["selected_project_idx"] = 1
        handler.handle_key("\r")
        assert state.get("active_project_id") == "p2"


class TestDaemonStatsDisplayed:
    def test_daemon_table_shows_pid_when_running(self) -> None:
        from general_ludd.cli import _build_daemon_table

        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                "pid": 12345,
                "requests_total": 42,
                "responses_total": 40,
                "memory_mb": 128.5,
                "uptime_s": 300,
            }
            table = _build_daemon_table(True, "http://localhost:8000", "main")
        assert len(table.rows) > 3
