"""Test TUI keybindings for models add, ansible galaxy search, and dispatch mode toggle.

These tests verify the TUI handle_key function calls the correct daemon endpoints
when the new keybindings are triggered.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx


def _make_handle_key_context(daemon_url: str = "http://localhost:8000"):
    from general_ludd.tui.config_editor import ConfigEditor

    editor = ConfigEditor()
    cats = editor.get_categories()
    config_nav = {
        "editor": editor,
        "categories": cats,
        "selected_cat": 0,
        "selected_item": 0,
        "depth": 0,
        "editing_value": False,
        "current_items": cats,
        "active_overlay_path": "",
    }

    state = {
        "current_view": "main",
        "daemon_running": False,
        "status_msg": "",
        "config_nav": config_nav,
        "daemon_url": daemon_url,
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
    }

    from general_ludd.tui.keybindings import TUIKeyHandler

    handler = TUIKeyHandler(state)
    return handler, state


class TestModelsAddKeybinding:
    def test_a_key_in_models_view_triggers_add_mode(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "models"
        result = handler.handle_key("a")
        assert result is True
        assert state["input_mode"] == "models_add"
        assert state["input_field_index"] == 0
        assert len(state["input_fields"]) == 3

    def test_models_add_collects_model_id_then_provider_then_api_base(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "models"
        handler.handle_key("a")
        assert state["input_fields"][0]["label"] == "model_id"
        assert state["input_fields"][1]["label"] == "provider"
        assert state["input_fields"][2]["label"] == "api_base"

    def test_models_add_typing_builds_buffer(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "models"
        handler.handle_key("a")
        handler.handle_key("m")
        handler.handle_key("y")
        handler.handle_key("-")
        handler.handle_key("m")
        handler.handle_key("o")
        handler.handle_key("d")
        handler.handle_key("e")
        handler.handle_key("l")
        assert state["input_buffer"] == "my-model"

    def test_models_add_enter_advances_fields(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "models"
        handler.handle_key("a")
        for ch in "test-model":
            handler.handle_key(ch)
        handler.handle_key("\r")
        assert state["input_field_index"] == 1
        assert state["input_fields"][0]["value"] == "test-model"
        assert state["input_buffer"] == ""

    def test_models_add_final_enter_calls_post_admin_models(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "models"
        handler.handle_key("a")
        for ch in "test-model":
            handler.handle_key(ch)
        handler.handle_key("\r")
        for ch in "openai":
            handler.handle_key(ch)
        handler.handle_key("\r")
        for ch in "https://api.example.com":
            handler.handle_key(ch)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"model_id": "test-model", "profile": {}}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            handler.handle_key("\r")
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "/admin/models" in call_args.args[0] or "/admin/models" in str(call_args)
            body = call_args.kwargs.get("json") or (call_args[1].get("json") if len(call_args) > 1 else None)
            assert body is not None
            assert body["model_id"] == "test-model"
            assert body["provider"] == "openai"
            assert body["api_base"] == "https://api.example.com"

    def test_models_add_escape_cancels(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "models"
        handler.handle_key("a")
        assert state["input_mode"] == "models_add"
        handler.handle_key("\x1b")
        assert state["input_mode"] is None
        assert state["current_view"] == "models"

    def test_models_add_backspace_removes_char(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "models"
        handler.handle_key("a")
        handler.handle_key("a")
        handler.handle_key("b")
        handler.handle_key("c")
        handler.handle_key("\x7f")
        assert state["input_buffer"] == "ab"


class TestAnsibleGalaxySearchKeybinding:
    def test_a_key_in_main_opens_ansible_view(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "main"
        result = handler.handle_key("a")
        assert result is True
        assert state["current_view"] == "ansible"

    def test_s_key_in_ansible_view_triggers_search_mode(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "ansible"
        handler.handle_key("s")
        assert state["input_mode"] == "ansible_search"
        assert state["input_buffer"] == ""

    def test_ansible_search_typing_builds_query(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "ansible"
        handler.handle_key("s")
        for ch in "nginx":
            handler.handle_key(ch)
        assert state["input_buffer"] == "nginx"

    def test_ansible_search_enter_calls_get_admin_ansible_search(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "ansible"
        handler.handle_key("s")
        for ch in "nginx":
            handler.handle_key(ch)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": "nginx",
            "type": "role",
            "results": [{"name": "nginxinc.nginx", "description": "Install nginx"}],
        }
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            handler.handle_key("\r")
            mock_get.assert_called_once()
            call_url = mock_get.call_args.args[0]
            assert "/admin/ansible/search" in call_url
            params = mock_get.call_args.kwargs.get("params") or {}
            assert params.get("query") == "nginx"

    def test_ansible_search_escape_cancels(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "ansible"
        handler.handle_key("s")
        handler.handle_key("\x1b")
        assert state["input_mode"] is None

    def test_ansible_search_backspace(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "ansible"
        handler.handle_key("s")
        handler.handle_key("x")
        handler.handle_key("\x7f")
        assert state["input_buffer"] == ""

    def test_ansible_view_escape_returns_to_main(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "ansible"
        handler.handle_key("\x1b")
        assert state["current_view"] == "main"

    def test_ansible_view_a_key_toggles_back(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "ansible"
        handler.handle_key("a")
        assert state["current_view"] == "main"


class TestDispatchModeToggle:
    def test_d_key_in_main_cycles_dispatch_mode(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "main"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"dispatch_mode": "passive_external"}
        with patch("httpx.put", return_value=mock_resp) as mock_put:
            result = handler.handle_key("d")
            assert result is True
            mock_put.assert_called_once()
            call_url = mock_put.call_args.args[0]
            assert "/admin/dispatch/mode" in call_url

    def test_dispatch_toggle_cycles_through_modes(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "main"
        modes_seen = []
        for expected_mode in ["passive_external", "worktree_monitor", "active"]:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"dispatch_mode": expected_mode}
            with patch("httpx.put", return_value=mock_resp) as mock_put:
                handler.handle_key("d")
                body = mock_put.call_args.kwargs.get("json") or {}
                modes_seen.append(body.get("mode"))
        assert modes_seen == ["passive_external", "worktree_monitor", "active"]

    def test_dispatch_toggle_error_sets_status_msg(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "main"
        with patch("httpx.put", side_effect=httpx.ConnectError("refused")):
            handler.handle_key("d")
            assert "error" in state["status_msg"].lower() or "fail" in state["status_msg"].lower()

    def test_dispatch_toggle_shows_mode_in_status(self):
        handler, state = _make_handle_key_context()
        state["current_view"] = "main"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"dispatch_mode": "passive_external"}
        with patch("httpx.put", return_value=mock_resp):
            handler.handle_key("d")
            assert "passive_external" in state["status_msg"]
