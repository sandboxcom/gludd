"""Unit tests for cli_account."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, Mock, patch

import pytest

from general_ludd.cli_account import (
    _cmd_backup,
    _cmd_cleanup,
    _cmd_create,
    _cmd_delete,
    _cmd_policy,
    _print_json,
    _psk_headers,
    add_account_subparser,
)


class TestPskHeaders:
    def test_no_psk_env(self):
        with patch.dict("os.environ", {}, clear=True):
            headers = _psk_headers()
            assert "Authorization" not in headers
            assert headers["Content-Type"] == "application/json"

    def test_psk_env_adds_bearer(self):
        with patch.dict("os.environ", {"GLUDD_AUTH_PSK": "secret123"}, clear=True):
            headers = _psk_headers()
            assert headers["Authorization"] == "Bearer secret123"


class TestPrintJson:
    def test_prints_json(self, capsys):
        _print_json({"key": "value"})
        captured = capsys.readouterr()
        assert "key" in captured.out
        assert "value" in captured.out

    def test_prints_none(self, capsys):
        _print_json(None)
        captured = capsys.readouterr()
        assert "null" in captured.out


class TestCmdBackup:
    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.user_id = "user1"
        args.json = True

        body = {"user_id": "user1", "exported_at": "2024-01-01"}
        with patch("general_ludd.cli_account._http", return_value=body):
            _cmd_backup(args)
        captured = capsys.readouterr()
        assert "user1" in captured.out

    def test_text_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.user_id = "user1"
        args.json = False

        body = {
            "user_id": "user1",
            "exported_at": "2024-01-01",
            "todos": [1, 2],
            "returns": [],
            "memory": [],
            "settings": [],
        }
        with patch("general_ludd.cli_account._http", return_value=body):
            _cmd_backup(args)
        captured = capsys.readouterr()
        assert "user1" in captured.out
        assert "2" in captured.out

    def test_non_dict_body(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.user_id = "user1"
        args.json = False

        with patch("general_ludd.cli_account._http", return_value="raw"):
            _cmd_backup(args)
        captured = capsys.readouterr()
        assert "raw" in captured.out


class TestCmdDelete:
    def test_no_confirm_exits_2(self):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.user_id = "user1"
        args.confirm = False

        with pytest.raises(SystemExit) as exc_info:
            _cmd_delete(args)
        assert exc_info.value.code == 2

    def test_json_output_with_confirm(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.user_id = "user1"
        args.confirm = True
        args.json = True

        body = {"user_id": "user1", "deleted_at": "2024-01-01"}
        with patch("general_ludd.cli_account._http", return_value=body):
            _cmd_delete(args)
        captured = capsys.readouterr()
        assert "user1" in captured.out


class TestCmdPolicy:
    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.service = "openai"
        args.json = True

        body = {"service": "openai", "policy": "30-day retention"}
        with patch("general_ludd.cli_account._http", return_value=body):
            _cmd_policy(args)
        captured = capsys.readouterr()
        assert "openai" in captured.out

    def test_text_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.service = "openai"
        args.json = False

        body = {"service": "openai", "policy": "30-day retention"}
        with patch("general_ludd.cli_account._http", return_value=body):
            _cmd_policy(args)
        captured = capsys.readouterr()
        assert "openai" in captured.out
        assert "30-day retention" in captured.out


class TestCmdCreate:
    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.provider = "aws"
        args.budget = 10.0
        args.ephemeral = True
        args.json = True

        body = {"account_id": "acct-123", "provider": "aws"}
        with patch("general_ludd.cli_account._http", new=Mock(return_value=body)):
            _cmd_create(args)
        captured = capsys.readouterr()
        assert "acct-123" in captured.out

    def test_text_output(self, capsys):
        args = argparse.Namespace()
        args.daemon_url = "http://localhost:8000"
        args.provider = "aws"
        args.budget = 10.0
        args.ephemeral = False
        args.json = False

        body = {
            "account_id": "acct-123",
            "provider": "aws",
            "access_key_id": "AKIA...",
            "budget_limit": 10.0,
            "ephemeral": False,
        }
        with patch("general_ludd.cli_account._http", new=Mock(return_value=body)):
            _cmd_create(args)
        captured = capsys.readouterr()
        assert "acct-123" in captured.out
        assert "aws" in captured.out


class TestCmdCleanup:
    def test_json_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.json = True

        body = {"deleted": [{"provider": "aws", "account_id": "acct-1"}], "kept": []}
        with patch("general_ludd.cli_account._http", new=Mock(return_value=body)):
            _cmd_cleanup(args)
        captured = capsys.readouterr()
        assert "acct-1" in captured.out

    def test_text_output(self, capsys):
        args = MagicMock()
        args.daemon_url = "http://localhost:8000"
        args.json = False

        body = {
            "deleted": [{"provider": "aws", "account_id": "acct-1", "deleted": True}],
            "kept": [{"provider": "gcp", "account_id": "acct-2"}],
        }
        with patch("general_ludd.cli_account._http", new=Mock(return_value=body)):
            _cmd_cleanup(args)
        captured = capsys.readouterr()
        assert "1" in captured.out


class TestAddAccountSubparser:
    def test_adds_backup_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_account_subparser(subparsers)

        ns = parser.parse_args(["account", "backup", "user1"])
        assert ns.account_command == "backup"
        assert ns.user_id == "user1"
        assert ns.func is _cmd_backup

    def test_adds_delete_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_account_subparser(subparsers)

        ns = parser.parse_args(["account", "delete", "user1", "--confirm"])
        assert ns.account_command == "delete"
        assert ns.func is _cmd_delete

    def test_adds_policy_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_account_subparser(subparsers)

        ns = parser.parse_args(["account", "policy", "openai"])
        assert ns.account_command == "policy"
        assert ns.func is _cmd_policy

    def test_adds_create_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_account_subparser(subparsers)

        ns = parser.parse_args(["account", "create", "--provider", "aws"])
        assert ns.account_command == "create"
        assert ns.provider == "aws"
        assert ns.func is _cmd_create

    def test_adds_cleanup_subcommand(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_account_subparser(subparsers)

        ns = parser.parse_args(["account", "cleanup"])
        assert ns.account_command == "cleanup"
        assert ns.func is _cmd_cleanup

    def test_default_daemon_url(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_account_subparser(subparsers)

        ns = parser.parse_args(["account", "backup", "user1"])
        assert ns.daemon_url == "http://localhost:8000"

    def test_json_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_account_subparser(subparsers)

        ns = parser.parse_args(["account", "backup", "user1", "--json"])
        assert ns.json is True
