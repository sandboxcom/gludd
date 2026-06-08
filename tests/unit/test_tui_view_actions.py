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


class TestTextSearchInput:
    def test_models_search_typing_and_submit(self) -> None:
        handler, state = _make_handler("models")
        handler.handle_key("s")
        assert state["input_mode"] == "models_search"
        for ch in "gpt":
            handler.handle_key(ch)
        assert state["input_buffer"] == "gpt"
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"results": [{"name": "gpt-4"}]}
            handler.handle_key("\r")
        assert "Found 1" in state["status_msg"]

    def test_models_search_error(self) -> None:
        handler, state = _make_handler("models")
        handler.handle_key("s")
        for ch in "x":
            handler.handle_key(ch)
        with patch("httpx.get", side_effect=Exception("fail")):
            handler.handle_key("\r")
        assert "error" in state["status_msg"].lower()

    def test_models_search_no_results(self) -> None:
        handler, state = _make_handler("models")
        handler.handle_key("s")
        for ch in "z":
            handler.handle_key(ch)
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"results": []}
            handler.handle_key("\r")
        assert "Found 0" in state["status_msg"]

    def test_models_search_http_error(self) -> None:
        handler, state = _make_handler("models")
        handler.handle_key("s")
        for ch in "z":
            handler.handle_key(ch)
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 500
            handler.handle_key("\r")
        assert "failed" in state["status_msg"].lower()

    def test_models_search_escape_cancels(self) -> None:
        handler, state = _make_handler("models")
        handler.handle_key("s")
        handler.handle_key("\x1b")
        assert state["input_mode"] is None

    def test_models_search_backspace(self) -> None:
        handler, state = _make_handler("models")
        handler.handle_key("s")
        for ch in "gp":
            handler.handle_key(ch)
        handler.handle_key("\x7f")
        assert state["input_buffer"] == "g"

    def test_mcp_search_submit(self) -> None:
        handler, state = _make_handler("mcp")
        handler.handle_key("s")
        for ch in "web":
            handler.handle_key(ch)
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"servers": [{"name": "webhook"}]}
            handler.handle_key("\r")
        assert "Found 1" in state["status_msg"]

    def test_skills_search_submit(self) -> None:
        handler, state = _make_handler("skills")
        handler.handle_key("s")
        for ch in "tdd":
            handler.handle_key(ch)
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"skills": [{"name": "tdd"}]}
            handler.handle_key("\r")
        assert "Found 1" in state["status_msg"]


class TestTodosAddInput:
    def test_todos_add_full_flow(self) -> None:
        handler, state = _make_handler("todos")
        handler.handle_key("a")
        assert state["input_mode"] == "todos_add"
        for ch in "My Task":
            handler.handle_key(ch)
        handler.handle_key("\r")
        assert "priority" in state["status_msg"].lower()
        for ch in "3":
            handler.handle_key(ch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"todo_id": "t1"}
            handler.handle_key("\r")
        assert "t1" in state["status_msg"]

    def test_todos_add_error(self) -> None:
        handler, state = _make_handler("todos")
        handler.handle_key("a")
        for ch in "Task":
            handler.handle_key(ch)
        handler.handle_key("\r")
        for ch in "bad":
            handler.handle_key(ch)
        with patch("httpx.post", side_effect=Exception("fail")):
            handler.handle_key("\r")
        assert "error" in state["status_msg"].lower()

    def test_todos_add_http_error(self) -> None:
        handler, state = _make_handler("todos")
        handler.handle_key("a")
        for ch in "Task":
            handler.handle_key(ch)
        handler.handle_key("\r")
        handler.handle_key("\r")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 500
            handler.handle_key("\r")
        assert "failed" in state["status_msg"].lower() or "error" in state["status_msg"].lower()

    def test_todos_add_escape_cancels(self) -> None:
        handler, state = _make_handler("todos")
        handler.handle_key("a")
        handler.handle_key("\x1b")
        assert state["input_mode"] is None

    def test_todos_add_backspace(self) -> None:
        handler, state = _make_handler("todos")
        handler.handle_key("a")
        handler.handle_key("X")
        handler.handle_key("\x7f")
        assert state["input_buffer"] == ""


