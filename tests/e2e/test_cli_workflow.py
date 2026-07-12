"""End-to-end tests for the gludd CLI workflow.

Covers: status, project list, human-todo list, audit-plugins, help, error handling.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import httpx

from general_ludd.cli import main


def _run_cli(args: list[str]) -> int:
    try:
        with patch.object(sys, "argv", ["gludd", *args]):
            main()
        return 0
    except SystemExit as exc:
        return exc.code if exc.code is not None else 1


def _run_cli_output(args: list[str], capsys) -> tuple[str, str, int]:
    try:
        with patch.object(sys, "argv", ["gludd", *args]):
            main()
        captured = capsys.readouterr()
        return captured.out, captured.err, 0
    except SystemExit as exc:
        captured = capsys.readouterr()
        return captured.out, captured.err, exc.code if exc.code is not None else 1


# ── gludd status ────────────────────────────────────────────────────────────

class TestStatusWorkflow:
    def test_status_parsing_no_args(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status"])
        args = mock_cmd.call_args[0][0]
        assert args.todo_id is None
        assert args.project is None

    def test_status_parsing_with_todo_id(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status", "TODO-001"])
        args = mock_cmd.call_args[0][0]
        assert args.todo_id == "TODO-001"

    def test_status_parsing_with_project(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status", "--project", "proj-1"])
        args = mock_cmd.call_args[0][0]
        assert args.project == "proj-1"

    def test_status_system_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {
            "version": "0.1.0",
            "uptime_ticks": 10,
            "todos_total": 5,
            "queue_depths": {"core": 3},
            "tick_metrics": {"todos_dispatched": 8},
            "config_dir": "/etc/gludd",
            "config_files": [],
            "config_file_count": 0,
            "filestore_available": True,
            "filestore_root": "/tmp",
            "filestore_binaries": [],
            "binary_versions": {},
            "db_engine": "sqlite",
            "db_url": "sqlite:///gludd.db",
            "quality_gate": {"overall": "passed", "passed_count": 5, "total_count": 5, "checks": []},
        })
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["status"], capsys)
        assert code == 0
        assert "General Ludd Agent" in out
        assert "v0.1.0" in out

    def test_status_todo_detail_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"todo_id": "TODO-001", "title": "Fix"})
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["status", "TODO-001"], capsys)
        assert code == 0
        assert "/api/todos/TODO-001" in mock_get.call_args[0][0]
        assert "TODO-001" in out

    def test_status_todo_not_found(self):
        mock_resp = MagicMock(status_code=404, text="not found")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["status", "MISSING"])
        assert exit_code == 1

    def test_status_offline_shows_offline_status(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            out, _err, code = _run_cli_output(["status"], capsys)
        assert code == 0
        assert "General Ludd Agent" in out

    def test_status_timeout_shows_offline_status(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            out, _err, code = _run_cli_output(["status"], capsys)
        assert code == 0
        assert "General Ludd Agent" in out


# ── gludd project list ──────────────────────────────────────────────────────

class TestProjectListWorkflow:
    def test_project_list_parsing_defaults(self):
        with patch("general_ludd.cli._cmd_project_list") as mock_cmd:
            _run_cli(["project", "list"])
        mock_cmd.assert_called_once()

    def test_project_list_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_project_list") as mock_cmd:
            _run_cli(["project", "list", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"

    def test_project_list_success_with_projects(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {
            "projects": [
                {"project_id": "p1", "name": "alpha", "weight": 30.0,
                 "dispatch_mode": "active", "active": True, "repo_url": "",
                 "workspace_path": ""},
                {"project_id": "p2", "name": "beta", "weight": 70.0,
                 "dispatch_mode": "passive_external", "active": True, "repo_url": "",
                 "workspace_path": ""},
            ],
        })
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            out, _err, code = _run_cli_output(["project", "list"], capsys)
        assert code == 0
        assert "/admin/projects" in mock_get.call_args[0][0]
        assert "Projects: 2" in out
        assert "alpha" in out
        assert "beta" in out

    def test_project_list_empty(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"projects": []})
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["project", "list"], capsys)
        assert code == 0
        assert "No projects registered" in out

    def test_project_list_api_error(self):
        mock_resp = MagicMock(status_code=500, text="server error")
        with patch("httpx.get", return_value=mock_resp):
            exit_code = _run_cli(["project", "list"])
        assert exit_code == 1

    def test_project_list_offline(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            out, err, code = _run_cli_output(["project", "list"], capsys)
        assert code == 1
        assert "Cannot connect" in err or "Cannot connect" in out

    def test_project_subcommand_prints_help(self, capsys):
        out, _err, code = _run_cli_output(["project"], capsys)
        assert "project" in out.lower() or code == 0


# ── gludd human-todo list ───────────────────────────────────────────────────

class TestHumanTodoListWorkflow:
    def test_human_todo_list_parsing_no_filters(self):
        with patch("general_ludd.cli_human_todos._http") as mock_http:
            mock_http.return_value = []
            with patch.object(sys, "argv", ["gludd", "human-todo", "list"]):
                main()
        mock_http.assert_called_once_with(
            "GET", "http://localhost:8000/api/human-todos", params=None,
        )

    def test_human_todo_list_with_status_filter(self):
        with patch("general_ludd.cli_human_todos._http") as mock_http:
            mock_http.return_value = []
            with patch.object(sys, "argv", [
                "gludd", "human-todo", "list", "--status", "open",
            ]):
                main()
        call_kwargs = mock_http.call_args
        assert call_kwargs[1]["params"] == {"status": "open"}

    def test_human_todo_list_with_all_filters(self):
        with patch("general_ludd.cli_human_todos._http") as mock_http:
            mock_http.return_value = []
            with patch.object(sys, "argv", [
                "gludd", "human-todo", "list",
                "--status", "open", "--category", "permission_escalation",
                "--priority", "high", "--agent-id", "agent-1",
            ]):
                main()
        params = mock_http.call_args[1]["params"]
        assert params["status"] == "open"
        assert params["category"] == "permission_escalation"
        assert params["priority"] == "high"
        assert params["agent_id"] == "agent-1"

    def test_human_todo_list_with_daemon_url(self):
        with patch("general_ludd.cli_human_todos._http") as mock_http:
            mock_http.return_value = []
            with patch.object(sys, "argv", [
                "gludd", "human-todo", "list", "--daemon-url", "http://localhost:7777",
            ]):
                main()
        assert mock_http.call_args[0][1] == "http://localhost:7777/api/human-todos"

    def test_human_todo_list_with_json_flag(self, capsys):
        mock_rows = [{"id": "ht-1", "status": "open", "title": "Need token"}]
        with patch("general_ludd.cli_human_todos._http", return_value=mock_rows):
            with patch.object(sys, "argv", [
                "gludd", "human-todo", "list", "--json",
            ]):
                main()
        out, _err, _code = _run_cli_output(["human-todo", "list", "--json"], capsys)
        assert "ht-1" in out

    def test_human_todo_list_empty(self, capsys):
        with patch("general_ludd.cli_human_todos._http", return_value=[]):
            with patch.object(sys, "argv", [
                "gludd", "human-todo", "list",
            ]):
                main()
        out, _err, _code = _run_cli_output(["human-todo", "list"], capsys)
        assert "(no human-todos)" in out

    def test_human_todo_list_api_error(self):
        with patch("httpx.request", side_effect=Exception("connection refused")):
            exit_code = _run_cli(["human-todo", "list"])
        assert exit_code == 1

    def test_human_todo_missing_subcommand_prints_help(self, capsys):
        out, _err, code = _run_cli_output(["human-todo"], capsys)
        assert "human-todo" in out or code == 0


# ── gludd audit-plugins ─────────────────────────────────────────────────────

class TestAuditPluginsWorkflow:
    def test_audit_plugins_parsing_defaults(self):
        with patch(
            "general_ludd.cli_audit_plugins._invoke_audit_playbook",
        ) as mock_invoke:
            mock_invoke.return_value = {"rc": 0, "status": "successful", "events": []}
            with patch.object(sys, "argv", ["gludd", "audit-plugins"]):
                main()
        mock_invoke.assert_called_once()
        extra = mock_invoke.call_args[0][0]
        assert extra["audit_plugins_run_enforce_disengage"] is False
        assert extra["daemon_url"] == "http://localhost:8000"

    def test_audit_plugins_with_all_args(self):
        with patch(
            "general_ludd.cli_audit_plugins._invoke_audit_playbook",
        ) as mock_invoke:
            mock_invoke.return_value = {"rc": 0, "status": "successful", "events": []}
            with patch.object(sys, "argv", [
                "gludd", "audit-plugins",
                "--project", "my-proj",
                "--limit", "agent_floor_check",
                "--enforce-disengage",
                "--daemon-url", "http://localhost:9999",
            ]):
                main()
        extra = mock_invoke.call_args[0][0]
        assert extra["project_name"] == "my-proj"
        assert extra["audit_limit"] == "agent_floor_check"
        assert extra["audit_plugins_run_enforce_disengage"] is True
        assert extra["daemon_url"] == "http://localhost:9999"

    def test_audit_plugins_success_output(self, capsys):
        with patch(
            "general_ludd.cli_audit_plugins._invoke_audit_playbook",
        ) as mock_invoke:
            mock_invoke.return_value = {
                "rc": 0, "status": "successful", "events": [{"e": 1}, {"e": 2}],
            }
            out, _err, code = _run_cli_output(["audit-plugins"], capsys)
        assert code == 0
        assert "status=successful" in out
        assert "rc=0" in out
        assert "events=2" in out

    def test_audit_plugins_failure_exits_nonzero(self):
        with patch(
            "general_ludd.cli_audit_plugins._invoke_audit_playbook",
        ) as mock_invoke:
            mock_invoke.return_value = {
                "rc": 1, "status": "failed", "events": [],
            }
            exit_code = _run_cli(["audit-plugins"])
        assert exit_code == 1

    def test_audit_plugins_no_events(self, capsys):
        with patch(
            "general_ludd.cli_audit_plugins._invoke_audit_playbook",
        ) as mock_invoke:
            mock_invoke.return_value = {
                "rc": 0, "status": "successful", "events": None,
            }
            out, _err, code = _run_cli_output(["audit-plugins"], capsys)
        assert code == 0
        assert "status=successful" in out
        assert "rc=0" in out


# ── help text and error handling ────────────────────────────────────────────

class TestHelpWorkflow:
    def test_help_flag_exits_zero(self, capsys):
        out, _err, code = _run_cli_output(["--help"], capsys)
        assert code == 0

    def test_help_output_contains_key_commands(self, capsys):
        out, _err, _code = _run_cli_output(["--help"], capsys)
        assert "status" in out
        assert "project" in out
        assert "human-todo" in out
        assert "audit-plugins" in out

    def test_help_output_contains_description(self, capsys):
        out, _err, _code = _run_cli_output(["--help"], capsys)
        assert "General Ludd Agent" in out

    def test_project_list_help_output(self, capsys):
        out, _err, code = _run_cli_output(["project", "list", "--help"], capsys)
        assert code == 0
        assert "List registered projects" in out

    def test_human_todo_list_help_output(self, capsys):
        out, _err, code = _run_cli_output(["human-todo", "list", "--help"], capsys)
        assert code == 0
        assert "human-todos" in out.lower()

    def test_audit_plugins_help_output(self, capsys):
        out, _err, code = _run_cli_output(["audit-plugins", "--help"], capsys)
        assert code == 0
        assert "plugin" in out.lower()


class TestErrorHandlingWorkflow:
    def test_unknown_command_exits_nonzero(self):
        exit_code = _run_cli(["nonexistent-command"])
        assert exit_code != 0

    def test_unknown_subcommand_exits_nonzero(self):
        exit_code = _run_cli(["project", "nonexistent"])
        assert exit_code != 0

    def test_missing_required_arg_exits_nonzero(self):
        exit_code = _run_cli(["add"])
        assert exit_code != 0

    def test_empty_args_shows_usage(self, capsys):
        out, _err, code = _run_cli_output([], capsys)
        assert "usage" in out.lower() or code != 0

    def test_version_output(self, capsys):
        with patch("general_ludd.cli._cmd_version") as mock_cmd:
            _run_cli(["version"])
        mock_cmd.assert_called_once()
