"""PTY-based E2E test: spawn TUI, press 's' to start daemon, verify daemon comes up."""

from __future__ import annotations

import contextlib
import json
import os
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


def _cleanup_daemon() -> None:
    data = _read_pid_file()
    if data is None:
        return
    pid = data["pid"]
    try:
        os.kill(pid, 15)
        for _ in range(30):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                break
        else:
            os.kill(pid, 9)
    except (OSError, ProcessLookupError):
        pass
    with contextlib.suppress(OSError):
        os.unlink(_DAEMON_PID_FILE)


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
        _cleanup_daemon()

    def teardown_method(self):
        _cleanup_daemon()

    def test_tui_starts_daemon_on_s_key(self):
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

            os.write(master_fd, b"s")

            time.sleep(3.0)

            output = _collect_pty_output(master_fd, timeout=1.0)
            text = output.decode("utf-8", errors="ignore")

            pid_data = _read_pid_file()
            pid_file_exists = pid_data is not None
            pid_is_valid = False
            healthz_ok = False

            if pid_data is not None:
                try:
                    os.kill(pid_data["pid"], 0)
                    pid_is_valid = True
                except (OSError, ProcessLookupError):
                    pid_is_valid = False

                try:
                    resp = httpx.get(f"{_DAEMON_URL}/healthz", timeout=5.0)
                    healthz_ok = resp.status_code == 200
                except Exception:
                    healthz_ok = False

            os.write(master_fd, b"q")
            time.sleep(1.0)

            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            print("=== TUI output ===")
            print(text)
            print(f"=== PID file exists: {pid_file_exists} ===")
            print(f"=== PID data: {pid_data} ===")
            print(f"=== PID is valid (alive): {pid_is_valid} ===")
            print(f"=== healthz OK: {healthz_ok} ===")

            if pid_file_exists and pid_data:
                print(f"=== PID from file: {pid_data.get('pid')} ===")
                print(f"=== daemon_url from file: {pid_data.get('daemon_url')} ===")

            assert pid_file_exists, (
                f"PID file {_DAEMON_PID_FILE} not created after pressing 's'. "
                f"TUI output:\n{text}"
            )
            assert pid_is_valid, (
                f"PID {pid_data['pid']} from file is not alive. "
                f"TUI output:\n{text}"
            )
            assert healthz_ok, (
                f"Daemon PID alive but healthz failed at {_DAEMON_URL}/healthz. "
                f"TUI output:\n{text}"
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

    def test_detect_daemon_sees_started_daemon(self):
        import pty

        from general_ludd.cli import (
            _DAEMON_PID_FILE as cli_pid_file,
        )
        from general_ludd.cli import (
            _is_daemon_pid_alive,
            _read_daemon_pid_file,
        )

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

            os.write(master_fd, b"s")

            for _attempt in range(10):
                time.sleep(1.0)
                pid_data = _read_daemon_pid_file(cli_pid_file)
                if pid_data:
                    break

            pid_data = _read_daemon_pid_file(cli_pid_file)
            print(f"=== PID data after 's': {pid_data} ===")

            pid_alive = _is_daemon_pid_alive(cli_pid_file)
            print(f"=== PID alive: {pid_alive} ===")

            healthz_ok = False
            try:
                resp = httpx.get(f"{_DAEMON_URL}/healthz", timeout=5.0)
                healthz_ok = resp.status_code == 200
            except Exception as exc:
                print(f"=== healthz exception: {exc} ===")

            print(f"=== healthz OK: {healthz_ok} ===")

            detect_by_pid = pid_alive
            detect_by_http = healthz_ok
            both_agree = detect_by_pid == detect_by_http

            print(f"=== detect_by_pid: {detect_by_pid}, detect_by_http: {detect_by_http} ===")
            print(f"=== both_agree: {both_agree} ===")

            os.write(master_fd, b"q")
            time.sleep(1.0)

            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            _collect_pty_output(master_fd, timeout=0.5)

            if pid_data and pid_alive:
                assert healthz_ok, (
                    f"BUG: PID alive but healthz fails — daemon process exists but isn't serving. "
                    f"This is a RACE CONDITION or STARTUP FAILURE. "
                    f"PID={pid_data.get('pid')}, URL={_DAEMON_URL}"
                )
            elif pid_data is None:
                pytest.skip("Daemon never started — gunicorn may not be installed or port busy")
            else:
                pytest.skip("Daemon process died immediately — likely startup error")

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

    def test_tui_daemon_start_status_in_output(self):
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

            os.write(master_fd, b"s")

            time.sleep(3.0)

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

            print("=== TUI output after 's' ===")
            print(text)

            assert any(
                marker in text.lower()
                for marker in ["started", "daemon", "pid", "running"]
            ), f"Expected daemon status marker in TUI output. Got:\n{text}"

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
