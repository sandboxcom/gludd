"""Tests for TUI view actions: hooks register/delete, integrity approve/reject,
models remove, ansible install, skills install."""

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
        "hooks_data": [
            {"hook_id": "h1", "event_name": "todo.completed", "hook_type": "webhook", "url": "http://hook.example.com"},
            {"hook_id": "h2", "event_name": "job.failed", "hook_type": "webhook", "url": "http://hook2.example.com"},
        ],
        "integrity_changes": [
            {"path": "/etc/config.yml", "status": "modified"},
            {"path": "/etc/secrets.yml", "status": "added"},
        ],
        "models_data": [
            {"model_id": "gpt-4", "provider": "openai"},
            {"model_id": "claude-3", "provider": "anthropic"},
        ],
        "ansible_search_results": [
            {"name": "nginx", "type": "role"},
        ],
        "skills_search_results": [
            {"name": "tdd-discipline", "id": "skill-1"},
        ],
        "selected_hook_idx": 0,
        "selected_integrity_idx": 0,
        "selected_model_idx": 0,
    }
    return TUIKeyHandler(state), state


class TestHooksRegisterAction:
    def test_r_key_in_hooks_view_enters_register_mode(self) -> None:
        handler, state = _make_handler("hooks")
        handler.handle_key("r")
        assert state["input_mode"] == "hooks_register"
        assert state["status_msg"] == "Register hook — enter event_name"

    def test_hooks_register_typing_appends(self) -> None:
        handler, state = _make_handler("hooks")
        handler.handle_key("r")
        handler.handle_key("t")
        handler.handle_key("o")
        handler.handle_key("d")
        assert state["input_buffer"] == "tod"

    def test_hooks_register_enter_advances_to_url(self) -> None:
        handler, state = _make_handler("hooks")
        handler.handle_key("r")
        for ch in "todo.completed":
            handler.handle_key(ch)
        handler.handle_key("\r")
        assert state["input_field_index"] == 1
        assert state["status_msg"] == "Register hook — enter url"

    def test_hooks_register_final_enter_submits(self) -> None:
        handler, state = _make_handler("hooks")
        handler.handle_key("r")
        for ch in "todo.completed":
            handler.handle_key(ch)
        handler.handle_key("\r")
        for ch in "http://hook.example.com":
            handler.handle_key(ch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"hook_id": "h3"}
            handler.handle_key("\r")
        mock_post.assert_called_once()
        assert "h3" in state["status_msg"]
        assert state["input_mode"] is None

    def test_hooks_register_escape_cancels(self) -> None:
        handler, state = _make_handler("hooks")
        handler.handle_key("r")
        handler.handle_key("\x1b")
        assert state["input_mode"] is None
        assert "cancel" in state["status_msg"].lower()

    def test_hooks_register_backspace(self) -> None:
        handler, state = _make_handler("hooks")
        handler.handle_key("r")
        handler.handle_key("a")
        handler.handle_key("b")
        handler.handle_key("\x7f")
        assert state["input_buffer"] == "a"


class TestHooksDeleteAction:
    def test_d_key_in_hooks_view_deletes_selected(self) -> None:
        handler, state = _make_handler("hooks")
        with patch("httpx.delete") as mock_delete:
            mock_delete.return_value.status_code = 200
            mock_delete.return_value.json.return_value = {"removed": "h1"}
            handler.handle_key("d")
        mock_delete.assert_called_once()
        assert "h1" in state["status_msg"]

    def test_delete_hook_empty_list(self) -> None:
        handler, state = _make_handler("hooks")
        state["hooks_data"] = []
        handler.handle_key("d")
        assert "no hook" in state["status_msg"].lower()

    def test_delete_hook_error(self) -> None:
        handler, state = _make_handler("hooks")
        with patch("httpx.delete", side_effect=Exception("connection failed")):
            handler.handle_key("d")
        assert "error" in state["status_msg"].lower()


class TestIntegrityScanAction:
    def test_s_key_in_integrity_view_triggers_scan(self) -> None:
        handler, state = _make_handler("integrity")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"changes": [{"path": "/a", "status": "modified"}]}
            handler.handle_key("s")
        mock_post.assert_called_once()
        assert "scan" in state["status_msg"].lower() or "1 change" in state["status_msg"]

    def test_scan_error(self) -> None:
        handler, state = _make_handler("integrity")
        with patch("httpx.post", side_effect=Exception("fail")):
            handler.handle_key("s")
        assert "error" in state["status_msg"].lower()


class TestIntegrityApproveAction:
    def test_a_key_in_integrity_view_approves_selected(self) -> None:
        handler, state = _make_handler("integrity")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"path": "/etc/config.yml", "status": "approved"}
            handler.handle_key("a")
        mock_post.assert_called_once()
        assert "approved" in state["status_msg"].lower() or "config.yml" in state["status_msg"]

    def test_approve_no_changes(self) -> None:
        handler, state = _make_handler("integrity")
        state["integrity_changes"] = []
        handler.handle_key("a")
        assert "no change" in state["status_msg"].lower()

    def test_approve_error(self) -> None:
        handler, state = _make_handler("integrity")
        with patch("httpx.post", side_effect=Exception("fail")):
            handler.handle_key("a")
        assert "error" in state["status_msg"].lower()


