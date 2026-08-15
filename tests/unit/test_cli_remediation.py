"""Unit tests for cli_remediation.py."""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

import general_ludd.cli_remediation as cli_remediation


class TestPskHeaders:
    def test_no_psk_env(self, monkeypatch):
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        headers = cli_remediation._psk_headers()
        assert "Content-Type" in headers
        assert "Authorization" not in headers

    def test_with_psk_env(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
        headers = cli_remediation._psk_headers()
        assert headers["Authorization"] == "Bearer secret123"


class TestPrintJson:
    def test_prints_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_remediation._print_json({"a": 1})
        output = json.loads(buf.getvalue())
        assert output == {"a": 1}


class TestCmdScan:
    def test_empty_result(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"blocked_tasks": []}
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project=None,
                json=False,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_scan(args)
            assert "(no blocked tasks)" in buf.getvalue()

    def test_with_results(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "blocked_tasks": [
                {
                    "blocker_kind": "human_input",
                    "todo_id": "t1",
                    "suggested_remediation": "dispatch_agent",
                    "blocked_duration_seconds": 7200,
                    "blocker_summary": "Needs input",
                }
            ]
        }
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project=None,
                json=False,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_scan(args)
            output = buf.getvalue()
            assert "Found 1 blocked task" in output
            assert "t1" in output

    def test_json_mode(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"blocked_tasks": []}
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_scan(args)
            output = json.loads(buf.getvalue())
            assert output == {"blocked_tasks": []}

    def test_scan_passes_project_filter(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"blocked_tasks": []}
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp) as mock_req:
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project="p1",
                json=False,
            )
            cli_remediation._cmd_scan(args)
            call_kwargs = mock_req.call_args
            assert "params" in call_kwargs.kwargs
            assert call_kwargs.kwargs["params"]["project_id"] == "p1"


class TestCmdChronic:
    def test_empty_result(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"chronic_blockers": []}
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project=None,
                lookback_days=None,
                json=False,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_chronic(args)
            assert "(no chronic blockers)" in buf.getvalue()

    def test_with_blockers(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "chronic_blockers": [
                {
                    "task_type": "bug_fix",
                    "blocker_kind": "human_input",
                    "incident_count": 7,
                    "last_seen": "2026-01-01T00:00:00Z",
                }
            ]
        }
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project=None,
                lookback_days=None,
                json=False,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_chronic(args)
            output = buf.getvalue()
            assert "Found 1 chronic blocker" in output
            assert "bug_fix" in output

    def test_json_mode(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"chronic_blockers": []}
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project=None,
                lookback_days=14,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_chronic(args)
            output = json.loads(buf.getvalue())
            assert output == {"chronic_blockers": []}


class TestCmdHistory:
    def test_empty_result(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"actions": []}
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project=None,
                since=None,
                limit=100,
                json=False,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_history(args)
            assert "(no remediation history)" in buf.getvalue()

    def test_with_actions(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "actions": [
                {
                    "action_kind": "dispatch_agent",
                    "ok": True,
                    "blocked_todo_id": "t1",
                    "created_at": "2026-01-01T00:00:00Z",
                    "summary": "Dispatched agent",
                }
            ]
        }
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000",
                project=None,
                since=None,
                limit=100,
                json=False,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_history(args)
            output = buf.getvalue()
            assert "Found 1 remediation action" in output
            assert "dispatch_agent" in output


class TestCmdConfigShow:
    def test_pretty_output(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "human_input_block_hours": 24,
            "permission_escalation_block_hours": 4,
            "max_requeues_before_chronic": 3,
            "chronic_lookback_days": 7,
            "min_chronic_incidents": 5,
            "retry_delay_hours": 4,
        }
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000", json=False
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_remediation._cmd_config_show(args)
            output = buf.getvalue()
            assert "human_input_block_hours" in output
            assert "chronic_lookback_days" in output
            assert "retry_delay_hours" in output


class TestHttpHelper:
    def test_success_returns_json(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            result = cli_remediation._http("GET", "http://example.com")
            assert result == {"ok": True}

    def test_non_ok_status_exits(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Server error"
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp), pytest.raises(SystemExit):
            cli_remediation._http("GET", "http://example.com")

    def test_json_parse_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad json")
        with patch.object(cli_remediation.httpx, "request", return_value=mock_resp):
            result = cli_remediation._http("GET", "http://example.com")
            assert result is None


class TestAddSubparser:
    def test_registers_remediation_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_remediation.add_remediation_subparser(sub)
        ns = parser.parse_args(["remediation", "scan"])
        assert ns.remediation_command == "scan"
        assert ns.func == cli_remediation._cmd_scan

    def test_registers_chronic_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_remediation.add_remediation_subparser(sub)
        ns = parser.parse_args(["remediation", "chronic-blockers"])
        assert ns.remediation_command == "chronic-blockers"
        assert ns.func == cli_remediation._cmd_chronic

    def test_registers_history_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_remediation.add_remediation_subparser(sub)
        ns = parser.parse_args(["remediation", "history"])
        assert ns.remediation_command == "history"
        assert ns.func == cli_remediation._cmd_history

    def test_registers_config_show_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_remediation.add_remediation_subparser(sub)
        ns = parser.parse_args(["remediation", "config", "show"])
        assert ns.remediation_config_command == "show"
        assert ns.func == cli_remediation._cmd_config_show

    def test_registers_config_edit_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_remediation.add_remediation_subparser(sub)
        ns = parser.parse_args(["remediation", "config", "edit"])
        assert ns.func == cli_remediation._cmd_config_edit
