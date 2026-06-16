"""Spawn-hardening tests for tui/keybindings.py gunicorn worker launch.

TUIKeyHandler._start_daemon spawns a gunicorn worker. It must validate
host/port/log-level/paths from config/UI state, build argv in list-form,
never use shell=True, and fail closed on bad values. Popen is mocked so
nothing is actually launched.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.tui.keybindings import (
    TUIKeyHandler,
    build_gunicorn_cmd,
    validate_gunicorn_spawn_args,
)


class TestValidateGunicornSpawnArgs:
    def test_normal_args_pass(self):
        validate_gunicorn_spawn_args(host="0.0.0.0", port=8000, workers=1)

    @pytest.mark.parametrize("bad_port", [0, -1, 65536, 99999])
    def test_out_of_range_port_rejected(self, bad_port):
        with pytest.raises(ValueError, match="port"):
            validate_gunicorn_spawn_args(host="0.0.0.0", port=bad_port, workers=1)

    @pytest.mark.parametrize("bad_port", ["8000; rm", "abc", None, 80.5])
    def test_non_int_port_rejected(self, bad_port):
        with pytest.raises((ValueError, TypeError)):
            validate_gunicorn_spawn_args(host="0.0.0.0", port=bad_port, workers=1)

    @pytest.mark.parametrize(
        "bad_host",
        ["0.0.0.0; rm -rf /", "$(reboot)", "h && curl x", "a:b", "x y", "`id`", "", "h\nn"],
    )
    def test_injection_host_rejected(self, bad_host):
        with pytest.raises(ValueError, match="host"):
            validate_gunicorn_spawn_args(host=bad_host, port=8000, workers=1)

    def test_valid_hosts_pass(self):
        for host in ("0.0.0.0", "127.0.0.1", "localhost", "::1"):
            validate_gunicorn_spawn_args(host=host, port=8000, workers=1)

    @pytest.mark.parametrize("bad_workers", [0, -1, "1; ls", None, 1.5])
    def test_bad_workers_rejected(self, bad_workers):
        with pytest.raises((ValueError, TypeError)):
            validate_gunicorn_spawn_args(host="0.0.0.0", port=8000, workers=bad_workers)

    @pytest.mark.parametrize("bad_level", ["info; rm", "$(x)", "a\nb"])
    def test_bad_log_level_rejected(self, bad_level):
        with pytest.raises(ValueError, match="log"):
            validate_gunicorn_spawn_args(
                host="0.0.0.0", port=8000, workers=1, log_level=bad_level
            )

    def test_path_outside_confinement_rejected(self, tmp_path):
        confine = tmp_path / "root"
        confine.mkdir()
        with pytest.raises(ValueError, match="path"):
            validate_gunicorn_spawn_args(
                host="0.0.0.0",
                port=8000,
                workers=1,
                paths=["/etc/passwd"],
                confine_root=str(confine),
            )


class TestBuildGunicornCmd:
    def test_builds_expected_listform_argv(self):
        cmd = build_gunicorn_cmd(host="127.0.0.1", port=9000, workers=2)
        assert isinstance(cmd, list)
        assert cmd[0] == "gunicorn"
        assert "general_ludd.daemon:create_daemon_app()" in cmd
        assert "--worker-class" in cmd
        wc = cmd.index("--worker-class")
        assert cmd[wc + 1] == "uvicorn_worker.UvicornWorker"
        assert "--bind" in cmd
        b = cmd.index("--bind")
        assert cmd[b + 1] == "127.0.0.1:9000"
        assert "--workers" in cmd
        w = cmd.index("--workers")
        assert cmd[w + 1] == "2"
        # No shell metacharacters smuggled in.
        for tok in cmd:
            assert ";" not in tok
            assert "&&" not in tok
            assert "$(" not in tok

    def test_build_rejects_bad_inputs(self):
        with pytest.raises(ValueError):
            build_gunicorn_cmd(host="x; rm", port=8000, workers=1)
        with pytest.raises(ValueError):
            build_gunicorn_cmd(host="0.0.0.0", port=99999, workers=1)


class TestStartDaemonSpawnHardening:
    def _handler(self, **state_over):
        state = {
            "daemon_url": "http://127.0.0.1:8000",
            "status_msg": "",
            "daemon_running": False,
        }
        state.update(state_over)
        return TUIKeyHandler(state), state

    def test_normal_spawn_listform_no_shell(self):
        handler, _state = self._handler()
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            proc = MagicMock()
            proc.pid = 777
            # Report alive so health-check path is taken.
            proc.poll.return_value = None
            return proc

        # Prevent real HTTP and real waiting.
        with (
            patch("general_ludd.tui.keybindings.httpx") as httpx_mock,
            patch.object(subprocess, "Popen", side_effect=fake_popen),
            patch("time.sleep", return_value=None),
        ):
            # healthz: first call (pre-spawn) raises -> not running; post-spawn returns 200.
            ok = MagicMock()
            ok.status_code = 200
            httpx_mock.get.side_effect = [Exception("down"), ok]
            handler._start_daemon()

        assert "cmd" in captured, "Popen should have been called for a valid spawn"
        assert isinstance(captured["cmd"], list)
        assert captured["cmd"][0] == "gunicorn"
        assert captured["kwargs"].get("shell", False) is False

    def test_injection_host_fails_closed_no_popen(self):
        handler, state = self._handler(daemon_host="0.0.0.0; rm -rf /")

        with (
            patch("general_ludd.tui.keybindings.httpx") as httpx_mock,
            patch.object(subprocess, "Popen") as popen,
            patch("time.sleep", return_value=None),
        ):
            httpx_mock.get.side_effect = Exception("down")
            handler._start_daemon()

        popen.assert_not_called()
        assert state["daemon_running"] is False
        assert "fail" in state["status_msg"].lower() or "invalid" in state["status_msg"].lower()

    def test_out_of_range_port_fails_closed_no_popen(self):
        handler, state = self._handler(daemon_port=99999)

        with (
            patch("general_ludd.tui.keybindings.httpx") as httpx_mock,
            patch.object(subprocess, "Popen") as popen,
            patch("time.sleep", return_value=None),
        ):
            httpx_mock.get.side_effect = Exception("down")
            handler._start_daemon()

        popen.assert_not_called()
        assert state["daemon_running"] is False
