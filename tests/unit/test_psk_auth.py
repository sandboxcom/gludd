from __future__ import annotations

import contextlib
import os
from unittest.mock import MagicMock, patch


class TestPSKAuth:
    def test_psk_env_var_activates_auth(self):
        with patch.dict(os.environ, {"GLUDD_PSK": "test-key-123"}):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app()
            assert app.state._psk == "test-key-123"

    def test_no_psk_env_var_disables_auth(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GLUDD_PSK", None)
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app()
            assert app.state._psk == ""

    def test_healthz_is_public(self):
        with patch.dict(os.environ, {"GLUDD_PSK": "secret"}):
            from fastapi.testclient import TestClient

            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app()
            client = TestClient(app)
            resp = client.get("/healthz")
            assert resp.status_code == 200


class TestDaemonStartPSK:
    def test_external_host_generates_psk(self, capsys):
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(wait=MagicMock(return_value=0))
            with patch("general_ludd.daemon.create_daemon_app"):
                args = MagicMock()
                args.host = "0.0.0.0"
                args.port = 8000
                args.workers = 1
                args.log_level = "info"
                args.tick_interval = 1.0
                args.config_dir = None
                from general_ludd.cli import _cmd_daemon

                with contextlib.suppress(SystemExit):
                    _cmd_daemon(args)
                captured = capsys.readouterr()
                assert "PSK" in captured.out or "psk" in captured.out.lower()

    def test_localhost_no_psk(self, capsys):
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(wait=MagicMock(return_value=0))
            with patch("general_ludd.daemon.create_daemon_app"):
                args = MagicMock()
                args.host = "127.0.0.1"
                args.port = 8000
                args.workers = 1
                args.log_level = "info"
                args.tick_interval = 1.0
                args.config_dir = None
                from general_ludd.cli import _cmd_daemon

                with contextlib.suppress(SystemExit):
                    _cmd_daemon(args)
                captured = capsys.readouterr()
                assert "PSK" not in captured.out
