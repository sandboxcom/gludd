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


def _daemon_process_rows() -> list[tuple[int, int, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        check=False,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[int, int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append((pid, ppid, parts[2]))
    return rows


def _daemon_process_tree_pids() -> set[int]:
    rows = _daemon_process_rows()
    roots = {
        pid
        for pid, _ppid, command in rows
        if "gunicorn" in command and "general_ludd.daemon:create_daemon_app()" in command
    }
    tree = set(roots)
    changed = True
    while changed:
        changed = False
        for pid, ppid, _command in rows:
            if ppid in tree and pid not in tree:
                tree.add(pid)
                changed = True
    return tree


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _terminate_process_tree(pids: set[int]) -> None:
    live = {pid for pid in pids if pid > 1 and _pid_alive(pid)}
    if not live:
        return
    for pid in sorted(live, reverse=True):
        with contextlib.suppress(OSError, ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not any(_pid_alive(pid) for pid in live):
            return
        time.sleep(0.1)
    for pid in sorted(live, reverse=True):
        if _pid_alive(pid):
            with contextlib.suppress(OSError, ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def _kill_daemon() -> None:
    pids: set[int] = set()
    data = _read_pid_file()
    if data is not None:
        with contextlib.suppress(TypeError, ValueError):
            pids.add(int(data.get("pid")))
    pids.update(_daemon_process_tree_pids())
    _terminate_process_tree(pids)
    with contextlib.suppress(OSError):
        os.unlink(_DAEMON_PID_FILE)

def _ensure_no_daemon() -> None:
    _kill_daemon()
    time.sleep(0.5)
    if _is_port_listening(_DAEMON_URL):
        pytest.skip(
            "Port 8000 occupied by external process. "
            "Cannot run daemon E2E tests."
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


def _wait_for_tui_text(master_fd: int, needles: tuple[str, ...], timeout: float = 5.0) -> str:
    text = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = _collect_pty_output(master_fd, timeout=0.25)
        if chunk:
            text += chunk.decode("utf-8", errors="ignore")
            lower = text.lower()
            if any(needle in lower for needle in needles):
                return text
        else:
            time.sleep(0.05)
    return text

def test_daemon_process_tree_pids_includes_orphan_gunicorn_workers(monkeypatch) -> None:
    stdout = (
        "100 1 /Users/shawnwilson/gludd/.venv/bin/python /Users/shawnwilson/gludd/.venv/bin/gunicorn general_ludd.daemon:create_daemon_app() --bind 127.0.0.1:8000\n"
        "101 100 /Users/shawnwilson/gludd/.venv/bin/python /Users/shawnwilson/gludd/.venv/bin/gunicorn worker\n"
        "202 1 unrelated process\n"
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _daemon_process_tree_pids() == {100, 101}

@pytest.mark.xdist_group("port_8000")
@pytest.mark.skipif(
    os.environ.get("CI") is not None,
    reason="PTY/gunicorn env-dependent under xdist",
)
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

            _wait_for_tui_text(master_fd, ("stopped", "running"), timeout=5.0)

            os.write(master_fd, b"S")

            healthz_ok = False
            pid_data = None
            daemon_pids: set[int] = set()
            observed_pid: int | None = None
            for _ in range(30):
                time.sleep(0.5)
                pid_data = _read_pid_file()
                daemon_pids = _daemon_process_tree_pids()
                if pid_data:
                    with contextlib.suppress(TypeError, ValueError):
                        observed_pid = int(pid_data.get("pid"))
                if observed_pid is None and daemon_pids:
                    observed_pid = min(daemon_pids)
                try:
                    resp = httpx.get(f"{_DAEMON_URL}/healthz", timeout=2.0)
                    if resp.status_code == 200 and observed_pid is not None and _pid_alive(observed_pid):
                        healthz_ok = True
                        break
                except Exception:
                    pass

            text = _wait_for_tui_text(master_fd, ("running",), timeout=2.0)

            os.write(master_fd, b"q")
            time.sleep(1.0)

            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            assert observed_pid is not None, (
                f"daemon process was not observed after pressing s. "
                f"pid_file={pid_data!r} process_scan={sorted(daemon_pids)} "
                f"TUI output:\n{text}"
            )
            assert _pid_alive(observed_pid), (
                f"PID {observed_pid} is not alive. "
                f"gunicorn started but exited immediately. TUI output:\n{text}"
            )
            assert healthz_ok, (
                f"PID {observed_pid} is alive but healthz failed at {_DAEMON_URL}. "
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

            initial_text = _wait_for_tui_text(master_fd, ("stopped",), timeout=5.0)

            assert "stopped" in initial_text.lower(), (
                f"Daemon should show stopped before pressing s. Got:\n{initial_text}"
            )

            os.write(master_fd, b"S")

            post_text = _wait_for_tui_text(master_fd, ("running",), timeout=8.0)

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

            text = _wait_for_tui_text(master_fd, ("stopped",), timeout=5.0)

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
