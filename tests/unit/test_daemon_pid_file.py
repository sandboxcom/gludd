"""Test TUI daemon lifecycle with PID file management."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


class TestDaemonPidFile:
    def test_write_pid_file_creates_file_with_correct_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            from general_ludd.cli import _write_daemon_pid_file

            _write_daemon_pid_file(str(pid_file), 12345, "http://localhost:8000")
            assert pid_file.exists()
            data = json.loads(pid_file.read_text())
            assert data["pid"] == 12345
            assert data["daemon_url"] == "http://localhost:8000"

    def test_write_pid_file_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "subdir" / "daemon.pid"
            from general_ludd.cli import _write_daemon_pid_file

            _write_daemon_pid_file(str(pid_file), 1, "http://localhost:8000")
            assert pid_file.exists()

    def test_read_pid_file_returns_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(json.dumps({"pid": 54321, "daemon_url": "http://0.0.0.0:9000"}))

            from general_ludd.cli import _read_daemon_pid_file

            data = _read_daemon_pid_file(str(pid_file))
            assert data is not None
            assert data["pid"] == 54321
            assert data["daemon_url"] == "http://0.0.0.0:9000"

    def test_read_pid_file_returns_none_when_missing(self):
        from general_ludd.cli import _read_daemon_pid_file

        data = _read_daemon_pid_file("/nonexistent/daemon.pid")
        assert data is None

    def test_read_pid_file_returns_none_on_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.write_text("not valid json {{{")

            from general_ludd.cli import _read_daemon_pid_file

            data = _read_daemon_pid_file(str(pid_file))
            assert data is None

    def test_is_daemon_alive_via_pid_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(json.dumps({"pid": os.getpid(), "daemon_url": "http://localhost:8000"}))

            from general_ludd.cli import _is_daemon_pid_alive

            assert _is_daemon_pid_alive(str(pid_file)) is True

    def test_is_daemon_alive_returns_false_for_dead_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "daemon.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(json.dumps({"pid": 99999, "daemon_url": "http://localhost:8000"}))

            from general_ludd.cli import _is_daemon_pid_alive

            assert _is_daemon_pid_alive(str(pid_file)) is False

    def test_get_daemon_pid_dir_creates_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_dir = Path(tmpdir) / "gludd-test"

            from general_ludd.cli import _get_daemon_pid_dir

            with patch("general_ludd.cli._DAEMON_PID_DIR", str(pid_dir)):
                result = _get_daemon_pid_dir()
                assert pid_dir.exists()
                assert result == str(pid_dir)

    def test_stop_daemon_via_pid_kills_process(self):
        import subprocess
        import sys

        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                pid_file = Path(tmpdir) / "daemon.pid"
                pid_file.write_text(json.dumps({
                    "pid": child.pid, "daemon_url": "http://localhost:8000",
                }))

                from general_ludd.cli import _stop_daemon_via_pid_file

                result = _stop_daemon_via_pid_file(str(pid_file))
                assert result is True

                child.wait(timeout=5)
                assert child.returncode is not None
        finally:
            try:
                child.kill()
                child.wait(timeout=3)
            except Exception:
                pass

    def test_child_survives_parent_with_detached_popen(self):
        import subprocess
        import sys

        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        assert child.pid > 0
        assert child.poll() is None
        child.kill()
        child.wait(timeout=3)
