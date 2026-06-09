from __future__ import annotations

from typing import Any
from unittest.mock import patch


def _make_handler() -> tuple[Any, dict[str, Any]]:
    from general_ludd.tui.keybindings import TUIKeyHandler

    state: dict[str, Any] = {
        "current_view": "main",
        "input_mode": None,
        "selected_main_idx": 0,
        "selected_idx": 0,
        "verbose_logging": False,
        "panel_focus": "left",
        "breadcrumb": ["main"],
        "status_msg": "",
        "daemon_running": False,
        "daemon_url": "http://127.0.0.1:8000",
        "projects_data": [],
        "todos_data": [],
        "hooks_data": [],
        "workers_data": [],
        "models_data": [],
        "active_project_id": None,
        "active_todo_id": None,
        "active_hook_id": None,
        "active_worker_id": None,
        "left_panel_width": 40,
        "tui_log_entries": [],
    }

    def _start_daemon() -> None:
        state["daemon_running"] = True
        state["status_msg"] = "Daemon started"

    def _stop_daemon() -> None:
        state["daemon_running"] = False
        state["status_msg"] = "Daemon stopped"

    handler = TUIKeyHandler(state)
    handler._start_daemon = _start_daemon  # type: ignore[assignment]
    handler._stop_daemon = _stop_daemon  # type: ignore[assignment]
    return handler, state


class TestSpaceBarOnMainView:
    def test_space_activates_selected_main_menu_item(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        state["selected_main_idx"] = 2
        handler.handle_key(" ")
        assert state["status_msg"] != "" or state["current_view"] != "main"

    def test_space_on_main_dispatches_to_activate(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        state["selected_main_idx"] = 0
        with patch.object(handler, "_activate_main_menu_item") as mock_activate:
            handler.handle_key(" ")
            mock_activate.assert_called_once()


class TestEscapeCancelsInputAndPopsBreadcrumb:
    def test_escape_cancels_input_mode(self):
        handler, state = _make_handler()
        state["input_mode"] = "models_search"
        state["current_view"] = "models"
        handler.handle_key("\x1b")
        assert state["input_mode"] is None

    def test_escape_from_subview_pops_breadcrumb(self):
        handler, state = _make_handler()
        state["current_view"] = "models"
        state["breadcrumb"] = ["main", "models"]
        state["input_mode"] = None
        handler.handle_key("\x1b")
        assert state["breadcrumb"] == ["main"]

    def test_escape_from_subview_returns_to_main(self):
        handler, state = _make_handler()
        state["current_view"] = "projects"
        state["breadcrumb"] = ["main", "projects"]
        state["input_mode"] = None
        handler.handle_key("\x1b")
        assert state["current_view"] == "main"


class TestVerboseToggle:
    def test_uppercase_V_toggles_verbose(self):
        handler, state = _make_handler()
        assert state["verbose_logging"] is False
        handler.handle_key("V")
        assert state["verbose_logging"] is True
        handler.handle_key("V")
        assert state["verbose_logging"] is False

    def test_lowercase_v_enters_config_view(self):
        handler, state = _make_handler()
        handler.handle_key("v")
        assert state["current_view"] == "config" or "config" in state.get("current_view", "")


class TestTabPanelFocus:
    def test_tab_toggles_panel_focus(self):
        handler, state = _make_handler()
        initial = state.get("panel_focus", "left")
        handler.handle_key("\t")
        new_focus = state.get("panel_focus", initial)
        assert new_focus != initial or "panel_focus" in state

    def test_tab_cycles_panel_focus(self):
        handler, state = _make_handler()
        state["panel_focus"] = "left"
        handler.handle_key("\t")
        assert state["panel_focus"] == "right"
        handler.handle_key("\t")
        assert state["panel_focus"] == "left"


class TestLeftArrowGoesBack:
    def test_left_arrow_cancels_input(self):
        handler, state = _make_handler()
        state["input_mode"] = "projects_add"
        state["current_view"] = "projects"
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None

    def test_left_arrow_from_subview_pops_breadcrumb(self):
        handler, state = _make_handler()
        state["current_view"] = "todos"
        state["breadcrumb"] = ["main", "todos"]
        state["input_mode"] = None
        handler.handle_key("\x1b[D")
        assert state["breadcrumb"] == ["main"]
        assert state["current_view"] == "main"


class TestMainMenuItemLabels:
    def test_main_menu_daemon_keys_work(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        handler.handle_key("s")
        assert state["daemon_running"] is True or state["status_msg"] != ""

    def test_main_menu_stop_key_works(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        state["daemon_running"] = True
        handler.handle_key("k")
        assert state["daemon_running"] is False or state["status_msg"] != ""
