"""Unit tests for `gludd connectors` CLI subcommands.

Covers:
- ``gludd connectors list``   -> GET /api/observe/sources, prints source names
- ``gludd connectors health`` -> GET /api/observe/health,  prints per-source health

Response shapes are owned by :mod:`general_ludd.routers.observe`:
- ``/api/observe/sources`` -> ``{"sources": [{name, kind, family}, ...], "by_kind": {...}, "count": N}``
- ``/api/observe/health``  -> ``{"health": {name: {ok: bool, ...}}, "count": N}``
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli import build_parser, main


def _ns(**kwargs: object) -> argparse.Namespace:
    defaults = {"daemon_url": "http://localhost:8000"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestConnectorsParserWiring:
    """The `connectors` subcommand must parse like sibling subcommands."""

    def test_connectors_list_invokes_cmd(self):
        with (
            patch("sys.argv", ["gludd", "connectors", "list"]),
            patch("general_ludd.cli._cmd_connectors_list") as mock_cmd,
        ):
            main()
        mock_cmd.assert_called_once()
        args = mock_cmd.call_args[0][0]
        assert args.daemon_url == "http://localhost:8000"

    def test_connectors_health_invokes_cmd(self):
        with (
            patch("sys.argv", ["gludd", "connectors", "health"]),
            patch("general_ludd.cli._cmd_connectors_health") as mock_cmd,
        ):
            main()
        mock_cmd.assert_called_once()

    def test_connectors_query_invokes_cmd(self):
        with (
            patch("sys.argv", ["gludd", "connectors", "query", "prod-logs"]),
            patch("general_ludd.cli._cmd_connectors_query") as mock_cmd,
        ):
            main()
        mock_cmd.assert_called_once()
        args = mock_cmd.call_args[0][0]
        assert args.source == "prod-logs"

    def test_connectors_no_subcommand_prints_help(self, capsys):
        with patch("sys.argv", ["gludd", "connectors"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "list" in out
        assert "health" in out
        assert "query" in out

    def test_connectors_custom_daemon_url(self):
        with (
            patch(
                "sys.argv",
                ["gludd", "connectors", "list", "--daemon-url", "http://node-2:9000"],
            ),
            patch("general_ludd.cli._cmd_connectors_list") as mock_cmd,
        ):
            main()
        args = mock_cmd.call_args[0][0]
        assert args.daemon_url == "http://node-2:9000"

    def test_connectors_in_subcommand_map(self):
        """`connectors` must be in the returned subcommand map so bare
        `gludd connectors` prints help instead of falling through."""
        _, subcommand_map = build_parser()
        assert "connectors" in subcommand_map


class TestConnectorsList:
    def test_list_prints_source_names(self, capsys):
        from general_ludd.cli import _cmd_connectors_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "sources": [
                {"name": "prod-logs", "kind": "logs", "family": "loki"},
                {"name": "metrics-1", "kind": "metrics", "family": "prometheus"},
            ],
            "by_kind": {"logs": ["prod-logs"], "metrics": ["metrics-1"]},
            "count": 2,
        }
        with patch("general_ludd.cli.httpx.get", return_value=mock_resp):
            _cmd_connectors_list(_ns())
        out = capsys.readouterr().out
        assert "prod-logs" in out
        assert "metrics-1" in out
        assert "loki" in out
        assert "prometheus" in out

    def test_list_empty(self, capsys):
        from general_ludd.cli import _cmd_connectors_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"sources": [], "by_kind": {}, "count": 0}
        with patch("general_ludd.cli.httpx.get", return_value=mock_resp):
            _cmd_connectors_list(_ns())
        out = capsys.readouterr().out.lower()
        assert "no" in out and "source" in out

    def test_list_server_error_exits(self):
        from general_ludd.cli import _cmd_connectors_list

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("general_ludd.cli.httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_connectors_list(_ns())

    def test_list_calls_correct_endpoint(self):
        from general_ludd.cli import _cmd_connectors_list

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"sources": [], "by_kind": {}, "count": 0}
        with patch("general_ludd.cli.httpx.get", return_value=mock_resp) as mock_get:
            _cmd_connectors_list(_ns(daemon_url="http://daemon:8000"))
        called_url = mock_get.call_args.args[0]
        assert called_url == "http://daemon:8000/api/observe/sources"


class TestConnectorsHealth:
    def test_health_prints_per_source_status(self, capsys):
        from general_ludd.cli import _cmd_connectors_health

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "health": {
                "prod-logs": {"ok": True, "latency_ms": 12},
                "metrics-1": {"ok": False, "error": "connection refused"},
            },
            "count": 2,
        }
        with patch("httpx.get", return_value=mock_resp):
            _cmd_connectors_health(_ns())
        out = capsys.readouterr().out
        assert "prod-logs" in out
        assert "metrics-1" in out
        assert "connection refused" in out

    def test_health_empty(self, capsys):
        from general_ludd.cli import _cmd_connectors_health

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"health": {}, "count": 0}
        with patch("httpx.get", return_value=mock_resp):
            _cmd_connectors_health(_ns())
        out = capsys.readouterr().out.lower()
        assert "no" in out and "source" in out

    def test_health_server_error_exits(self):
        from general_ludd.cli import _cmd_connectors_health

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_connectors_health(_ns())

    def test_health_calls_correct_endpoint(self):
        from general_ludd.cli import _cmd_connectors_health

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"health": {}, "count": 0}
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            _cmd_connectors_health(_ns(daemon_url="http://daemon:8000"))
        called_url = mock_get.call_args.args[0]
        assert called_url == "http://daemon:8000/api/observe/health"


class TestConnectorsQuery:
    def _ns(self, **kwargs: object) -> argparse.Namespace:
        defaults = {
            "daemon_url": "http://localhost:8000",
            "source": "prod-logs",
            "spec": "{}",
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_query_prints_records(self, capsys):
        from general_ludd.cli import _cmd_connectors_query

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "source": "prod-logs",
            "records": [
                {"level": "error", "msg": "boom"},
                {"level": "info", "msg": "ok"},
            ],
            "count": 2,
        }
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            _cmd_connectors_query(self._ns())
        out = capsys.readouterr().out
        assert "prod-logs" in out
        assert "boom" in out
        assert "ok" in out
        # body must contain the source name + spec dict
        posted_json = mock_post.call_args.kwargs.get("json") or {}
        assert posted_json.get("source") == "prod-logs"
        assert posted_json.get("spec") == {}

    def test_query_empty(self, capsys):
        from general_ludd.cli import _cmd_connectors_query

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "source": "prod-logs",
            "records": [],
            "count": 0,
        }
        with patch("httpx.post", return_value=mock_resp):
            _cmd_connectors_query(self._ns())
        out = capsys.readouterr().out.lower()
        assert "no" in out and "record" in out

    def test_query_server_error_exits(self):
        from general_ludd.cli import _cmd_connectors_query

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp), pytest.raises(SystemExit):
            _cmd_connectors_query(self._ns())

    def test_query_calls_correct_endpoint(self):
        from general_ludd.cli import _cmd_connectors_query

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "source": "prod-logs",
            "records": [],
            "count": 0,
        }
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            _cmd_connectors_query(self._ns(daemon_url="http://daemon:8000"))
        called_url = mock_post.call_args.args[0]
        assert called_url == "http://daemon:8000/api/observe/query"

    def test_query_custom_daemon_url(self):
        with (
            patch(
                "sys.argv",
                [
                    "gludd",
                    "connectors",
                    "query",
                    "metrics-1",
                    "--daemon-url",
                    "http://node-2:9000",
                ],
            ),
            patch("general_ludd.cli._cmd_connectors_query") as mock_cmd,
        ):
            main()
        args = mock_cmd.call_args[0][0]
        assert args.daemon_url == "http://node-2:9000"
        assert args.source == "metrics-1"

    def test_query_passes_spec_json_in_body(self):
        from general_ludd.cli import _cmd_connectors_query

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "source": "prod-logs",
            "records": [],
            "count": 0,
        }
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            _cmd_connectors_query(self._ns(spec='{"limit": 5, "level": "error"}'))
        posted_json = mock_post.call_args.kwargs.get("json") or {}
        assert posted_json.get("spec") == {"limit": 5, "level": "error"}

    def test_query_bad_spec_json_exits(self):
        from general_ludd.cli import _cmd_connectors_query

        with pytest.raises(SystemExit):
            _cmd_connectors_query(self._ns(spec="not-json{"))