class TestComputeRegisterInput:
    def test_compute_register_full_flow(self) -> None:
        handler, state = _make_handler("compute")
        handler.handle_key("a")
        assert state["input_mode"] == "compute_register"
        for ch in "http://gpu:8000":
            handler.handle_key(ch)
        handler.handle_key("\r")
        assert "provider" in state["status_msg"].lower()
        for ch in "nvidia":
            handler.handle_key(ch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"endpoint_id": "e1"}
            handler.handle_key("\r")
        mock_post.assert_called_once()

    def test_compute_register_error(self) -> None:
        handler, state = _make_handler("compute")
        handler.handle_key("a")
        for ch in "url":
            handler.handle_key(ch)
        handler.handle_key("\r")
        handler.handle_key("\r")
        with patch("httpx.post", side_effect=Exception("fail")):
            handler.handle_key("\r")
        assert "error" in state["status_msg"].lower() or "fail" in state["status_msg"].lower()


class TestProjectsAddInput:
    def test_projects_add_full_flow(self) -> None:
        handler, state = _make_handler("projects")
        state["projects_data"] = []
        handler.handle_key("a")
        assert state["input_mode"] == "projects_add"
        for ch in "my-proj":
            handler.handle_key(ch)
        handler.handle_key("\r")
        assert "weight" in state["status_msg"].lower()
        for ch in "20":
            handler.handle_key(ch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"project_id": "p1"}
            handler.handle_key("\r")
        assert "p1" in state["status_msg"]

    def test_projects_add_error(self) -> None:
        handler, state = _make_handler("projects")
        state["projects_data"] = []
        handler.handle_key("a")
        for ch in "proj":
            handler.handle_key(ch)
        handler.handle_key("\r")
        handler.handle_key("\r")
        with patch("httpx.post", side_effect=Exception("fail")):
            handler.handle_key("\r")
        assert (
            "error" in state["status_msg"].lower()
            or "added" in state["status_msg"].lower()
        )


class TestProjectsSetWeightInput:
    def test_set_weight_success(self) -> None:
        handler, state = _make_handler("projects")
        state["projects_data"] = [{"project_id": "p1", "name": "test"}]
        state["selected_project_idx"] = 0
        handler.handle_key("w")
        assert state["input_mode"] == "projects_set_weight"
        for ch in "50":
            handler.handle_key(ch)
        with patch("httpx.put") as mock_put:
            mock_put.return_value.status_code = 200
            handler.handle_key("\r")
        assert "50" in state["status_msg"]

    def test_set_weight_invalid(self) -> None:
        handler, state = _make_handler("projects")
        state["projects_data"] = [{"project_id": "p1", "name": "test"}]
        state["selected_project_idx"] = 0
        handler.handle_key("w")
        for ch in "abc":
            handler.handle_key(ch)
        handler.handle_key("\r")
        assert "invalid" in state["status_msg"].lower()

    def test_set_weight_error(self) -> None:
        handler, state = _make_handler("projects")
        state["projects_data"] = [{"project_id": "p1", "name": "test"}]
        state["selected_project_idx"] = 0
        handler.handle_key("w")
        for ch in "50":
            handler.handle_key(ch)
        with patch("httpx.put", side_effect=Exception("fail")):
            handler.handle_key("\r")
        assert "error" in state["status_msg"].lower()

    def test_set_weight_http_error(self) -> None:
        handler, state = _make_handler("projects")
        state["projects_data"] = [{"project_id": "p1", "name": "test"}]
        state["selected_project_idx"] = 0
        handler.handle_key("w")
        for ch in "50":
            handler.handle_key(ch)
        with patch("httpx.put") as mock_put:
            mock_put.return_value.status_code = 500
            handler.handle_key("\r")
        assert "failed" in state["status_msg"].lower()


class TestCycleDispatchMode:
    def test_cycle_dispatch_success(self) -> None:
        handler, state = _make_handler("main")
        with patch("httpx.put") as mock_put:
            mock_put.return_value.status_code = 200
            mock_put.return_value.json.return_value = {"dispatch_mode": "passive_external"}
            handler.handle_key("d")
        assert state["dispatch_mode"] == "passive_external"

    def test_cycle_dispatch_error(self) -> None:
        handler, state = _make_handler("main")
        with patch("httpx.put", side_effect=Exception("fail")):
            handler.handle_key("d")
        assert "error" in state["status_msg"].lower()

    def test_cycle_dispatch_http_error(self) -> None:
        handler, state = _make_handler("main")
        with patch("httpx.put") as mock_put:
            mock_put.return_value.status_code = 500
            handler.handle_key("d")
        assert "failed" in state["status_msg"].lower()

    def test_cycle_dispatch_unknown_mode(self) -> None:
        handler, state = _make_handler("main")
        state["dispatch_mode"] = "unknown"
        with patch("httpx.put") as mock_put:
            mock_put.return_value.status_code = 200
            mock_put.return_value.json.return_value = {"dispatch_mode": "active"}
            handler.handle_key("d")
        assert state["dispatch_mode"] == "active"


