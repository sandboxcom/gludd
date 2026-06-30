"""Background test runner — launch, poll, and collect results from non-blocking test runs.

Usage (CLI):
    python -m general_ludd.runner.background_test_runner launch tests/unit/test_foo.py
    python -m general_ludd.runner.background_test_runner status tests/unit/test_foo.py
    python -m general_ludd.runner.background_test_runner poll-all
    python -m general_ludd.runner.background_test_runner kill tests/unit/test_foo.py
    python -m general_ludd.runner.background_test_runner results tests/unit/test_foo.py
    python -m general_ludd.runner.background_test_runner launch tests/unit/test_foo.py --wait

Usage (API):
    from general_ludd.runner.background_test_runner import BackgroundTestRunner
    runner = BackgroundTestRunner()
    runner.launch("tests/unit/test_foo.py")
    print(runner.status("tests/unit/test_foo.py"))
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_DIR = Path(".gate-logs")


class BackgroundTestRunner:
    """Launch, poll, and collect results from non-blocking background test runs."""

    def __init__(self, status_dir: str | Path = STATUS_DIR) -> None:
        self.status_dir = Path(status_dir)
        self.status_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize(testfile: str) -> str:
        return re.sub(r"[^a-zA-Z0-9._-]", "_", testfile).replace(".", "_")

    def _pid_path(self, testfile: str) -> Path:
        return self.status_dir / f".test-{self._sanitize(testfile)}.pid"

    def _status_path(self, testfile: str) -> Path:
        return self.status_dir / f".test-{self._sanitize(testfile)}.status.json"

    def _log_paths(self, testfile: str) -> list[Path]:
        sanitized = self._sanitize(testfile)
        return sorted(self.status_dir.glob(f"test-{sanitized}-*.log"), reverse=True)

    def launch(
        self,
        testfile: str,
        timeout_min: int = 30,
        wait: bool = False,
    ) -> dict[str, Any]:
        """Launch a test in the background.

        Runs `make test-specific TESTFILE=<testfile>` via subprocess.Popen.
        Writes a JSON status file with pid, testfile, start_time, phase.
        """
        sanitized = self._sanitize(testfile)
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")  # noqa: UP017
        log_file = self.status_dir / f"test-{sanitized}-{timestamp}.log"
        pid_path = self._pid_path(testfile)
        status_path = self._status_path(testfile)

        # Check if already running
        if pid_path.exists():
            existing = self._read_pid(testfile)
            if existing and self._pid_alive(existing):
                return {
                    "status": "already_running",
                    "pid": existing,
                    "testfile": testfile,
                }

        cmd = ["make", "test-specific", f"TESTFILE={testfile}"]
        with open(log_file, "w", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )

        pid_path.write_text(str(proc.pid))

        status: dict[str, Any] = {
            "pid": proc.pid,
            "testfile": testfile,
            "start_time": datetime.now(tz=timezone.utc).isoformat(),  # noqa: UP017
            "log_file": str(log_file),
            "phase": "running",
            "terminal_marker": None,
            "exit_code": None,
            "timeout_min": timeout_min,
        }
        status_path.write_text(json.dumps(status, indent=2))

        if wait:
            return self._wait(testfile, timeout_min)

        return status

    def status(self, testfile: str) -> dict[str, Any]:
        """Check the status of a background test."""
        status_path = self._status_path(testfile)
        result: dict[str, Any] = {
            "testfile": testfile,
            "phase": "unknown",
            "pid": None,
            "alive": False,
            "terminal_marker": None,
            "exit_code": None,
            "last_lines": [],
        }

        pid = self._read_pid(testfile)
        result["pid"] = pid
        result["alive"] = self._pid_alive(pid) if pid else False

        if status_path.exists():
            try:
                saved = json.loads(status_path.read_text(encoding="utf-8"))
                result.update(saved)
            except (json.JSONDecodeError, OSError):
                pass

        logs = self._log_paths(testfile)
        if logs:
            result["log_file"] = str(logs[0])
            result["terminal_marker"] = self._detect_terminal_marker(logs[0])
            result["exit_code"] = self._parse_exit_code(logs[0])
            result["last_lines"] = self._get_last_lines(logs[0], 15)

        if result["alive"]:
            result["phase"] = "running"
        elif not result["alive"] and result.get("phase") == "running":
            result["phase"] = "completed"
            if status_path.exists():
                saved = json.loads(status_path.read_text(encoding="utf-8"))
                saved["phase"] = "completed"
                saved["terminal_marker"] = result.get("terminal_marker")
                saved["exit_code"] = result.get("exit_code")
                status_path.write_text(json.dumps(saved, indent=2))

        return result

    def _get_last_lines(self, log_path: Path, n: int) -> list[str]:
        """Get last N lines from log file."""
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return lines[-n:] if len(lines) > n else lines
        except OSError:
            return []

    def poll_all(self) -> list[dict[str, Any]]:
        """Return status for every tracked background test."""
        results: list[dict[str, Any]] = []
        for pid_file in self.status_dir.glob(".test-*.pid"):
            testfile = self._resolve_testfile(pid_file)
            if testfile:
                results.append(self.status(testfile))
        return results

    def kill(self, testfile: str, force: bool = False) -> dict[str, Any]:
        """Kill a background test by PID file. Sends SIGTERM, then SIGKILL after 5s."""
        pid = self._read_pid(testfile)
        if not pid:
            return {"status": "no_pid_file", "testfile": testfile}

        if not self._pid_alive(pid):
            self._cleanup_pid(testfile)
            return {"status": "already_dead", "pid": pid, "testfile": testfile}

        os.kill(pid, signal.SIGTERM)
        for _ in range(5):
            time.sleep(1)
            if not self._pid_alive(pid):
                self._cleanup_pid(testfile)
                return {"status": "terminated", "pid": pid, "testfile": testfile}

        if force:
            os.kill(pid, signal.SIGKILL)
            self._cleanup_pid(testfile)
            return {"status": "killed", "pid": pid, "testfile": testfile}

        self._cleanup_pid(testfile)
        return {"status": "killed_after_timeout", "pid": pid, "testfile": testfile}

    def results(self, testfile: str) -> dict[str, Any]:
        """Get final results for a completed test. Returns None if still running."""
        st = self.status(testfile)
        if st.get("alive", False):
            return {**st, "phase": "running", "complete": False}
        return {
            **st,
            "phase": "completed",
            "complete": True,
            "passed": st.get("terminal_marker") == "PASS",
            "exit_code": st.get("exit_code"),
        }

    def _wait(
        self,
        testfile: str,
        timeout_min: int = 30,
        poll_interval: int = 30,
    ) -> dict[str, Any]:
        """Block until the test completes or timeout is reached. Polls every 30s."""
        deadline = time.time() + timeout_min * 60
        while time.time() < deadline:
            st = self.status(testfile)
            if not st.get("alive", False):
                log_file = st.get("log_file")
                latest_log = (
                    f"--- last 10 lines of {log_file} ---\n"
                    + "".join(
                        Path(log_file).read_text(encoding="utf-8").splitlines(keepends=True)[-10:]
                    )
                    if log_file and Path(log_file).exists()
                    else ""
                )
                return {
                    **st,
                    "phase": "completed",
                    "complete": True,
                    "log_tail": latest_log,
                }
            print(
                f"[BackgroundTestRunner] waiting for {testfile} ... "
                f"({int(time.time() - deadline + timeout_min * 60)}s elapsed)"
            )
            time.sleep(poll_interval)

        return {
            "testfile": testfile,
            "phase": "timeout",
            "complete": False,
            "message": f"Timed out after {timeout_min} minutes",
        }

    def _read_pid(self, testfile: str) -> int | None:
        pid_path = self._pid_path(testfile)
        if not pid_path.exists():
            return None
        try:
            return int(pid_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _cleanup_pid(self, testfile: str) -> None:
        pid_path = self._pid_path(testfile)
        pid_path.unlink(missing_ok=True)

    @staticmethod
    def _detect_terminal_marker(log_path: Path) -> str | None:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        # Check for pytest summary line
        if re.search(r"===\s*PASSED\s*===", text):
            return "PASS"
        if re.search(r"\d+ passed", text) and not re.search(
            r"\d+ failed|\d+ error", text
        ):
            return "PASS"
        first_fail_line = re.search(r"===\s*FAILED\s*===", text)
        if first_fail_line:
            tail = text[first_fail_line.start():]
            if re.search(r"\d+ passed", tail) and re.search(r"\d+ failed", tail):
                return "FAIL"
            if re.search(r"\d+ failed|\d+ error", tail):
                return "FAIL"
            return "FAIL"
        if re.search(r"\d+ failed\b|\d+ error\b", text):
            return "FAIL"
        if re.search(r"Error$", text, re.MULTILINE):
            return "FAIL"
        return None

    @staticmethod
    def _parse_exit_code(log_path: Path) -> int | None:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m = re.search(r"exit code (\d+)", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        m = re.search(r"PYTEST_EXIT=(\d+)", text)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _resolve_testfile(pid_file: Path) -> str | None:
        name = pid_file.name
        name = name.removeprefix(".test-").removesuffix(".pid")
        return name.replace("_", "/") if "_" in name else name


def cli() -> None:
    runner = BackgroundTestRunner()
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "launch":
        if len(sys.argv) < 3:
            print("Usage: background_test_runner launch <testfile> [--wait]")
            sys.exit(1)
        testfile = sys.argv[2]
        wait = "--wait" in sys.argv
        result = runner.launch(testfile, wait=wait)
        print(json.dumps(result, indent=2))
        if result.get("phase") == "timeout":
            sys.exit(1)

    elif command == "status":
        if len(sys.argv) < 3:
            print("Usage: background_test_runner status <testfile>")
            sys.exit(1)
        result = runner.status(sys.argv[2])
        print(json.dumps(result, indent=2))

    elif command == "poll-all":
        results = runner.poll_all()
        print(json.dumps(results, indent=2))

    elif command == "kill":
        if len(sys.argv) < 3:
            print("Usage: background_test_runner kill <testfile>")
            sys.exit(1)
        force = "--force" in sys.argv
        result = runner.kill(sys.argv[2], force=force)
        print(json.dumps(result, indent=2))

    elif command == "results":
        if len(sys.argv) < 3:
            print("Usage: background_test_runner results <testfile>")
            sys.exit(1)
        result = runner.results(sys.argv[2])
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