class TestIntegrityRejectAction:
    def test_r_key_in_integrity_view_rejects_selected(self) -> None:
        handler, state = _make_handler("integrity")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"path": "/etc/config.yml", "status": "rejected"}
            handler.handle_key("r")
        mock_post.assert_called_once()
        assert "reject" in state["status_msg"].lower() or "config.yml" in state["status_msg"]

    def test_reject_no_changes(self) -> None:
        handler, state = _make_handler("integrity")
        state["integrity_changes"] = []
        handler.handle_key("r")
        assert "no change" in state["status_msg"].lower()


class TestModelsRemoveAction:
    def test_x_key_in_models_view_removes_selected(self) -> None:
        handler, state = _make_handler("models")
        with patch("httpx.delete") as mock_delete:
            mock_delete.return_value.status_code = 200
            mock_delete.return_value.json.return_value = {"removed": "gpt-4"}
            handler.handle_key("x")
        mock_delete.assert_called_once()
        assert "gpt-4" in state["status_msg"]

    def test_remove_model_empty_list(self) -> None:
        handler, state = _make_handler("models")
        state["models_data"] = []
        handler.handle_key("x")
        assert "no model" in state["status_msg"].lower()

    def test_remove_model_error(self) -> None:
        handler, state = _make_handler("models")
        with patch("httpx.delete", side_effect=Exception("fail")):
            handler.handle_key("x")
        assert "error" in state["status_msg"].lower()


class TestAnsibleInstallAction:
    def test_i_key_in_ansible_view_enters_install_mode(self) -> None:
        handler, state = _make_handler("ansible")
        handler.handle_key("i")
        assert state["input_mode"] == "ansible_install"
        assert "install" in state["status_msg"].lower()

    def test_ansible_install_typing_appends(self) -> None:
        handler, state = _make_handler("ansible")
        handler.handle_key("i")
        handler.handle_key("n")
        handler.handle_key("g")
        handler.handle_key("i")
        handler.handle_key("n")
        handler.handle_key("x")
        assert state["input_buffer"] == "nginx"

    def test_ansible_install_enter_submits(self) -> None:
        handler, state = _make_handler("ansible")
        handler.handle_key("i")
        for ch in "nginx":
            handler.handle_key(ch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"name": "nginx", "status": "installed"}
            handler.handle_key("\r")
        mock_post.assert_called_once()
        assert "nginx" in state["status_msg"]
        assert state["input_mode"] is None

    def test_ansible_install_escape_cancels(self) -> None:
        handler, state = _make_handler("ansible")
        handler.handle_key("i")
        handler.handle_key("\x1b")
        assert state["input_mode"] is None

    def test_ansible_install_backspace(self) -> None:
        handler, state = _make_handler("ansible")
        handler.handle_key("i")
        handler.handle_key("a")
        handler.handle_key("\x7f")
        assert state["input_buffer"] == ""

    def test_ansible_install_error(self) -> None:
        handler, state = _make_handler("ansible")
        handler.handle_key("i")
        for ch in "nginx":
            handler.handle_key(ch)
        with patch("httpx.post", side_effect=Exception("fail")):
            handler.handle_key("\r")
        assert "error" in state["status_msg"].lower()


class TestSkillsInstallAction:
    def test_i_key_in_skills_view_enters_install_mode(self) -> None:
        handler, state = _make_handler("skills")
        handler.handle_key("i")
        assert state["input_mode"] == "skills_install"
        assert "install" in state["status_msg"].lower()

    def test_skills_install_typing(self) -> None:
        handler, state = _make_handler("skills")
        handler.handle_key("i")
        handler.handle_key("t")
        handler.handle_key("d")
        handler.handle_key("d")
        assert state["input_buffer"] == "tdd"

    def test_skills_install_enter_submits(self) -> None:
        handler, state = _make_handler("skills")
        handler.handle_key("i")
        for ch in "tdd-discipline":
            handler.handle_key(ch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"installed": "/path/to/skill.md"}
            handler.handle_key("\r")
        mock_post.assert_called_once()
        assert state["input_mode"] is None

    def test_skills_install_escape_cancels(self) -> None:
        handler, state = _make_handler("skills")
        handler.handle_key("i")
        handler.handle_key("\x1b")
        assert state["input_mode"] is None

    def test_skills_install_error(self) -> None:
        handler, state = _make_handler("skills")
        handler.handle_key("i")
        for ch in "bad-skill":
            handler.handle_key(ch)
        with patch("httpx.post", side_effect=Exception("fail")):
            handler.handle_key("\r")
        assert "error" in state["status_msg"].lower()