class TestAnsibleInstallInput:
    def test_ansible_install_success(self) -> None:
        handler, state = _make_handler("ansible")
        handler.handle_key("i")
        assert state["input_mode"] == "ansible_install"
        for ch in "nginx":
            handler.handle_key(ch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"name": "nginx"}
            handler.handle_key("\r")
        assert "nginx" in state["status_msg"]

    def test_ansible_install_http_error(self) -> None:
        handler, state = _make_handler("ansible")
        handler.handle_key("i")
        for ch in "x":
            handler.handle_key(ch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 500
            handler.handle_key("\r")
        assert "failed" in state["status_msg"].lower()


class TestTemplatesRefreshAction:
    def test_r_key_in_templates_view_refreshes(self) -> None:
        handler, state = _make_handler("templates")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "templates": ["default", "bug_fix"],
                "refreshed": True,
            }
            handler.handle_key("r")
        mock_post.assert_called_once_with(
            "http://localhost:8000/admin/templates/refresh",
            timeout=30.0,
        )
        assert "Refreshed" in state["status_msg"]

    def test_templates_refresh_http_error(self) -> None:
        handler, state = _make_handler("templates")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 500
            handler.handle_key("r")
        assert "Refresh failed" in state["status_msg"]

    def test_templates_refresh_connection_error(self) -> None:
        handler, state = _make_handler("templates")
        with patch("httpx.post") as mock_post:
            mock_post.side_effect = Exception("connection refused")
            handler.handle_key("r")
        assert "Refresh error" in state["status_msg"]


class TestQuantizationDetectAction:
    def test_d_key_in_quantization_view_detects(self) -> None:
        handler, state = _make_handler("quantization")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "detections": [
                    {"model_id": "gpt-4", "precision": "fp16", "confidence": 0.95}
                ]
            }
            handler.handle_key("d")
        mock_post.assert_called_once_with(
            "http://localhost:8000/admin/quantization/detect",
            json={},
            timeout=30.0,
        )
        assert "Detected" in state["status_msg"]

    def test_quantization_detect_http_error(self) -> None:
        handler, state = _make_handler("quantization")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 500
            handler.handle_key("d")
        assert "Detect failed" in state["status_msg"]

    def test_quantization_detect_connection_error(self) -> None:
        handler, state = _make_handler("quantization")
        with patch("httpx.post") as mock_post:
            mock_post.side_effect = Exception("timeout")
            handler.handle_key("d")
        assert "Detect error" in state["status_msg"]


class TestWorkersPingSuccess:
    def test_ping_workers_success(self) -> None:
        handler, state = _make_handler("workers")
        with patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "workers": [
                    {"worker_id": "w1", "status": "alive"},
                    {"worker_id": "w2", "status": "alive"},
                ]
            }
            handler.handle_key("p")
        mock_post.assert_called_once_with(
            "http://localhost:8000/admin/workers/ping",
            timeout=10.0,
        )
        assert "Ping OK" in state["status_msg"]
        assert "2 workers" in state["status_msg"]


class TestToggleViews:
    def test_toggle_mcp_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("u")
        assert state["current_view"] == "mcp"

    def test_toggle_mcp_back_to_main(self) -> None:
        handler, state = _make_handler("mcp")
        handler.handle_key("u")
        assert state["current_view"] == "main"

    def test_toggle_skills_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("j")
        assert state["current_view"] == "skills"

    def test_toggle_compute_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("e")
        assert state["current_view"] == "compute"

    def test_toggle_scores_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("b")
        assert state["current_view"] == "scores"

    def test_toggle_templates_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("l")
        assert state["current_view"] == "templates"

    def test_toggle_quantization_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("n")
        assert state["current_view"] == "quantization"

    def test_toggle_filestore_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("f")
        assert state["current_view"] == "filestore"

    def test_toggle_deployments_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("z")
        assert state["current_view"] == "deployments"

    def test_toggle_leaderboard_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("y")
        assert state["current_view"] == "leaderboard"
        assert "Leaderboard" in state["status_msg"]

    def test_toggle_leaderboard_back_to_main(self) -> None:
        handler, state = _make_handler("leaderboard")
        handler.handle_key("y")
        assert state["current_view"] == "main"

    def test_toggle_playbooks_from_main(self) -> None:
        handler, state = _make_handler("main")
        handler.handle_key("P")
        assert state["current_view"] == "playbooks"
        assert "Playbooks" in state["status_msg"]

    def test_toggle_playbooks_back_to_main(self) -> None:
        handler, state = _make_handler("playbooks")
        handler.handle_key("P")
        assert state["current_view"] == "main"
