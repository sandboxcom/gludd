"""Hardening tests for daemon-spawn argv construction in cli.py.

cli._cmd_daemon spawns the daemon via subprocess.Popen(cmd, start_new_session=True,
close_fds=True) where ``cmd`` is built by ``_build_daemon_start_cmd`` from
CLI-supplied args (host, port) and the daemon env is built by ``_build_daemon_env``
from CLI-supplied log-level and path args. None of those inputs are trusted: a
malicious or fat-fingered ``--host``, ``--port``, ``--log-level`` or ``*-dir`` must
not be able to smuggle shell metacharacters / extra argv tokens / out-of-range
values into the spawned process.

These tests prove:
1. an out-of-range / non-numeric port is rejected,
2. an injection-y host (shell metachars, whitespace, embedded argv flags) is rejected,
3. a bad log-level (outside the allowlist) is rejected,
4. injection-y / non-confined path args are rejected,
5. a normal invocation builds the expected argv as a *list* (never a shell string),
6. Popen is invoked with a list argv and shell is never used.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd import cli


# --------------------------------------------------------------------------- #
# port validation
# --------------------------------------------------------------------------- #
class TestPortValidation:
    @pytest.mark.parametrize("bad_port", [0, -1, 65536, 99999, 123456789])
    def test_out_of_range_port_rejected(self, bad_port: int) -> None:
        with pytest.raises(ValueError, match="port"):
            cli._build_daemon_start_cmd(host="127.0.0.1", port=bad_port)

    @pytest.mark.parametrize("bad_port", ["8000; rm -rf /", "8000 --bind", "abc", "80a0"])
    def test_non_numeric_port_rejected(self, bad_port: str) -> None:
        with pytest.raises(ValueError, match="port"):
            cast(Any, cli)._build_daemon_start_cmd(host="127.0.0.1", port=bad_port)

    @pytest.mark.parametrize("good_port", [1, 80, 8000, 65535])
    def test_in_range_port_accepted(self, good_port: int) -> None:
        cmd = cli._build_daemon_start_cmd(host="127.0.0.1", port=good_port)
        assert f"127.0.0.1:{good_port}" in cmd


# --------------------------------------------------------------------------- #
# host validation
# --------------------------------------------------------------------------- #
class TestHostValidation:
    @pytest.mark.parametrize(
        "bad_host",
        [
            "127.0.0.1; rm -rf /",
            "$(whoami)",
            "`id`",
            "host && curl evil",
            "host\nport",
            "host with space",
            "1.2.3.4 --bind",
            "--bind",
            "-x",
            "host|nc evil 1",
            "",
        ],
    )
    def test_injection_host_rejected(self, bad_host: str) -> None:
        with pytest.raises(ValueError, match="host"):
            cli._build_daemon_start_cmd(host=bad_host, port=8000)

    @pytest.mark.parametrize(
        "good_host",
        ["127.0.0.1", "0.0.0.0", "localhost", "::1", "example.com", "sub.example.co.uk", "10.0.0.5"],
    )
    def test_valid_host_accepted(self, good_host: str) -> None:
        cmd = cli._build_daemon_start_cmd(host=good_host, port=8000)
        assert isinstance(cmd, list)
        # host appears in the --bind token
        joined = "".join(cmd)
        assert good_host in joined


# --------------------------------------------------------------------------- #
# log-level allowlist validation
# --------------------------------------------------------------------------- #
class TestLogLevelValidation:
    @pytest.mark.parametrize("bad_level", ["trace", "verbose", "info; rm -rf /", "INFO --x", "", "12"])
    def test_bad_log_level_rejected(self, bad_level: str) -> None:
        with pytest.raises(ValueError, match=r"log.level"):
            cli._build_daemon_env(log_level=bad_level)

    @pytest.mark.parametrize("good_level", ["debug", "info", "warning", "error", "DEBUG", "Info"])
    def test_allowlisted_log_level_accepted(self, good_level: str) -> None:
        env = cli._build_daemon_env(log_level=good_level)
        # info is the default and is not emitted; others are
        if good_level.lower() != "info":
            assert env["GLUDD_LOG_LEVEL"].lower() == good_level.lower()


# --------------------------------------------------------------------------- #
# path-arg confinement / injection
# --------------------------------------------------------------------------- #
class TestPathValidation:
    @pytest.mark.parametrize(
        "bad_path",
        [
            "/etc/passwd\nGLUDD_PSK=leak",
            "/some/path; rm -rf /",
            "/path\x00/null",
            "/path`id`",
            "/path$(whoami)",
        ],
    )
    def test_injection_path_rejected(self, bad_path: str) -> None:
        with pytest.raises(ValueError, match=r"path|dir"):
            cli._build_daemon_env(config_dir=bad_path)

    def test_normal_path_accepted(self, tmp_path) -> None:
        env = cli._build_daemon_env(config_dir=str(tmp_path))
        assert env["GLUDD_CONFIG_DIR"] == str(tmp_path)


# --------------------------------------------------------------------------- #
# normal invocation builds expected argv as a list
# --------------------------------------------------------------------------- #
class TestArgvShape:
    def test_normal_invocation_builds_expected_argv(self) -> None:
        cmd = cli._build_daemon_start_cmd(host="0.0.0.0", port=8000, workers=1)
        assert cmd == [
            "gunicorn",
            "general_ludd.daemon:create_daemon_app()",
            "--worker-class",
            "uvicorn_worker.UvicornWorker",
            "--workers",
            "1",
            "--bind",
            "0.0.0.0:8000",
        ]
        assert isinstance(cmd, list)
        assert all(isinstance(tok, str) for tok in cmd)

    def test_cmd_daemon_popen_uses_list_argv_no_shell(self) -> None:
        """_cmd_daemon must pass a list argv to Popen and never use a shell."""
        args = MagicMock()
        args.host = "127.0.0.1"
        args.port = 8000
        args.log_level = "info"
        args.workers = 1
        args.tick_interval = 1.0
        args.config_dir = None
        args.templates_dir = None
        args.playbooks_dir = None
        # pid_file unset on the real args namespace; a MagicMock would return a
        # MagicMock from getattr() and corrupt the daemon pid-file JSON.
        args.pid_file = None

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0
        fake_proc.returncode = 0

        with (
            patch("subprocess.Popen", return_value=fake_proc) as mock_popen,
            patch("sys.exit") as mock_exit,
            patch("signal.signal"),
        ):
            cli._cmd_daemon(args)

        assert mock_popen.called
        call_args, call_kwargs = mock_popen.call_args
        argv = call_args[0]
        assert isinstance(argv, list)
        assert argv[0] == "gunicorn"
        assert "127.0.0.1:8000" in argv
        # shell must never be enabled for a spawn built from CLI args
        assert call_kwargs.get("shell", False) is False
        assert call_kwargs.get("start_new_session") is True
        assert call_kwargs.get("close_fds") is True
        mock_exit.assert_called()
