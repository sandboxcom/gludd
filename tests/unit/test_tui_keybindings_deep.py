"""Deep unit tests for TUI keybinding handler — untested classes and methods.

Covers: handle_key navigation, handle_key_down/up, _get_selection_keys,
_activate_main_menu_item, _activate_selected, _handle_text_input,
_submit_text_input, _toggle_verbose, _handle_text_search_input,
_cycle_dispatch_mode, and escape/backspace handling across views.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.tui.keybindings import (
    DISPATCH_MODES,
    TUIKeyHandler,
    _handle_text_input,
    _submit_text_input,
    build_gunicorn_cmd,
    validate_gunicorn_spawn_args,
)

ESC = "\033"
DOWN = ESC + "[B"
UP = ESC + "[A"
LEFT = ESC + "[D"
BS = "\177"
TAB = "\t"
CR = "\r"
SP = " "


# ── Module-level helpers ────────────────────────────────────────────────────


class TestHandleTextInput:
    def test_escape_cancels_input_mode(self):
        state = {"input_mode": "models_add", "input_buffer": "hello"}
        assert _handle_text_input(state, ESC) is True
        assert state["input_mode"] is None
        assert state["input_buffer"] == ""
        assert "Cancelled" in state["status_msg"]

    def test_left_arrow_cancels_input_mode(self):
        state = {"input_mode": "projects_add", "input_buffer": "x"}
        assert _handle_text_input(state, LEFT) is True
        assert state["input_mode"] is None
        assert state["input_buffer"] == ""
        assert "Cancelled" in state["status_msg"]

    def test_backspace_removes_last_char(self):
        state = {"input_mode": "models_add", "input_buffer": "abc"}
        assert _handle_text_input(state, BS) is True
        assert state["input_buffer"] == "ab"

    def test_backspace_on_empty_buffer_is_noop_charwise(self):
        state = {"input_mode": "models_add", "input_buffer": ""}
        assert _handle_text_input(state, BS) is True
        assert state["input_buffer"] == ""

    def test_regular_char_returns_false(self):
        state = {"input_mode": "models_add", "input_buffer": ""}
        assert _handle_text_input(state, "x") is False
        assert state["input_buffer"] == ""


class TestSubmitTextInput:
    def test_clears_input_mode_and_buffer(self):
        state = {"input_mode": "models_add", "input_buffer": "stuff"}
        _submit_text_input(state)
        assert state["input_mode"] is None
        assert state["input_buffer"] == ""


# ── TUIKeyHandler helpers ──────────────────────────────────────────────────


def _handler(**overrides):
    state = {
        "current_view": "main",
        "daemon_url": "http://127.0.0.1:8000",
        "status_msg": "",
        "daemon_running": False,
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "panel_focus": "left",
        "projects_data": [],
        "hooks_data": [],
        "models_data": [],
        "todos_data": [],
        "workers_data": [],
        "agents_data": [],
        "integrity_changes": [],
        "selected_main_idx": 0,
        "selected_project_idx": 0,
        "selected_hook_idx": 0,
        "selected_model_idx": 0,
        "selected_todo_idx": 0,
        "selected_worker_idx": 0,
        "selected_agent_idx": 0,
        "selected_integrity_idx": 0,
        "dispatch_mode": "active",
        "active_project_id": "",
        "active_todo_id": "",
        "active_hook_id": "",
        "active_worker_id": "",
        "active_model_id": "",
        "health_data": {},
        "selftest_data": {},
        "current_log_level": "info",
        "verbose_logging": False,
        "last_reload": False,
        "last_discover": False,
        "last_scan": False,
        "last_report": False,
        "last_bootstrap": False,
        "last_loglevel": False,
        "discovered_data": [],
        "ansible_builtins": [],
        "filestore_binaries": [],
        "code_graph_data": {},
        "code_search_results": [],
    }
    state.update(overrides)
    return TUIKeyHandler(state), state


# ── TUIKeyHandler._get_selection_keys ──────────────────────────────────────


class TestGetSelectionKeys:
    def test_projects_mapping(self):
        assert TUIKeyHandler._get_selection_keys("projects") == (
            "selected_project_idx",
            "projects_data",
        )

    def test_hooks_mapping(self):
        assert TUIKeyHandler._get_selection_keys("hooks") == (
            "selected_hook_idx",
            "hooks_data",
        )

    def test_models_mapping(self):
        assert TUIKeyHandler._get_selection_keys("models") == (
            "selected_model_idx",
            "models_data",
        )

    def test_integrity_mapping(self):
        assert TUIKeyHandler._get_selection_keys("integrity") == (
            "selected_integrity_idx",
            "integrity_changes",
        )

    def test_todos_mapping(self):
        assert TUIKeyHandler._get_selection_keys("todos") == (
            "selected_todo_idx",
            "todos_data",
        )

    def test_workers_mapping(self):
        assert TUIKeyHandler._get_selection_keys("workers") == (
            "selected_worker_idx",
            "workers_data",
        )

    def test_agents_mapping(self):
        assert TUIKeyHandler._get_selection_keys("agents") == (
            "selected_agent_idx",
            "agents_data",
        )

    def test_unknown_view_returns_empty(self):
        assert TUIKeyHandler._get_selection_keys("nonexistent") == ("", "")


# ── TUIKeyHandler.handle_key_down / handle_key_up ─────────────────────────


class TestHandleKeyDown:
    def test_main_menu_wraps_down(self):
        h, s = _handler(selected_main_idx=0)
        h.handle_key_down()
        assert s["selected_main_idx"] == 1
        assert "Selected:" in s["status_msg"]

    def test_main_menu_wraps_to_zero(self):
        n = len(TUIKeyHandler.MAIN_MENU_ITEMS)
        h, s = _handler(selected_main_idx=n - 1)
        h.handle_key_down()
        assert s["selected_main_idx"] == 0

    def test_subview_wraps_items(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[
                {"name": "a", "project_id": "p1"},
                {"name": "b", "project_id": "p2"},
            ],
            selected_project_idx=0,
        )
        h.handle_key_down()
        assert s["selected_project_idx"] == 1
        assert "Selected:" in s["status_msg"]

    def test_subview_wraps_to_top(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[
                {"name": "a", "project_id": "p1"},
                {"name": "b", "project_id": "p2"},
            ],
            selected_project_idx=1,
        )
        h.handle_key_down()
        assert s["selected_project_idx"] == 0

    def test_empty_items_no_effect(self):
        h, s = _handler(current_view="projects", projects_data=[], selected_project_idx=0)
        s["status_msg"] = "before"
        h.handle_key_down()
        assert s["selected_project_idx"] == 0
        assert s["status_msg"] == "before"

    def test_unknown_view_no_effect(self):
        h, s = _handler(current_view="nonexistent", status_msg="before")
        h.handle_key_down()
        assert s["status_msg"] == "before"


class TestHandleKeyUp:
    def test_main_menu_wraps_up(self):
        h, s = _handler(selected_main_idx=0)
        n = len(TUIKeyHandler.MAIN_MENU_ITEMS)
        h.handle_key_up()
        assert s["selected_main_idx"] == n - 1

    def test_main_menu_decrements(self):
        h, s = _handler(selected_main_idx=2)
        h.handle_key_up()
        assert s["selected_main_idx"] == 1

    def test_subview_wraps_up(self):
        h, s = _handler(
            current_view="todos",
            todos_data=[
                {"title": "t1", "todo_id": "1"},
                {"title": "t2", "todo_id": "2"},
            ],
            selected_todo_idx=0,
        )
        h.handle_key_up()
        assert s["selected_todo_idx"] == 1

    def test_subview_decrements(self):
        h, s = _handler(
            current_view="todos",
            todos_data=[
                {"title": "t1", "todo_id": "1"},
                {"title": "t2", "todo_id": "2"},
            ],
            selected_todo_idx=1,
        )
        h.handle_key_up()
        assert s["selected_todo_idx"] == 0

    def test_empty_items_no_effect(self):
        h, s = _handler(current_view="todos", todos_data=[], selected_todo_idx=0)
        s["status_msg"] = "before"
        h.handle_key_up()
        assert s["selected_todo_idx"] == 0
        assert s["status_msg"] == "before"


# ── TUIKeyHandler.handle_key — navigation ─────────────────────────────────


class TestHandleKeyNavigation:
    def test_arrow_down_calls_handle_key_down(self):
        h, s = _handler(selected_main_idx=0)
        assert h.handle_key(DOWN) is True
        assert s["selected_main_idx"] == 1

    def test_arrow_up_calls_handle_key_up(self):
        n = len(TUIKeyHandler.MAIN_MENU_ITEMS)
        h, s = _handler(selected_main_idx=0)
        assert h.handle_key(UP) is True
        assert s["selected_main_idx"] == n - 1

    def test_tab_toggles_panel_focus_left_to_right(self):
        h, s = _handler(panel_focus="left")
        assert h.handle_key(TAB) is True
        assert s["panel_focus"] == "right"

    def test_tab_toggles_panel_focus_right_to_left(self):
        h, s = _handler(panel_focus="right")
        assert h.handle_key(TAB) is True
        assert s["panel_focus"] == "left"

    def test_escape_from_subview_returns_to_main(self):
        h, s = _handler(current_view="projects")
        assert h.handle_key(ESC) is True
        assert s["current_view"] == "main"
        assert s["status_msg"] == ""

    def test_escape_from_main_is_noop_on_view(self):
        h, s = _handler(current_view="main")
        assert h.handle_key(ESC) is True
        assert s["current_view"] == "main"

    def test_escape_cancels_input_mode_in_subview(self):
        h, s = _handler(current_view="projects", input_mode="projects_add", input_buffer="x")
        assert h.handle_key(ESC) is True
        assert s["input_mode"] is None
        assert s["input_buffer"] == ""
        assert "Cancelled" in s["status_msg"]

    def test_left_arrow_from_subview_returns_to_main(self):
        h, s = _handler(current_view="models")
        assert h.handle_key(LEFT) is True
        assert s["current_view"] == "main"
        assert s["status_msg"] == ""

    def test_left_arrow_from_main_is_noop_on_view(self):
        h, s = _handler(current_view="main")
        h.handle_key(LEFT)
        assert s["current_view"] == "main"

    def test_left_arrow_cancels_input_mode(self):
        h, s = _handler(current_view="main", input_mode="models_add", input_buffer="x")
        assert h.handle_key(LEFT) is True
        assert s["input_mode"] is None
        assert "Cancelled" in s["status_msg"]


# ── TUIKeyHandler.handle_key — view toggling ──────────────────────────────


class TestHandleKeyViewToggle:
    def test_p_key_opens_projects(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("p") is True
        assert s["current_view"] == "projects"

    def test_p_key_closes_projects(self):
        h, s = _handler(current_view="projects")
        assert h.handle_key("p") is True
        assert s["current_view"] == "main"

    def test_m_key_opens_models(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("m") is True
        assert s["current_view"] == "models"

    def test_m_key_closes_models(self):
        h, s = _handler(current_view="models")
        assert h.handle_key("m") is True
        assert s["current_view"] == "main"

    def test_t_key_opens_todos(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("t") is True
        assert s["current_view"] == "todos"

    def test_t_key_closes_todos(self):
        h, s = _handler(current_view="todos")
        assert h.handle_key("t") is True
        assert s["current_view"] == "main"

    def test_o_key_opens_workers(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("o") is True
        assert s["current_view"] == "workers"

    def test_h_key_opens_hooks(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("h") is True
        assert s["current_view"] == "hooks"

    def test_g_key_opens_agents(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("g") is True
        assert s["current_view"] == "agents"

    def test_x_key_opens_metrics(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("x") is True
        assert s["current_view"] == "metrics"

    def test_w_key_opens_worktrees(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("w") is True
        assert s["current_view"] == "worktrees"

    def test_j_key_opens_skills(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("j") is True
        assert s["current_view"] == "skills"

    def test_e_key_opens_compute(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("e") is True
        assert s["current_view"] == "compute"

    def test_b_key_opens_scores(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("b") is True
        assert s["current_view"] == "scores"

    def test_l_key_opens_templates(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("l") is True
        assert s["current_view"] == "templates"

    def test_n_key_opens_quantization(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("n") is True
        assert s["current_view"] == "quantization"

    def test_f_key_opens_filestore(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("f") is True
        assert s["current_view"] == "filestore"

    def test_z_key_opens_deployments(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("z") is True
        assert s["current_view"] == "deployments"

    def test_C_key_opens_code(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("C") is True
        assert s["current_view"] == "code"

    def test_L_key_opens_slurm(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("L") is True
        assert s["current_view"] == "slurm"


# ── TUIKeyHandler.handle_key — action keys ────────────────────────────────


class TestHandleKeyActions:
    def test_capital_V_toggles_verbose(self):
        h, s = _handler(current_view="main", verbose_logging=False)
        assert h.handle_key("V") is True
        assert s["verbose_logging"] is True
        assert "ON" in s["status_msg"]

    def test_capital_V_toggles_verbose_off(self):
        h, s = _handler(current_view="main", verbose_logging=True)
        assert h.handle_key("V") is True
        assert s["verbose_logging"] is False
        assert "OFF" in s["status_msg"]

    def test_space_activates_main_menu_item(self):
        h, _s = _handler(current_view="main", selected_main_idx=0)
        with patch.object(h, "_start_daemon") as mock_start:
            assert h.handle_key(SP) is True
            mock_start.assert_called_once()

    def test_enter_activates_main_menu_item(self):
        h, _s = _handler(current_view="main", selected_main_idx=1)
        with patch.object(h, "_stop_daemon") as mock_stop:
            assert h.handle_key(CR) is True
            mock_stop.assert_called_once()

    def test_space_activates_selected_in_subview(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[{"project_id": "p1", "name": "Project 1"}],
            selected_project_idx=0,
        )
        assert h.handle_key(SP) is True
        assert s["active_project_id"] == "p1"
        assert "p1" in s["status_msg"]

    def test_enter_activates_selected_in_subview(self):
        h, s = _handler(
            current_view="todos",
            todos_data=[{"todo_id": "t99", "title": "Fix it"}],
            selected_todo_idx=0,
        )
        assert h.handle_key(CR) is True
        assert s["active_todo_id"] == "t99"

    def test_R_on_main_reloads_daemon(self):
        h, _s = _handler(current_view="main")
        with patch.object(h, "_reload_daemon") as mock_reload:
            assert h.handle_key("R") is True
            mock_reload.assert_called_once()

    def test_capital_S_starts_daemon(self):
        h, _s = _handler(current_view="main")
        with patch.object(h, "_start_daemon") as mock_start:
            assert h.handle_key("S") is True
            mock_start.assert_called_once()

    def test_capital_K_stops_daemon(self):
        h, _s = _handler(current_view="main")
        with patch.object(h, "_stop_daemon") as mock_stop:
            assert h.handle_key("K") is True
            mock_stop.assert_called_once()

    def test_s_on_main_starts_daemon(self):
        h, _s = _handler(current_view="main")
        with patch.object(h, "_start_daemon") as mock_start:
            assert h.handle_key("s") is True
            mock_start.assert_called_once()

    def test_k_on_main_stops_daemon(self):
        h, _s = _handler(current_view="main")
        with patch.object(h, "_stop_daemon") as mock_stop:
            assert h.handle_key("k") is True
            mock_stop.assert_called_once()

    def test_v_on_main_opens_config(self):
        h, s = _handler(current_view="main")
        assert h.handle_key("v") is True
        assert s["current_view"] == "config"


# ── TUIKeyHandler._activate_main_menu_item ────────────────────────────────


class TestActivateMainMenuItem:
    def test_daemon_start_calls_start_daemon(self):
        h, _s = _handler(selected_main_idx=0)
        with patch.object(h, "_start_daemon") as mock:
            h._activate_main_menu_item()
            mock.assert_called_once()

    def test_daemon_stop_calls_stop_daemon(self):
        h, _s = _handler(selected_main_idx=1)
        with patch.object(h, "_stop_daemon") as mock:
            h._activate_main_menu_item()
            mock.assert_called_once()

    def test_refresh_sets_status(self):
        h, s = _handler(selected_main_idx=2)
        h._activate_main_menu_item()
        assert s["status_msg"] == "Refreshed"

    def test_integrity_switches_view(self):
        h, s = _handler(selected_main_idx=3)
        h._activate_main_menu_item()
        assert s["current_view"] == "integrity"

    def test_config_switches_view(self):
        h, s = _handler(selected_main_idx=4)
        h._activate_main_menu_item()
        assert s["current_view"] == "config"

    def test_edit_switches_view(self):
        h, s = _handler(selected_main_idx=5)
        h._activate_main_menu_item()
        assert s["current_view"] == "edit"

    def test_models_switches_view(self):
        h, s = _handler(selected_main_idx=6)
        h._activate_main_menu_item()
        assert s["current_view"] == "models"

    def test_dispatch_calls_cycle(self):
        h, _s = _handler(selected_main_idx=15)
        with patch.object(h, "_cycle_dispatch_mode") as mock:
            h._activate_main_menu_item()
            mock.assert_called_once()

    def test_reload_calls_reload_daemon(self):
        h, _s = _handler(selected_main_idx=24)
        with patch.object(h, "_reload_daemon") as mock:
            h._activate_main_menu_item()
            mock.assert_called_once()

    def test_out_of_bounds_index_no_effect(self):
        h, s = _handler(selected_main_idx=999)
        s["status_msg"] = "before"
        h._activate_main_menu_item()
        assert s["status_msg"] == "before"


# ── TUIKeyHandler._activate_selected ──────────────────────────────────────


class TestActivateSelected:
    def test_projects_activates_selected(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[{"project_id": "p42", "name": "alpha"}],
            selected_project_idx=0,
        )
        h._activate_selected("projects")
        assert s["active_project_id"] == "p42"
        assert "p42" in s["status_msg"]

    def test_projects_out_of_bounds_no_effect(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[{"project_id": "p1"}],
            selected_project_idx=2,
        )
        s["status_msg"] = "before"
        h._activate_selected("projects")
        assert s["status_msg"] == "before"

    def test_todos_activates_selected(self):
        h, s = _handler(
            current_view="todos",
            todos_data=[{"todo_id": "t88", "title": "fix"}],
            selected_todo_idx=0,
        )
        h._activate_selected("todos")
        assert s["active_todo_id"] == "t88"
        assert "t88" in s["status_msg"]

    def test_hooks_activates_selected(self):
        h, s = _handler(
            current_view="hooks",
            hooks_data=[{"hook_id": "hk1", "url": "http://x"}],
            selected_hook_idx=0,
        )
        h._activate_selected("hooks")
        assert s["active_hook_id"] == "hk1"

    def test_workers_activates_selected(self):
        h, s = _handler(
            current_view="workers",
            workers_data=[{"worker_id": "wk7", "status": "idle"}],
            selected_worker_idx=0,
        )
        h._activate_selected("workers")
        assert s["active_worker_id"] == "wk7"

    def test_models_activates_selected(self):
        h, s = _handler(
            current_view="models",
            models_data=[{"model_id": "gpt-4", "provider": "openai"}],
            selected_model_idx=0,
        )
        h._activate_selected("models")
        assert s["active_model_id"] == "gpt-4"

    def test_unknown_view_noop(self):
        h, s = _handler(current_view="nonexistent")
        s["status_msg"] = "before"
        h._activate_selected("nonexistent")
        assert s["status_msg"] == "before"


# ── TUIKeyHandler._handle_text_search_input ───────────────────────────────


class TestHandleTextSearchInput:
    def test_escape_cancels_search(self):
        h, s = _handler(input_mode="models_search", input_buffer="gpt")
        assert (
            h._handle_text_search_input(ESC, "models_search", "/admin/models/search", "models_search_results") is True
        )
        assert s["input_mode"] is None

    def test_backspace_removes_char(self):
        h, s = _handler(input_mode="models_search", input_buffer="abc")
        assert h._handle_text_search_input(BS, "models_search", "/admin/models/search", "models_search_results") is True
        assert s["input_buffer"] == "ab"

    def test_typing_appends_to_buffer(self):
        h, s = _handler(input_mode="models_search", input_buffer="")
        assert (
            h._handle_text_search_input("x", "models_search", "/admin/models/search", "models_search_results") is True
        )
        assert s["input_buffer"] == "x"

    def test_enter_submits_search_and_stores_results(self):
        h, s = _handler(
            current_view="models",
            input_mode="models_search",
            input_buffer="gpt-4",
            daemon_url="http://127.0.0.1:8000",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"name": "gpt-4"}]}
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            result = h._handle_text_search_input(CR, "models_search", "/admin/models/search", "models_search_results")
            assert result is True
            mock_get.assert_called_once()
            assert s["input_mode"] is None
            assert "Found 1" in s["status_msg"]
            assert s["models_search_results"] == [{"name": "gpt-4"}]

    def test_enter_on_http_error_sets_status(self):
        h, s = _handler(
            input_mode="models_search",
            input_buffer="x",
            daemon_url="http://127.0.0.1:8000",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp):
            h._handle_text_search_input(CR, "models_search", "/admin/models/search", "models_search_results")
            assert "failed" in s["status_msg"].lower()

    def test_enter_on_connection_error_sets_status(self):
        h, s = _handler(
            input_mode="models_search",
            input_buffer="x",
            daemon_url="http://127.0.0.1:8000",
        )
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            h._handle_text_search_input(CR, "models_search", "/admin/models/search", "models_search_results")
            assert "error" in s["status_msg"].lower()


# ── TUIKeyHandler._toggle_verbose ─────────────────────────────────────────


class TestToggleVerbose:
    def test_turns_on(self):
        h, s = _handler(verbose_logging=False)
        h._toggle_verbose()
        assert s["verbose_logging"] is True
        assert "ON" in s["status_msg"]

    def test_turns_off(self):
        h, s = _handler(verbose_logging=True)
        h._toggle_verbose()
        assert s["verbose_logging"] is False
        assert "OFF" in s["status_msg"]


# ── TUIKeyHandler._cycle_dispatch_mode ────────────────────────────────────


class TestCycleDispatchMode:
    def test_cycles_to_next_mode_and_stores_it(self):
        h, s = _handler(current_view="main", dispatch_mode="active")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"dispatch_mode": "passive_external"}
        with patch("httpx.put", return_value=mock_resp) as mock_put:
            assert h._cycle_dispatch_mode() is True
            mock_put.assert_called_once()
            body = mock_put.call_args.kwargs.get("json") or {}
            assert body["mode"] == "passive_external"
            assert s["dispatch_mode"] == "passive_external"
            assert "passive_external" in s["status_msg"]

    def test_cycles_through_all_modes_in_order(self):
        h, s = _handler(current_view="main", dispatch_mode="active")
        for expected in ["passive_external", "worktree_monitor", "active"]:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"dispatch_mode": expected}
            with patch("httpx.put", return_value=mock_resp):
                h._cycle_dispatch_mode()
            assert s["dispatch_mode"] == expected

    def test_unknown_mode_defaults_to_first(self):
        h, s = _handler(current_view="main", dispatch_mode="bogus")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"dispatch_mode": DISPATCH_MODES[0]}
        with patch("httpx.put", return_value=mock_resp):
            h._cycle_dispatch_mode()
        assert s["dispatch_mode"] == DISPATCH_MODES[0]

    def test_http_error_sets_status(self):
        h, s = _handler(current_view="main", dispatch_mode="active")
        with patch("httpx.put", side_effect=httpx.ConnectError("refused")):
            h._cycle_dispatch_mode()
            assert "error" in s["status_msg"].lower() or "fail" in s["status_msg"].lower()


# ── TUIKeyHandler handle_key — subview action keys ────────────────────────


class TestHandleKeySubviewActions:
    def test_models_view_a_enters_add_mode(self):
        h, s = _handler(current_view="models")
        assert h.handle_key("a") is True
        assert s["input_mode"] == "models_add"

    def test_models_view_s_enters_search_mode(self):
        h, s = _handler(current_view="models")
        assert h.handle_key("s") is True
        assert s["input_mode"] == "models_search"

    def test_models_view_x_removes_selected(self):
        h, _s = _handler(
            current_view="models",
            models_data=[{"model_id": "m1"}],
            selected_model_idx=0,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.delete", return_value=mock_resp) as mock_del:
            h.handle_key("x")
            mock_del.assert_called_once()
            assert "/admin/models/m1" in mock_del.call_args.args[0]

    def test_projects_view_a_enters_add_mode(self):
        h, s = _handler(current_view="projects")
        assert h.handle_key("a") is True
        assert s["input_mode"] == "projects_add"

    def test_projects_view_d_deletes_selected(self):
        h, _s = _handler(current_view="projects")
        with patch.object(h, "delete_selected_project") as mock_del:
            h.handle_key("d")
            mock_del.assert_called_once()

    def test_todos_view_a_enters_add_mode(self):
        h, s = _handler(current_view="todos")
        assert h.handle_key("a") is True
        assert s["input_mode"] == "todos_add"

    def test_workers_view_p_pings_workers(self):
        h, _s = _handler(current_view="workers")
        with patch.object(h, "_ping_workers") as mock_ping:
            h.handle_key("p")
            mock_ping.assert_called_once()

    def test_hooks_view_r_enters_register_mode(self):
        h, s = _handler(current_view="hooks")
        assert h.handle_key("r") is True
        assert s["input_mode"] == "hooks_register"

    def test_hooks_view_d_deletes_selected(self):
        h, _s = _handler(current_view="hooks")
        with patch.object(h, "_delete_selected_hook") as mock_del:
            h.handle_key("d")
            mock_del.assert_called_once()

    def test_skills_view_s_enters_search_mode(self):
        h, s = _handler(current_view="skills")
        assert h.handle_key("s") is True
        assert s["input_mode"] == "skills_search"

    def test_skills_view_i_enters_install_mode(self):
        h, s = _handler(current_view="skills")
        assert h.handle_key("i") is True
        assert s["input_mode"] == "skills_install"

    def test_mcp_view_s_enters_search_mode(self):
        h, s = _handler(current_view="mcp")
        assert h.handle_key("s") is True
        assert s["input_mode"] == "mcp_search"

    def test_compute_view_a_enters_register_mode(self):
        h, s = _handler(current_view="compute")
        assert h.handle_key("a") is True
        assert s["input_mode"] == "compute_register"

    def test_templates_view_r_refreshes(self):
        h, _s = _handler(current_view="templates")
        with patch.object(h, "_refresh_templates") as mock_ref:
            h.handle_key("r")
            mock_ref.assert_called_once()

    def test_quantization_view_d_detects(self):
        h, _s = _handler(current_view="quantization")
        with patch.object(h, "_detect_quantization") as mock_det:
            h.handle_key("d")
            mock_det.assert_called_once()

    def test_filestore_view_b_loads_binaries(self):
        h, _s = _handler(current_view="filestore")
        with patch.object(h, "_filestore_binaries") as mock_bin:
            h.handle_key("b")
            mock_bin.assert_called_once()

    def test_filestore_view_capital_B_bootstraps(self):
        h, _s = _handler(current_view="filestore")
        with patch.object(h, "_filestore_bootstrap") as mock_boot:
            h.handle_key("B")
            mock_boot.assert_called_once()

    def test_health_view_r_refreshes(self):
        h, _s = _handler(current_view="health")
        with patch.object(h, "_health_refresh") as mock_ref:
            h.handle_key("r")
            mock_ref.assert_called_once()

    def test_selftest_view_r_runs(self):
        h, _s = _handler(current_view="selftest")
        with patch.object(h, "_selftest_run") as mock_run:
            h.handle_key("r")
            mock_run.assert_called_once()

    def test_log_level_view_c_cycles(self):
        h, _s = _handler(current_view="log-level")
        with patch.object(h, "_loglevel_cycle") as mock_cycle:
            h.handle_key("c")
            mock_cycle.assert_called_once()

    def test_discovered_view_r_refreshes(self):
        h, _s = _handler(current_view="discovered")
        with patch.object(h, "_discovered_refresh") as mock_ref:
            h.handle_key("r")
            mock_ref.assert_called_once()

    def test_code_view_s_enters_search_mode(self):
        h, s = _handler(current_view="code")
        assert h.handle_key("s") is True
        assert s["input_mode"] == "code_search"

    def test_code_view_g_enters_graph_mode(self):
        h, s = _handler(current_view="code")
        assert h.handle_key("g") is True
        assert s["input_mode"] == "code_graph"

    def test_integrity_view_s_scans(self):
        h, _s = _handler(current_view="integrity")
        with patch.object(h, "_integrity_scan") as mock:
            h.handle_key("s")
            mock.assert_called_once()

    def test_integrity_view_a_approves(self):
        h, _s = _handler(current_view="integrity")
        with patch.object(h, "_integrity_approve") as mock:
            h.handle_key("a")
            mock.assert_called_once()

    def test_integrity_view_r_rejects(self):
        h, _s = _handler(current_view="integrity")
        with patch.object(h, "_integrity_reject") as mock:
            h.handle_key("r")
            mock.assert_called_once()

    def test_integrity_view_p_shows_report(self):
        h, _s = _handler(current_view="integrity")
        with patch.object(h, "_integrity_report") as mock:
            h.handle_key("p")
            mock.assert_called_once()

    def test_models_view_d_discovers(self):
        h, _s = _handler(current_view="models")
        with patch.object(h, "_models_discover") as mock:
            h.handle_key("d")
            mock.assert_called_once()

    def test_worktrees_view_s_scans(self):
        h, _s = _handler(current_view="worktrees")
        with patch.object(h, "_worktree_scan") as mock:
            h.handle_key("s")
            mock.assert_called_once()

    def test_ansible_view_s_enters_search_mode(self):
        h, s = _handler(current_view="ansible")
        assert h.handle_key("s") is True
        assert s["input_mode"] == "ansible_search"

    def test_ansible_view_i_enters_install_mode(self):
        h, s = _handler(current_view="ansible")
        assert h.handle_key("i") is True
        assert s["input_mode"] == "ansible_install"

    def test_ansible_view_b_builtins(self):
        h, _s = _handler(current_view="ansible")
        with patch.object(h, "_ansible_builtins") as mock:
            h.handle_key("b")
            mock.assert_called_once()

    def test_projects_view_w_sets_weight(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[{"name": "proj1", "project_id": "p1"}],
            selected_project_idx=0,
        )
        assert h.handle_key("w") is True
        assert s["input_mode"] == "projects_set_weight"
        assert "weight" in s["status_msg"].lower()


# ── TUIKeyHandler delete_selected_project ─────────────────────────────────


class TestDeleteSelectedProject:
    def test_empty_projects_shows_message(self):
        h, s = _handler(current_view="projects", projects_data=[])
        h.delete_selected_project()
        assert "No projects" in s["status_msg"]

    def test_deletes_selected_project(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[{"project_id": "p99", "name": "test"}],
            selected_project_idx=0,
            daemon_url="http://127.0.0.1:8000",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.delete", return_value=mock_resp) as mock_del:
            h.delete_selected_project()
            mock_del.assert_called_once()
            assert "/admin/projects/p99" in mock_del.call_args.args[0]
            assert "Removed p99" in s["status_msg"]

    def test_delete_failure_sets_status(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[{"project_id": "p99"}],
            selected_project_idx=0,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.delete", return_value=mock_resp):
            h.delete_selected_project()
            assert "failed" in s["status_msg"].lower()

    def test_delete_error_sets_status(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[{"project_id": "p99"}],
            selected_project_idx=0,
        )
        with patch("httpx.delete", side_effect=httpx.ConnectError("refused")):
            h.delete_selected_project()
            assert "error" in s["status_msg"].lower()

    def test_out_of_bounds_index_clamped(self):
        h, s = _handler(
            current_view="projects",
            projects_data=[{"project_id": "p1"}, {"project_id": "p2"}],
            selected_project_idx=5,
            daemon_url="http://127.0.0.1:8000",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.delete", return_value=mock_resp):
            h.delete_selected_project()
        assert "Removed p2" in s["status_msg"]


# ── TUIKeyHandler input field handlers ────────────────────────────────────


class TestHandleProjectsAddInput:
    def test_escape_cancels(self):
        h, s = _handler(input_mode="projects_add", input_buffer="stuff")
        assert h._handle_projects_add_input(ESC) is True
        assert s["input_mode"] is None
        assert "cancelled" in s["status_msg"].lower()

    def test_backspace_removes_char(self):
        h, s = _handler(input_mode="projects_add", input_buffer="xyz")
        assert h._handle_projects_add_input(BS) is True
        assert s["input_buffer"] == "xy"

    def test_typing_appends(self):
        h, s = _handler(input_mode="projects_add", input_buffer="")
        h._handle_projects_add_input("h")
        assert s["input_buffer"] == "h"

    def test_enter_advances_field(self):
        h, s = _handler(
            input_mode="projects_add",
            input_buffer="myproj",
            input_field_index=0,
            input_fields=[
                {"label": "name", "value": ""},
                {"label": "weight", "value": ""},
            ],
        )
        assert h._handle_projects_add_input(CR) is True
        assert s["input_field_index"] == 1
        assert s["input_fields"][0]["value"] == "myproj"
        assert s["input_buffer"] == ""

    def test_enter_on_last_field_submits(self):
        h, s = _handler(
            input_mode="projects_add",
            input_buffer="42",
            input_field_index=1,
            input_fields=[
                {"label": "name", "value": "myproj"},
                {"label": "weight", "value": ""},
            ],
            daemon_url="http://127.0.0.1:8000",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"project_id": "p-new"}
        with patch("httpx.post", return_value=mock_resp):
            assert h._handle_projects_add_input(CR) is True
            assert s["input_mode"] is None
            assert "Project added" in s["status_msg"]


class TestHandleProjectsSetWeightInput:
    def test_escape_cancels(self):
        h, s = _handler(input_mode="projects_set_weight", input_buffer="50")
        assert h._handle_projects_set_weight_input(ESC) is True
        assert s["input_mode"] is None
        assert "cancelled" in s["status_msg"].lower()

    def test_valid_weight_sets_via_put(self):
        h, s = _handler(
            input_mode="projects_set_weight",
            input_buffer="75",
            projects_data=[{"project_id": "p1", "name": "x"}],
            selected_project_idx=0,
            daemon_url="http://127.0.0.1:8000",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.put", return_value=mock_resp) as mock_put:
            assert h._handle_projects_set_weight_input(CR) is True
            mock_put.assert_called_once()
            assert mock_put.call_args.kwargs["json"]["weight"] == 75.0
            assert "Weight set" in s["status_msg"]

    def test_invalid_weight_reports_error(self):
        h, s = _handler(
            input_mode="projects_set_weight",
            input_buffer="abc",
            projects_data=[{"project_id": "p1"}],
            selected_project_idx=0,
        )
        assert h._handle_projects_set_weight_input(CR) is True
        assert s["input_mode"] is None
        assert "Invalid" in s["status_msg"]


class TestHandleModelsAddInput:
    def test_enter_advances_fields(self):
        h, s = _handler(
            input_mode="models_add",
            input_buffer="gpt-4",
            input_field_index=0,
            input_fields=[
                {"label": "model_id", "value": ""},
                {"label": "provider", "value": ""},
                {"label": "api_base", "value": ""},
            ],
        )
        assert h._handle_models_add_input(CR) is True
        assert s["input_field_index"] == 1
        assert s["input_fields"][0]["value"] == "gpt-4"
        assert s["input_buffer"] == ""

    def test_final_enter_submits(self):
        h, s = _handler(
            input_mode="models_add",
            input_buffer="https://api.x.com",
            input_field_index=2,
            input_fields=[
                {"label": "model_id", "value": "gpt-4"},
                {"label": "provider", "value": "openai"},
                {"label": "api_base", "value": ""},
            ],
            daemon_url="http://127.0.0.1:8000",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"model_id": "gpt-4"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            assert h._handle_models_add_input(CR) is True
            mock_post.assert_called_once()
            assert s["input_mode"] is None
            assert "Model added" in s["status_msg"]

    def test_escape_cancels(self):
        h, s = _handler(input_mode="models_add", input_buffer="x")
        assert h._handle_models_add_input(ESC) is True
        assert s["input_mode"] is None


# ── Module-level validate/build helpers re-coverage ────────────────────────


class TestValidateGunicornSpawnArgsModule:
    def test_bool_port_rejected(self):
        with pytest.raises(ValueError):
            validate_gunicorn_spawn_args(host="0.0.0.0", port=True, workers=1)

    def test_bool_workers_rejected(self):
        with pytest.raises(ValueError):
            validate_gunicorn_spawn_args(host="0.0.0.0", port=8000, workers=False)

    def test_null_byte_in_path_rejected(self, tmp_path):
        confine = tmp_path / "root"
        confine.mkdir()
        with pytest.raises(ValueError, match="null"):
            validate_gunicorn_spawn_args(
                host="0.0.0.0",
                port=8000,
                workers=1,
                paths=["a\x00b"],
                confine_root=str(confine),
            )

    def test_non_list_paths_rejected(self):
        with pytest.raises(ValueError, match="paths"):
            validate_gunicorn_spawn_args(host="0.0.0.0", port=8000, workers=1, paths="not_a_list")

    def test_path_inside_confinement_allowed(self, tmp_path):
        confine = tmp_path / "root"
        confine.mkdir()
        safe = confine / "allowed.py"
        safe.write_text("")
        validate_gunicorn_spawn_args(
            host="0.0.0.0",
            port=8000,
            workers=1,
            paths=[str(safe)],
            confine_root=str(confine),
        )


class TestBuildGunicornCmdModule:
    def test_with_log_level(self):
        cmd = build_gunicorn_cmd(host="127.0.0.1", port=9000, workers=1, log_level="debug")
        assert "--log-level" in cmd
        assert "debug" in cmd

    def test_ipv6_host(self):
        cmd = build_gunicorn_cmd(host="::1", port=8000, workers=2)
        assert "--bind" in cmd
        b = cmd.index("--bind")
        assert cmd[b + 1] == "::1:8000"

    def test_hostname_host(self):
        cmd = build_gunicorn_cmd(host="localhost", port=8000, workers=2)
        b = cmd.index("--bind")
        assert cmd[b + 1] == "localhost:8000"


# ── handle_key — input mode dispatch ──────────────────────────────────────


class TestHandleKeyInputModeAll:
    def test_models_add_input_dispatched(self):
        h, _s = _handler(input_mode="models_add", input_buffer="")
        with patch.object(h, "_handle_models_add_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_models_search_input_dispatched(self):
        h, _s = _handler(input_mode="models_search", input_buffer="")
        with patch.object(h, "_handle_text_search_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x", "models_search", "/admin/models/search", "models_search_results")

    def test_ansible_search_input_dispatched(self):
        h, _s = _handler(input_mode="ansible_search", input_buffer="")
        with patch.object(h, "_handle_ansible_search_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_projects_add_input_dispatched(self):
        h, _s = _handler(input_mode="projects_add", input_buffer="")
        with patch.object(h, "_handle_projects_add_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_projects_set_weight_input_dispatched(self):
        h, _s = _handler(input_mode="projects_set_weight", input_buffer="")
        with patch.object(h, "_handle_projects_set_weight_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_mcp_search_input_dispatched(self):
        h, _s = _handler(input_mode="mcp_search", input_buffer="")
        with patch.object(h, "_handle_text_search_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x", "mcp_search", "/admin/mcp/search", "mcp_search_results")

    def test_skills_search_input_dispatched(self):
        h, _s = _handler(input_mode="skills_search", input_buffer="")
        with patch.object(h, "_handle_text_search_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x", "skills_search", "/admin/skills/search", "skills_search_results")

    def test_compute_register_input_dispatched(self):
        h, _s = _handler(input_mode="compute_register", input_buffer="")
        with patch.object(h, "_handle_compute_register_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_todos_add_input_dispatched(self):
        h, _s = _handler(input_mode="todos_add", input_buffer="")
        with patch.object(h, "_handle_todos_add_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_hooks_register_input_dispatched(self):
        h, _s = _handler(input_mode="hooks_register", input_buffer="")
        with patch.object(h, "_handle_hooks_register_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_ansible_install_input_dispatched(self):
        h, _s = _handler(input_mode="ansible_install", input_buffer="")
        with patch.object(h, "_handle_ansible_install_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_skills_install_input_dispatched(self):
        h, _s = _handler(input_mode="skills_install", input_buffer="")
        with patch.object(h, "_handle_skills_install_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")

    def test_code_search_input_dispatched(self):
        h, _s = _handler(input_mode="code_search", input_buffer="")
        with patch.object(h, "_handle_text_search_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x", "code_search", "/admin/code/search", "code_search_results")

    def test_code_graph_input_dispatched(self):
        h, _s = _handler(input_mode="code_graph", input_buffer="")
        with patch.object(h, "_handle_code_graph_input", return_value=True) as mock:
            h.handle_key("x")
            mock.assert_called_once_with("x")
