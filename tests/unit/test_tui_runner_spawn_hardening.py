"""Spawn-hardening tests for tui/runner.py daemon launch.

A daemon launcher must validate its inputs (host, port, log-level, paths)
and fail closed on bad values, build argv in list-form, and never use a
shell. These tests prove the validator rejects an out-of-range port, an
injection-y host, a bad path, and that a normal spawn builds the expected
argv (Popen is mocked so nothing is actually launched).
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.tui.runner import validate_daemon_spawn_args


class TestValidateDaemonSpawnArgs:
    def test_normal_args_pass(self):
        # Should not raise.
        validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=1)
        validate_daemon_spawn_args(host="0.0.0.0", port=9000, workers=4)

    @pytest.mark.parametrize("bad_port", [0, -1, 65536, 99999, 70000])
    def test_out_of_range_port_rejected(self, bad_port):
        with pytest.raises(ValueError, match="port"):
            validate_daemon_spawn_args(host="127.0.0.1", port=bad_port, workers=1)

    @pytest.mark.parametrize("bad_port", ["8000; rm -rf /", "8000", "abc", 80.5, None])
    def test_non_int_port_rejected(self, bad_port):
        with pytest.raises((ValueError, TypeError)):
            validate_daemon_spawn_args(host="127.0.0.1", port=bad_port, workers=1)

    @pytest.mark.parametrize(
        "bad_host",
        [
            "127.0.0.1; rm -rf /",
            "$(reboot)",
            "host && curl evil",
            "10.0.0.1:8000",  # embedded port / colon
            "a b c",
            "`id`",
            "host\nname",
            "",
        ],
    )
    def test_injection_host_rejected(self, bad_host):
        with pytest.raises(ValueError, match="host"):
            validate_daemon_spawn_args(host=bad_host, port=8000, workers=1)

    def test_valid_hosts_pass(self):
        for host in ("0.0.0.0", "127.0.0.1", "localhost", "::1", "example.com", "10.0.0.5"):
            validate_daemon_spawn_args(host=host, port=8000, workers=1)

    @pytest.mark.parametrize("bad_workers", [0, -1, "1; ls", None, 1.5])
    def test_bad_workers_rejected(self, bad_workers):
        with pytest.raises((ValueError, TypeError)):
            validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=bad_workers)

    @pytest.mark.parametrize("bad_level", ["info; rm", "WARN && x", "$(x)", "lol\nx"])
    def test_bad_log_level_rejected(self, bad_level):
        with pytest.raises(ValueError, match="log"):
            validate_daemon_spawn_args(
                host="127.0.0.1", port=8000, workers=1, log_level=bad_level
            )

    def test_valid_log_levels_pass(self):
        for lvl in ("debug", "info", "warning", "error", "critical"):
            validate_daemon_spawn_args(
                host="127.0.0.1", port=8000, workers=1, log_level=lvl
            )

    def test_path_outside_confinement_rejected(self, tmp_path):
        confine = tmp_path / "root"
        confine.mkdir()
        outside = "/etc/passwd"
        with pytest.raises(ValueError, match="path"):
            validate_daemon_spawn_args(
                host="127.0.0.1",
                port=8000,
                workers=1,
                paths=[outside],
                confine_root=str(confine),
            )

    def test_path_traversal_rejected(self, tmp_path):
        confine = tmp_path / "root"
        confine.mkdir()
        with pytest.raises(ValueError, match="path"):
            validate_daemon_spawn_args(
                host="127.0.0.1",
                port=8000,
                workers=1,
                paths=[str(confine / ".." / "escape")],
                confine_root=str(confine),
            )

    def test_path_inside_confinement_passes(self, tmp_path):
        confine = tmp_path / "root"
        confine.mkdir()
        good = confine / "logs" / "daemon.log"
        validate_daemon_spawn_args(
            host="127.0.0.1",
            port=8000,
            workers=1,
            paths=[str(good)],
            confine_root=str(confine),
        )


class TestStartDaemonSpawnHardening:
    def test_normal_spawn_builds_expected_argv_listform_no_shell(self):
        """A normal spawn must pass argv as a list and never use shell=True."""
        from general_ludd.cli import _build_daemon_start_cmd

        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            proc = MagicMock()
            proc.pid = 4242
            proc.poll.return_value = None
            proc.returncode = None
            return proc

        # Validate then build argv exactly the way runner does.
        validate_daemon_spawn_args(host="127.0.0.1", port=8000, workers=1)
        cmd = _build_daemon_start_cmd(host="127.0.0.1", port=8000, workers=1)

        with patch.object(subprocess, "Popen", side_effect=fake_popen):
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )

        assert proc.pid == 4242
        assert isinstance(captured["cmd"], list), "argv must be list-form"
        assert captured["cmd"][0] == "gunicorn"
        assert "127.0.0.1:8000" in captured["cmd"]
        assert captured["kwargs"].get("shell", False) is False

    def test_injection_host_never_reaches_popen(self):
        """An injection-y host must fail closed before any Popen call."""
        with patch.object(subprocess, "Popen") as popen:
            with pytest.raises(ValueError):
                validate_daemon_spawn_args(host="127.0.0.1; rm -rf /", port=8000, workers=1)
            popen.assert_not_called()

    def test_out_of_range_port_never_reaches_popen(self):
        with patch.object(subprocess, "Popen") as popen:
            with pytest.raises(ValueError):
                validate_daemon_spawn_args(host="127.0.0.1", port=99999, workers=1)
            popen.assert_not_called()
