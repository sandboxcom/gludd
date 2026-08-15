"""Unit tests for cli_human_todos."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli_human_todos import (
    _cmd_comment,
    _cmd_dismiss,
    _cmd_done,
    _cmd_in_progress,
    _cmd_list,
    _cmd_show,
    _cmd_stats,
    _cmd_watch,
    _print_json,
    _print_table,
    _psk_headers,
    add_human_todo_subparser,
)


class TestPskHeaders:
    def test_no_psk_env(self):
        with patch.dict("os.environ", {}, clear=True):
            headers = _psk_headers()
            assert "Authorization" not in headers

    def test_psk_env_adds_bearer(self):
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": "secret123"}, clear=True):
            headers = _psk_headers()
            assert headers["Authorization"] == "Bearer secret123"


class TestPrintJson:
    def test_outputs_json(self, capsys):
        _print_json({"key": [1, 2, 3]})
        captured = capsys.readouterr()
        assert '"key"' in captured.out


class TestPrintTable:
    def test_empty_rows(self, capsys):
        _print_table([])
        captured = capsys.readouterr()
        assert "no human-todos" in captured.out

    def test_with_rows(self, capsys):
        rows = [
            {
                "id": "1",
                "status": "open",
                "priority": "high",
                "category": "permission",
                "agent_id": "agent-1",
                "title": "Test todo",
            }
        ]
        _print_table(rows)
        captured = capsys.readouterr()
        assert "1" in captured.out
        assert "open" in captured.out
        assert "Test todo" in captured.out


class TestCmdList:
    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.status = None
        args.category = None
        args.priority = None
        args.agent_id = None
        args.json = True

        rows = [{"id": "1", "title": "test"}]
        with patch("general_ludd.cli_human_todos._http", return_value=rows):
            _cmd_list(args)
        captured = capsys.readouterr()
        assert "1" in captured.out

    def test_filters_applied_to_params(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.status = "open"
        args.category = "permission"
        args.priority = "high"
        args.agent_id = "agent-1"
        args.json = False

        with patch("general_ludd.cli_human_todos._http", return_value=[]) as mock_http:
            _cmd_list(args)
            mock_http.assert_called_once()
            call_kwargs = mock_http.call_args
            params = call_kwargs[1]["params"]
            assert params["status"] == "open"
            assert params["category"] == "permission"


class TestCmdShow:
    def test_not_found(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "999"
        args.json = False

        with patch("general_ludd.cli_human_todos._http", return_value=None):
            _cmd_show(args)
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.json = True

        row = {"id": "1", "title": "test"}
        with patch("general_ludd.cli_human_todos._http", return_value=row):
            _cmd_show(args)
        captured = capsys.readouterr()
        assert "1" in captured.out

    def test_detailed_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.json = False

        row = {
            "id": "1",
            "status": "open",
            "priority": "high",
            "category": "permission",
            "agent_id": "agent-1",
            "parent_agent_todo_id": "parent-1",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-02",
            "resolved_at": None,
            "human_resolver": None,
            "title": "Need access",
            "body": "Please grant access",
            "human_resolution": None,
            "tags": ["urgent"],
        }
        with patch("general_ludd.cli_human_todos._http", return_value=row):
            _cmd_show(args)
        captured = capsys.readouterr()
        assert "Need access" in captured.out
        assert "agent-1" in captured.out
        assert "urgent" in captured.out


class TestCmdDone:
    def test_text_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.resolution = "Done"
        args.resolver = "operator"
        args.json = False

        with patch("general_ludd.cli_human_todos._http", return_value={}):
            _cmd_done(args)
        captured = capsys.readouterr()
        assert "Marked done" in captured.out


class TestCmdDismiss:
    def test_without_reason_exits_2(self):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.reason = None

        with pytest.raises(SystemExit) as exc_info:
            _cmd_dismiss(args)
        assert exc_info.value.code == 2

    def test_with_reason(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.reason = "Not needed"
        args.resolver = "operator"
        args.json = False

        with patch("general_ludd.cli_human_todos._http", return_value={}):
            _cmd_dismiss(args)
        captured = capsys.readouterr()
        assert "Dismissed" in captured.out


class TestCmdInProgress:
    def test_text_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.json = False

        with patch("general_ludd.cli_human_todos._http", return_value={}):
            _cmd_in_progress(args)
        captured = capsys.readouterr()
        assert "Marked in-progress" in captured.out


class TestCmdComment:
    def test_without_text_exits_2(self):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.text = ""

        with pytest.raises(SystemExit) as exc_info:
            _cmd_comment(args)
        assert exc_info.value.code == 2

    def test_with_text(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.text = "Working on it"
        args.json = False

        with patch("general_ludd.cli_human_todos._http", return_value={}):
            _cmd_comment(args)
        captured = capsys.readouterr()
        assert "Commented" in captured.out

    def test_comment_prefix_preserved(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.id = "1"
        args.text = "comment:already prefixed"
        args.json = False

        with patch("general_ludd.cli_human_todos._http", return_value={}) as mock_http:
            _cmd_comment(args)
            call_kwargs = mock_http.call_args
            tag = call_kwargs[1]["json_body"]["tag"]
            assert tag == "comment:already prefixed"


class TestCmdWatch:
    def test_keyboard_interrupt_exits_cleanly(self):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.poll = 1

        call_count = 0

        def _mock_http(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"id": "1", "updated_at": "now", "status": "open", "category": "p", "title": "t"}]
            raise KeyboardInterrupt()

        with patch("general_ludd.cli_human_todos._http", side_effect=_mock_http), patch(
            "general_ludd.cli_human_todos.time.sleep"
        ), patch("builtins.print"):
            _cmd_watch(args)


class TestCmdStats:
    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.json = True

        rows = [{"id": "1", "status": "open", "category": "permission", "priority": "high"}]
        with patch("general_ludd.cli_human_todos._http", return_value=rows):
            _cmd_stats(args)
        captured = capsys.readouterr()
        assert "total" in captured.out

    def test_text_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.json = False

        rows = [
            {"id": "1", "status": "open", "category": "permission", "priority": "high"},
            {"id": "2", "status": "done", "category": "permission", "priority": "low"},
        ]
        with patch("general_ludd.cli_human_todos._http", return_value=rows):
            _cmd_stats(args)
        captured = capsys.readouterr()
        assert "total: 2" in captured.out
        assert "open" in captured.out


class TestAddHumanTodoSubparser:
    def test_adds_list_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_human_todo_subparser(subparsers)

        ns = parser.parse_args(["human-todo", "list"])
        assert ns.human_todo_command == "list"
        assert ns.func is _cmd_list

    def test_adds_show_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_human_todo_subparser(subparsers)

        ns = parser.parse_args(["human-todo", "show", "123"])
        assert ns.human_todo_command == "show"
        assert ns.id == "123"

    def test_adds_done_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_human_todo_subparser(subparsers)

        ns = parser.parse_args(["human-todo", "done", "123", "--resolution", "Fixed"])
        assert ns.human_todo_command == "done"
        assert ns.resolution == "Fixed"

    def test_adds_stats_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_human_todo_subparser(subparsers)

        ns = parser.parse_args(["human-todo", "stats"])
        assert ns.human_todo_command == "stats"
        assert ns.func is _cmd_stats

    def test_adds_comment_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_human_todo_subparser(subparsers)

        ns = parser.parse_args(["human-todo", "comment", "123", "hello"])
        assert ns.human_todo_command == "comment"
        assert ns.text == "hello"

    def test_adds_in_progress_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_human_todo_subparser(subparsers)

        ns = parser.parse_args(["human-todo", "in-progress", "123"])
        assert ns.human_todo_command == "in-progress"

    def test_all_subcommands_registered(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_human_todo_subparser(subparsers)

        expected = {"list", "show", "done", "dismiss", "in-progress", "comment", "watch", "stats"}
        hp = subparsers.choices["human-todo"]
        names = set(hp._subparsers._group_actions[0].choices.keys())
        for name in names:
            assert name in expected
