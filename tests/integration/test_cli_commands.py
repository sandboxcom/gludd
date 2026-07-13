"""CLI integration tests: project, todo, pause, dispatch commands.

Covers arg parsing, daemon HTTP calls (mocked), and error handling.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import httpx

from general_ludd.cli import build_parser, main


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


# ── project add ──────────────────────────────────────────────────────────────

class TestProjectAdd:
    def test_parsing_minimal(self):
        with patch("general_ludd.cli._cmd_project_add") as mock_cmd:
            _run_cli(["project", "add", "myproj"])
        args = mock_cmd.call_args[0][0]
        assert args.name == "myproj"
        assert args.weight == 30.0
        assert args.description == ""
        assert args.repo_url == ""
        assert args.workspace_path == ""
        assert args.dispatch_mode == "active"

    def test_parsing_all_args(self):
        with patch("general_ludd.cli._cmd_project_add") as mock_cmd:
            _run_cli([
                "project", "add", "bigproj",
                "--repo-url", "https://github.com/x/y",
                "--workspace-path", "/tmp/ws",
                "--weight", "60",
                "--description", "Big project",
                "--dispatch-mode", "passive_external",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.name == "bigproj"
        assert args.repo_url == "https://github.com/x/y"
        assert args.workspace_path == "/tmp/ws"
        assert args.weight == 60.0
        assert args.description == "Big project"
        assert args.dispatch_mode == "passive_external"

    def test_success_http(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {
            "project_id": "proj-abc", "name": "myproj", "weight": 30.0,
            "dispatch_mode": "active", "repo_url": "", "workspace_path": "",
        })
        with patch("httpx.post", return_value=mock_resp):
            out, _err, code = _run_cli_output(["project", "add", "myproj"], capsys)
        assert code == 0
        assert "proj-abc" in out
        assert "myproj" in out

    def test_error_http(self, capsys):
        mock_resp = MagicMock(status_code=500, text="Internal Error")
        with patch("httpx.post", return_value=mock_resp):
            _out, err, code = _run_cli_output(["project", "add", "fail"], capsys)
        assert code == 1
        assert "Error" in err


# ── project list ─────────────────────────────────────────────────────────────

class TestProjectList:
    def test_parsing(self):
        with patch("general_ludd.cli._cmd_project_list") as mock_cmd:
            _run_cli(["project", "list"])
        args = mock_cmd.call_args[0][0]
        assert args.daemon_url == "http://localhost:8000"

    def test_empty_list(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"projects": []})
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["project", "list"], capsys)
        assert code == 0
        assert "No projects registered" in out

    def test_populated_list(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"projects": [
            {"project_id": "p1", "name": "Alpha", "weight": 50.0,
             "dispatch_mode": "active", "active": True, "repo_url": "", "workspace_path": ""},
            {"project_id": "p2", "name": "Beta", "weight": 50.0,
             "dispatch_mode": "passive_external", "active": False,
             "repo_url": "https://x", "workspace_path": "/tmp/x"},
        ]})
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["project", "list"], capsys)
        assert code == 0
        assert "Projects: 2" in out
        assert "p1" in out and "Alpha" in out
        assert "p2" in out and "Beta" in out
        assert "[active]" in out
        assert "[inactive]" in out

    def test_error_http(self, capsys):
        mock_resp = MagicMock(status_code=500, text="Server Error")
        with patch("httpx.get", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["project", "list"], capsys)
        assert code == 1


# ── project remove ───────────────────────────────────────────────────────────

class TestProjectRemove:
    def test_parsing(self):
        with patch("general_ludd.cli._cmd_project_remove") as mock_cmd:
            _run_cli(["project", "remove", "proj-abc"])
        args = mock_cmd.call_args[0][0]
        assert args.project_id == "proj-abc"

    def test_success(self, capsys):
        mock_resp = MagicMock(status_code=200)
        with patch("httpx.delete", return_value=mock_resp):
            out, _err, code = _run_cli_output(["project", "remove", "proj-abc"], capsys)
        assert code == 0
        assert "proj-abc" in out

    def test_error_http(self, capsys):
        mock_resp = MagicMock(status_code=404, text="Not Found")
        with patch("httpx.delete", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["project", "remove", "nope"], capsys)
        assert code == 1


# ── add (todo dispatch) ─────────────────────────────────────────────────────

class TestAddTodo:
    def test_parsing_minimal(self):
        with patch("general_ludd.cli._cmd_add") as mock_cmd:
            _run_cli(["add", "Fix login bug"])
        args = mock_cmd.call_args[0][0]
        assert args.title == "Fix login bug"
        assert args.queue == "core"
        assert args.priority == "medium"
        assert args.work_type == "code"
        assert args.description == ""
        assert args.project is None

    def test_parsing_all_args(self):
        with patch("general_ludd.cli._cmd_add") as mock_cmd:
            _run_cli([
                "add", "Add dark mode",
                "--queue", "ui", "--priority", "high",
                "--work-type", "feature",
                "--description", "Implement dark theme",
                "--project", "proj-dm",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.title == "Add dark mode"
        assert args.queue == "ui"
        assert args.priority == "high"
        assert args.work_type == "feature"
        assert args.description == "Implement dark theme"
        assert args.project == "proj-dm"

    def test_success_http(self, capsys):
        mock_resp = MagicMock(status_code=201, json=lambda: {
            "todo_id": "TODO-001", "title": "Fix login", "status": "pending",
        })
        with patch("httpx.post", return_value=mock_resp):
            out, _err, code = _run_cli_output(["add", "Fix login"], capsys)
        assert code == 0
        assert "TODO-001" in out

    def test_error_http(self, capsys):
        mock_resp = MagicMock(status_code=400, text="Bad Request")
        with patch("httpx.post", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["add", "bad"], capsys)
        assert code == 1


# ── status (todo/system) ─────────────────────────────────────────────────────

class TestStatus:
    def test_parsing_system(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status"])
        args = mock_cmd.call_args[0][0]
        assert args.todo_id is None

    def test_parsing_todo(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run_cli(["status", "TODO-001"])
        args = mock_cmd.call_args[0][0]
        assert args.todo_id == "TODO-001"

    def test_system_status_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {
            "version": "0.1.0", "db_engine": "sqlite", "db_url": "sqlite:///...",
            "uptime_ticks": 5, "todos_total": 3, "queue_depths": {"core": 3},
            "tick_metrics": {}, "quality_gate": {"overall": "pass", "passed_count": 3, "total_count": 3, "checks": []},
            "config_file_count": 0, "filestore_available": False, "binary_versions": {},
        })
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["status"], capsys)
        assert code == 0
        assert "daemon running" in out

    def test_todo_status_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {
            "todo_id": "TODO-001", "title": "Fix bug", "status": "in_progress",
        })
        with patch("httpx.get", return_value=mock_resp):
            out, _err, code = _run_cli_output(["status", "TODO-001"], capsys)
        assert code == 0
        assert "TODO-001" in out


# ── list (todos) ─────────────────────────────────────────────────────────────

class TestListTodos:
    def test_parsing(self):
        with patch("general_ludd.cli._cmd_list") as mock_cmd:
            _run_cli(["list"])
        args = mock_cmd.call_args[0][0]
        assert args.queue is None
        assert args.status is None
        assert args.project is None

    def test_parsing_filters(self):
        with patch("general_ludd.cli._cmd_list") as mock_cmd:
            _run_cli(["list", "--queue", "core", "--status", "pending", "--project", "p1"])
        args = mock_cmd.call_args[0][0]
        assert args.queue == "core"
        assert args.status == "pending"
        assert args.project == "p1"

    def test_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"todos": [
            {"todo_id": "T1", "title": "Bug fix", "status": "pending", "queue": "core"},
            {"todo_id": "T2", "title": "Feature X", "status": "in_progress", "queue": "feature"},
        ]})
        with patch("httpx.get", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["list", "--status", "pending"], capsys)
        assert code == 0


# ── pause commands ───────────────────────────────────────────────────────────

class TestPause:
    def test_pause_list_parsing(self):
        with patch("general_ludd.cli._cmd_pause_list") as mock_cmd:
            _run_cli(["pause", "list"])
        args = mock_cmd.call_args[0][0]
        assert args.daemon_url == "http://localhost:8000"

    def test_pause_list_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"paused": []})
        with patch("httpx.get", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["pause", "list"], capsys)
        assert code == 0

    def test_pause_project_parsing(self):
        with patch("general_ludd.cli._cmd_pause_project") as mock_cmd:
            _run_cli(["pause", "project", "proj-z", "--reason", "blocked"])
        args = mock_cmd.call_args[0][0]
        assert args.target_id == "proj-z"
        assert args.reason == "blocked"

    def test_pause_project_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"paused": True, "target_id": "proj-z"})
        with patch("httpx.post", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["pause", "project", "proj-z"], capsys)
        assert code == 0

    def test_pause_model_parsing(self):
        with patch("general_ludd.cli._cmd_pause_model") as mock_cmd:
            _run_cli(["pause", "model", "gpt-4", "--reason", "rate limit"])
        args = mock_cmd.call_args[0][0]
        assert args.target_id == "gpt-4"
        assert args.reason == "rate limit"

    def test_pause_model_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"paused": True, "target_id": "gpt-4"})
        with patch("httpx.post", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["pause", "model", "gpt-4"], capsys)
        assert code == 0

    def test_resume_project_parsing(self):
        with patch("general_ludd.cli._cmd_resume_project") as mock_cmd:
            _run_cli(["resume", "project", "proj-z"])
        args = mock_cmd.call_args[0][0]
        assert args.target_id == "proj-z"

    def test_resume_project_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"resumed": True, "target_id": "proj-z"})
        with patch("httpx.post", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["resume", "project", "proj-z"], capsys)
        assert code == 0

    def test_resume_model_parsing(self):
        with patch("general_ludd.cli._cmd_resume_model") as mock_cmd:
            _run_cli(["resume", "model", "gpt-4"])
        args = mock_cmd.call_args[0][0]
        assert args.target_id == "gpt-4"

    def test_resume_model_success(self, capsys):
        mock_resp = MagicMock(status_code=200, json=lambda: {"resumed": True, "target_id": "gpt-4"})
        with patch("httpx.post", return_value=mock_resp):
            _out, _err, code = _run_cli_output(["resume", "model", "gpt-4"], capsys)
        assert code == 0


# ── parser structure ─────────────────────────────────────────────────────────

class TestParserStructure:
    def test_project_subcommands_registered(self):
        parser, _map = build_parser()
        proj = parser._subparsers._group_actions[0].choices["project"]
        sub_sub = proj._subparsers._group_actions[0].choices
        assert "add" in sub_sub
        assert "list" in sub_sub
        assert "remove" in sub_sub
        assert "init" in sub_sub
        assert "paths" in sub_sub

    def test_pause_subcommands_registered(self):
        parser, _map = build_parser()
        pause = parser._subparsers._group_actions[0].choices["pause"]
        sub_sub = pause._subparsers._group_actions[0].choices
        assert "list" in sub_sub
        assert "project" in sub_sub
        assert "model" in sub_sub

    def test_resume_subcommands_registered(self):
        parser, _map = build_parser()
        resume = parser._subparsers._group_actions[0].choices["resume"]
        sub_sub = resume._subparsers._group_actions[0].choices
        assert "project" in sub_sub
        assert "model" in sub_sub

    def test_add_command_registered(self):
        parser, _map = build_parser()
        choices = parser._subparsers._group_actions[0].choices
        assert "add" in choices
        assert "status" in choices
        assert "list" in choices

    def test_project_dispatch_mode_choices(self):
        parser, _subcommands = build_parser()
        proj = parser._subparsers._group_actions[0].choices["project"]
        add_cmd = proj._subparsers._group_actions[0].choices["add"]
        mode_action = next(a for a in add_cmd._actions if a.dest == "dispatch_mode")
        assert set(mode_action.choices) == {"active", "passive_external", "worktree_monitor"}

    def test_add_work_type_defaults(self):
        parser, _subcommands = build_parser()
        add_cmd = parser._subparsers._group_actions[0].choices["add"]
        for a in add_cmd._actions:
            if a.dest == "work_type":
                assert a.default == "code"
            if a.dest == "priority":
                assert a.default == "medium"
            if a.dest == "queue":
                assert a.default == "core"


# ── HTTP error resilience ──────────────────────────────────────────────────

class TestCliHttpErrors:
    def test_list_connection_refused(self, capsys):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            _out, err, code = _run_cli_output(["project", "list"], capsys)
        assert code == 1
        assert "Cannot connect" in err

    def test_add_connection_timeout(self, capsys):
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
            _out, err, code = _run_cli_output(["add", "test"], capsys)
        assert code == 1
        assert "Cannot connect" in err

    def test_status_non_200_code(self, capsys):
        mock_resp = MagicMock(status_code=503, text="Unavailable")
        with patch("httpx.get", return_value=mock_resp):
            _out, err, code = _run_cli_output(["status", "TODO-999"], capsys)
        assert code == 1
        assert "Error" in err
