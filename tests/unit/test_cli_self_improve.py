"""Unit tests for cli_self_improve.py."""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

import general_ludd.cli_self_improve as cli_self_improve


class TestPskHeaders:
    def test_no_psk_env(self, monkeypatch):
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        headers = cli_self_improve._psk_headers()
        assert "Content-Type" in headers
        assert "Authorization" not in headers

    def test_with_psk_env(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
        headers = cli_self_improve._psk_headers()
        assert headers["Authorization"] == "Bearer secret123"


class TestPrintJson:
    def test_prints_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_self_improve._print_json({"a": 1})
        output = json.loads(buf.getvalue())
        assert output == {"a": 1}


class TestCmdPending:
    def test_empty_result(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"pending": []}
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000", json=False
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_self_improve._cmd_pending(args)
            assert "(no self-improve todos awaiting approval)" in buf.getvalue()

    def test_with_pending_todos(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "pending": [
                {
                    "todo_id": "t1",
                    "priority": "high",
                    "title": "Improve error handling",
                }
            ]
        }
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000", json=False
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_self_improve._cmd_pending(args)
            output = buf.getvalue()
            assert "t1" in output
            assert "high" in output
            assert "Improve error handling" in output

    def test_json_mode(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"pending": []}
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000", json=True
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_self_improve._cmd_pending(args)
            output = json.loads(buf.getvalue())
            assert output == {"pending": []}

    def test_resp_is_none_handled(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = None
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                daemon_url="http://localhost:8000", json=False
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_self_improve._cmd_pending(args)
            assert "(no self-improve todos awaiting approval)" in buf.getvalue()


class TestCmdApprove:
    def test_approve_pretty(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "queued"}
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                todo_id="t1",
                daemon_url="http://localhost:8000",
                json=False,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_self_improve._cmd_approve(args)
            assert "Approved (queued): t1" in buf.getvalue()

    def test_approve_json(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "queued"}
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                todo_id="t1",
                daemon_url="http://localhost:8000",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_self_improve._cmd_approve(args)
            output = json.loads(buf.getvalue())
            assert output["status"] == "queued"


class TestCmdReject:
    def test_reject_pretty(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "cancelled"}
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                todo_id="t1",
                daemon_url="http://localhost:8000",
                json=False,
                reason="Not needed",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_self_improve._cmd_reject(args)
            assert "Rejected (cancelled): t1" in buf.getvalue()

    def test_reject_without_reason(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "cancelled"}
        with patch.object(
            cli_self_improve.httpx, "request", return_value=mock_resp
        ) as mock_req:
            args = argparse.Namespace(
                todo_id="t1",
                daemon_url="http://localhost:8000",
                json=False,
                reason=None,
            )
            cli_self_improve._cmd_reject(args)
            # json_body should be {} when no reason
            call_kwargs = mock_req.call_args
            assert call_kwargs.kwargs["json"] == {}

    def test_reject_json(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "cancelled"}
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            args = argparse.Namespace(
                todo_id="t2",
                daemon_url="http://localhost:8000",
                json=True,
                reason="Obsolete",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_self_improve._cmd_reject(args)
            output = json.loads(buf.getvalue())
            assert output["status"] == "cancelled"


class TestHttpHelper:
    def test_success_returns_json(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            result = cli_self_improve._http("GET", "http://example.com")
            assert result == {"ok": True}

    def test_non_ok_status_exits(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Server error"
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp), pytest.raises(SystemExit):
            cli_self_improve._http("GET", "http://example.com")

    def test_network_error_exits(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        with patch.object(
            cli_self_improve.httpx,
            "request",
            side_effect=ConnectionError("refused"),
        ), pytest.raises(SystemExit):
            cli_self_improve._http("GET", "http://example.com")

    def test_json_parse_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad json")
        with patch.object(cli_self_improve.httpx, "request", return_value=mock_resp):
            result = cli_self_improve._http("GET", "http://example.com")
            assert result is None


class TestAddSubparser:
    def test_registers_pending_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_self_improve.add_self_improve_subparser(sub)
        ns = parser.parse_args(["self-improve", "pending"])
        assert ns.self_improve_command == "pending"
        assert ns.func == cli_self_improve._cmd_pending

    def test_registers_approve_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_self_improve.add_self_improve_subparser(sub)
        ns = parser.parse_args(["self-improve", "approve", "todo-1"])
        assert ns.self_improve_command == "approve"
        assert ns.todo_id == "todo-1"
        assert ns.func == cli_self_improve._cmd_approve

    def test_registers_reject_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_self_improve.add_self_improve_subparser(sub)
        ns = parser.parse_args(
            ["self-improve", "reject", "todo-2", "--reason", "stale"]
        )
        assert ns.self_improve_command == "reject"
        assert ns.todo_id == "todo-2"
        assert ns.reason == "stale"
        assert ns.func == cli_self_improve._cmd_reject

    def test_default_daemon_url(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_self_improve.add_self_improve_subparser(sub)
        ns = parser.parse_args(["self-improve", "pending"])
        assert ns.daemon_url == "http://localhost:8000"
