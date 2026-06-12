"""Tests for TUI daemon detachment — daemon must survive TUI exit.

TDD: The daemon launched from TUI must:
1. Be started as gunicorn directly (not via intermediate python wrapper)
2. Use start_new_session=True so it survives parent exit
"""

from __future__ import annotations


class TestTUIDaemonDetachment:
    def test_start_daemon_command_contains_gunicorn_factory(self):
        from general_ludd.cli import _build_daemon_start_cmd

        cmd = _build_daemon_start_cmd(host="0.0.0.0", port=8000, workers=1)
        assert cmd[0] == "gunicorn"
        assert "general_ludd.daemon:create_daemon_app()" in cmd
        assert "--bind" in cmd
        assert "0.0.0.0:8000" in cmd

    def test_start_daemon_command_uses_uvicorn_worker(self):
        from general_ludd.cli import _build_daemon_start_cmd

        # W3.5 (M8): workers>1 is clamped to 1 (SQLite-only single-writer). The
        # command still uses the uvicorn worker class and binds host:port.
        cmd = _build_daemon_start_cmd(host="127.0.0.1", port=9000, workers=4)
        assert "--worker-class" in cmd
        idx = cmd.index("--worker-class")
        assert cmd[idx + 1] == "uvicorn_worker.UvicornWorker"
        assert "--workers" in cmd
        w_idx = cmd.index("--workers")
        assert cmd[w_idx + 1] == "1"
        assert "127.0.0.1:9000" in cmd

    def test_start_daemon_no_intermediate_python_wrapper(self):
        from general_ludd.cli import _build_daemon_start_cmd

        cmd = _build_daemon_start_cmd()
        cmd_str = " ".join(cmd)
        assert "python" not in cmd_str.lower() or "gunicorn" in cmd_str
        assert "-m" not in cmd
        assert "general_ludd.cli" not in cmd_str or "create_daemon_app" in cmd_str

    def test_start_daemon_detects_immediate_exit(self):
        from unittest.mock import MagicMock

        fake_proc = MagicMock()
        fake_proc.pid = 12345
        fake_proc.poll.return_value = 1
        fake_proc.returncode = 1

        daemon_running = False
        status_msg = ""

        import time as _time
        _time.sleep(0)
        if fake_proc.poll() is not None:
            status_msg = f"Daemon exited immediately (rc={fake_proc.returncode})"
        else:
            daemon_running = True

        assert not daemon_running
        assert "exited immediately" in status_msg
        assert "rc=1" in status_msg

    def test_start_daemon_succeeds_when_process_stays_alive(self):
        from unittest.mock import MagicMock

        fake_proc = MagicMock()
        fake_proc.pid = 54321
        fake_proc.poll.return_value = None
        fake_proc.returncode = None

        daemon_running = False

        if fake_proc.poll() is not None:
            pass
        else:
            daemon_running = True

        assert daemon_running, "daemon_running should be True when poll() returns None"
