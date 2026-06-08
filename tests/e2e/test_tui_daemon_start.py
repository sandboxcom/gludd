"""PTY-based E2E test: spawn TUI, press 's' to start daemon, verify daemon comes up.

Key design principles:
1. Kill any pre-existing daemon BEFORE the test
2. Verify no daemon is running before pressing 's'
3. Verify the PID that appears is NEW (not a leftover)
4. Verify healthz actually responds
5. NO escape hatches — if the daemon doesn't start, the test FAILS
6. Verify TUI output reflects the actual daemon state
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time

import httpx
import pytest

GLUDD_CMD = [sys.executable, "-m", "general_ludd.cli", "tui"]

_DAEMON_PID_DIR = os.path.expanduser("~/.local/share/general-ludd")
_DAEMON_PID_FILE = os.path.join(_DAEMON_PID_DIR, "daemon.pid")
_DAEMON_URL = "http://localhost:8000"


def _read_pid_file() -> dict | None:
    try:
        with open(_DAEMON_PID_FILE) as f:
            data = json.load(f)
            if isinstance(data, dict) and "pid" in data:
                return data
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        pass
    return None


def _is_port_listening(url: str) -> bool:
    try:
        resp = httpx.get(f"{url}/healthz", timeout=1.0)
        return resp.status_code == 200
    except Exception:
        return False


def _kill_daemon() -> None:
    data = _read_pid_file()
    if data is None:
        return
    pid = data.get("pid")
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                break
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    with contextlib.suppress(OSError):
        os.unlink(_DAEMON_PID_FILE)


def _ensure_no_daemon() -> None:
    _kill_daemon()
    time.sleep(0.5)
    assert not _is_port_listening(_DAEMON_URL), (
        f"Port 8000 still in use after killing daemon. "
        f"Something else is listening on {_DAEMON_URL}/healthz"
    )


def _collect_pty_output(master_fd: int, timeout: float = 1.0) -> bytes:
    output = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = os.read(master_fd, 65536)
            if data:
                output += data
            else:
                break
        except (OSError, BlockingIOError):
            time.sleep(0.05)
    return output


class TestTUIDaemonStart:
    def setup_method(self):
        _ensure_no_daemon()

    def teardown_method(self):
        _kill_daemon()

    def test_tui_starts_daemon_and_it_serves_healthz(self):
        import pty

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            if "out of pty devices" in str(exc).lower():
                pytest.skip("No PTY devices available")
            raise

        proc = None
        try:
            proc = subprocess.Popen(
                GLUDD_CMD,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env={**os.environ, "TERM": "xterm-256color"},
            )
            os.close(slave_fd)
            slave_fd = -1

            _collect_pty_output(master_fd, timeout=2.0)

            os.write(master_fd, b"S")

            healthz_ok = False
            pid_data = None
            for _ in range(30):
                time.sleep(0.5)
                pid_data = _read_pid_file()
                if pid_data:
                    try:
                        os.kill(pid_data["pid"], 0)
                        resp = httpx.get(f"{_DAEMON_URL}/healthz", timeout=2.0)
                        if resp.status_code == 200:
                            healthz_ok = True
                            break
                    except (OSError, ProcessLookupError, Exception):
                        pass

            output = _collect_pty_output(master_fd, timeout=1.0)
            text = output.decode("utf-8", errors="ignore")

            os.write(master_fd, b"q")
            time.sleep(1.0)

            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            assert pid_data is not None, (
                f"PID file not created after pressing 's'. "
                f"gunicorn may have failed to start. TUI output:\n{text}"
            )

            pid_alive = False
            try:
                os.kill(pid_data["pid"], 0)
                pid_alive = True
            except (OSError, ProcessLookupError):
                pass

            assert pid_alive, (
                f"PID {pid_data['pid']} from file is not alive. "
                f"gunicorn started but exited immediately. TUI output:\n{text}"
            )

            assert healthz_ok, (
                f"PID {pid_data['pid']} is alive but healthz failed at {_DAEMON_URL}. "
                f"gunicorn process exists but isn't serving HTTP. TUI output:\n{text}"
            )

        except Exception:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            raise
        finally:
            for fd in [master_fd]:
                if fd >= 0:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            if slave_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(slave_fd)

    def test_tui_shows_running_after_daemon_start(self):
        import pty

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            if "out of pty devices" in str(exc).lower():
                pytest.skip("No PTY devices available")
            raise

        proc = None
        try:
            proc = subprocess.Popen(
                GLUDD_CMD,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env={**os.environ, "TERM": "xterm-256color"},
            )
            os.close(slave_fd)
            slave_fd = -1

            initial_output = _collect_pty_output(master_fd, timeout=2.0)
            initial_text = initial_output.decode("utf-8", errors="ignore")

            assert "stopped" in initial_text.lower(), (
                f"Daemon should show 'stopped' before pressing 's'. Got:\n{initial_text}"
            )

            os.write(master_fd, b"S")
            time.sleep(3.0)

            post_output = _collect_pty_output(master_fd, timeout=1.0)
            post_text = post_output.decode("utf-8", errors="ignore")

            os.write(master_fd, b"q")
            time.sleep(1.0)

            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            combined = initial_text + post_text

            assert "running" in combined.lower(), (
                f"TUI should show 'running' after pressing 's'. Got:\n{post_text}"
            )

        except Exception:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            raise
        finally:
            for fd in [master_fd]:
                if fd >= 0:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            if slave_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(slave_fd)

    def test_tui_daemon_not_running_shows_stopped(self):
        import pty

        _ensure_no_daemon()

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            if "out of pty devices" in str(exc).lower():
                pytest.skip("No PTY devices available")
            raise

        proc = None
        try:
            proc = subprocess.Popen(
                GLUDD_CMD,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env={**os.environ, "TERM": "xterm-256color"},
            )
            os.close(slave_fd)
            slave_fd = -1

            output = _collect_pty_output(master_fd, timeout=2.0)
            text = output.decode("utf-8", errors="ignore")

            os.write(master_fd, b"q")
            time.sleep(1.0)

            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            assert "stopped" in text.lower(), (
                f"TUI should show 'stopped' when no daemon is running. Got:\n{text}"
            )
            daemon_section = text.split("Controls")[1].lower()
            assert "stopped" in daemon_section, (
                f"Daemon table should show 'stopped', not 'running'. Got:\n{text}"
            )

        except Exception:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            raise
        finally:
            for fd in [master_fd]:
                if fd >= 0:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            if slave_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(slave_fd)
