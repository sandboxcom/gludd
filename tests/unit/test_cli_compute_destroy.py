"""Unit tests for ``gludd compute destroy`` CLI subcommand.

Covers:
- ``gludd compute destroy INSTANCE_ID`` -> DELETE /admin/compute/destroy/{instance_id}
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, Mock, patch

import pytest

from general_ludd.cli import build_parser, main


def _ns(**kwargs: object) -> argparse.Namespace:
    defaults = {"daemon_url": "http://localhost:8000"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestComputeDestroyParserWiring:
    """The ``compute destroy`` subcommand must parse like sibling subcommands."""

    def test_destroy_invokes_cmd(self):
        with (
            patch("sys.argv", ["gludd", "compute", "destroy", "inst-1234"]),
            patch("general_ludd.cli._cmd_compute_destroy") as mock_cmd,
        ):
            main()
        mock_cmd.assert_called_once()
        args = mock_cmd.call_args[0][0]
        assert args.instance_id == "inst-1234"
        assert args.daemon_url == "http://localhost:8000"

    def test_destroy_custom_daemon_url(self):
        with (
            patch(
                "sys.argv",
                ["gludd", "compute", "destroy", "inst-5678", "--daemon-url", "http://node-2:9000"],
            ),
            patch("general_ludd.cli._cmd_compute_destroy") as mock_cmd,
        ):
            main()
        args = mock_cmd.call_args[0][0]
        assert args.instance_id == "inst-5678"
        assert args.daemon_url == "http://node-2:9000"

    def test_compute_in_subcommand_map(self):
        """``compute`` must be in the returned subcommand map."""
        _, subcommand_map = build_parser()
        assert "compute" in subcommand_map


class TestComputeDestroy:
    """Handler behaviour: HTTP call + output."""

    def test_destroy_prints_destroyed_id(self, capsys):
        from general_ludd.cli import _cmd_compute_destroy

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"destroyed": "inst-1234"}
        with patch("general_ludd.cli.httpx.delete", new=Mock(return_value=mock_resp)):
            _cmd_compute_destroy(_ns(instance_id="inst-1234"))
        out = capsys.readouterr().out
        assert "Destroyed" in out
        assert "inst-1234" in out

    def test_destroy_calls_correct_endpoint(self):
        from general_ludd.cli import _cmd_compute_destroy

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"destroyed": "inst-42"}
        with patch("general_ludd.cli.httpx.delete", new=Mock(return_value=mock_resp)) as mock_delete:
            _cmd_compute_destroy(_ns(instance_id="inst-42", daemon_url="http://daemon:8000"))
        called_url = mock_delete.call_args.args[0]
        assert called_url == "http://daemon:8000/admin/compute/destroy/inst-42"

    def test_destroy_not_found_exits(self):
        from general_ludd.cli import _cmd_compute_destroy

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Unknown instance_id"
        with patch("general_ludd.cli.httpx.delete", new=Mock(return_value=mock_resp)), pytest.raises(SystemExit):
            _cmd_compute_destroy(_ns(instance_id="inst-ghost"))

    def test_destroy_server_error_exits(self):
        from general_ludd.cli import _cmd_compute_destroy

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "compute destroy failed"
        with patch("general_ludd.cli.httpx.delete", new=Mock(return_value=mock_resp)), pytest.raises(SystemExit):
            _cmd_compute_destroy(_ns(instance_id="inst-broken"))

    def test_destroy_connect_error_exits(self):
        import httpx

        from general_ludd.cli import _cmd_compute_destroy

        with (
            patch("general_ludd.cli.httpx.delete", side_effect=httpx.ConnectError("refused")),
            pytest.raises(SystemExit),
        ):
            _cmd_compute_destroy(_ns(instance_id="inst-1234"))
