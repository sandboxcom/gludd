---
name: background-test-runner
description: Launch long-lived tests in the background and poll their status, so no task thread is ever blocked waiting for a test.
metadata:
  category: engineering
---

# Background Test Runner

Never run a test that takes >30s in the foreground. Use the background test
runner to keep the subagent pool full while tests execute.

---

## Architecture Overview

```
                    ┌─────────────────────┐
                    │   Orchestrator       │
                    │   (main thread)      │
                    └──────┬──────┬────────┘
                           │      │
              ┌────────────┘      └────────────┐
              ▼                                ▼
   ┌──────────────────────┐       ┌──────────────────────┐
   │  test-bg             │       │  Subagent dispatches  │
   │  (nohup make test)   │       │  (real work while     │
   │                      │       │   test runs in bg)    │
   │  ┌────────────────┐  │       └──────────────────────┘
   │  │ PID file        │  │
   │  │ (<test>.pid)    │  │
   │  └────────────────┘  │
   │  ┌────────────────┐  │
   │  │ Log file        │  │
   │  │ (.gate-logs/)   │  │
   │  └────────────────┘  │
   └──────────┬───────────┘
              │
              │  poll from subagent every ~30s
              ▼
   ┌──────────────────────┐
   │  test-bg-runner       │
   │  ACTION=status        │
   │                      │
   │  Returns:            │
   │  - PID + alive/dead  │
   │  - Terminal marker   │
   │  - Last 15 log lines │
   └──────────────────────┘
```

### Process lifecycle

```
Launch (nohup)
  │
  ├─> PID file written to .gate-logs/.test-<sanitized>.pid
  ├─> Log file opened at .gate-logs/test-<sanitized>-<ts>.log
  │
  ▼
Running (poll-able)
  │
  ├─> Heartbeat: every phase marker written to log
  ├─> status() returns: RUNNING, PID, current phase
  │
  ▼
Finished
  │
  ├─> Terminal marker written: PASS or FAIL
  ├─> PID file removed
  ├─> status() returns: FINISHED, terminal marker, last log lines
  │
  ▼
Result ingestion
  │
  ├─> results() parses pytest output for pass/fail/skip counts
  └─> Caller dispatches next action based on PASS/FAIL
```

---

## Make Target Reference

### `test-bg` — Launch a test in the background

```makefile
# Makefile excerpt
test-bg:
	@test -n "$(TESTFILE)" || (echo "ERROR: TESTFILE is required" && exit 1)
	@SANITIZED=$$(echo "$(TESTFILE)" | tr '/' '_' | tr '.' '_'); \
	TIMESTAMP=$$(date +%Y%m%d_%H%M%S); \
	mkdir -p .gate-logs; \
	nohup .venv/bin/python -m pytest $(TESTFILE) -v \
		> .gate-logs/test-$${SANITIZED}-$${TIMESTAMP}.log 2>&1 & \
	echo $$! > .gate-logs/.test-$${SANITIZED}.pid; \
	echo "Launched $(TESTFILE) [PID: $$!]"; \
	echo "Log: .gate-logs/test-$${SANITIZED}-$${TIMESTAMP}.log"
```

**What it does:**
1. Validates `TESTFILE` is set
2. Sanitizes the test file path into a filename-safe string
3. Creates a timestamped log file
4. Launches pytest via `nohup` (survives terminal close)
5. Writes the PID to `.gate-logs/.test-<sanitized>.pid`
6. Prints launch confirmation

### `test-bg-runner ACTION=status` — Poll a single test

```makefile
# Makefile excerpt
test-bg-runner:
	@python3 scripts/background_test_runner_cli.py \
		--action $(ACTION) \
		$(if $(TESTFILE),--testfile $(TESTFILE),)
```

**What it does:**
- `ACTION=status TESTFILE=...` — prints status for one test
- `ACTION=poll-all` — prints status for ALL tracked tests
- `ACTION=kill TESTFILE=...` — kills one test (SIGTERM → SIGKILL after 5s)
- `ACTION=results TESTFILE=...` — prints parsed pytest results

### `test-bg-runner ACTION=poll-all` — List all background tests

**Output format:**
```
BACKGROUND TESTS (2 tracked):
  tests/unit/test_foo.py        FINISHED (PASS)   PID: 12345  Started: 14:30
  tests/unit/test_bar.py        RUNNING (collect)  PID: 12346  Started: 14:35
```

### `test-bg-runner ACTION=kill` — Kill a running test

```makefile
# Kill logic (inside background_test_runner_cli.py)
def kill_test(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    for _ in range(5):  # wait up to 5 seconds
        time.sleep(1)
        try:
            os.kill(pid, 0)  # check if alive
        except OSError:
            return  # process died from SIGTERM
    os.kill(pid, signal.SIGKILL)  # force kill
    cleanup_pid_file(testfile)
```

---

## Full Python API Reference

### `BackgroundTestRunner` class

```python
# src/general_ludd/runner/background_test_runner.py

import os
import re
import signal
import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone


@dataclass
class TestStatus:
    """Status of a single background test."""
    testfile: str
    pid: int
    status: str                    # "RUNNING" | "FINISHED"
    started_at: str                # ISO timestamp
    elapsed_seconds: int
    terminal_marker: Optional[str] # "PASS" | "FAIL" | None (if still running)
    current_phase: Optional[str]   # e.g. "collect", "test", "coverage"
    last_log_lines: list[str]
    log_file: str
    pid_file: str

    def is_running(self) -> bool:
        return self.status == "RUNNING"

    def is_finished(self) -> bool:
        return self.status == "FINISHED"

    def passed(self) -> bool:
        return self.terminal_marker == "PASS"

    def failed(self) -> bool:
        return self.terminal_marker == "FAIL"


@dataclass
class TestResults:
    """Parsed pytest output for a completed test."""
    testfile: str
    passed: int
    failed: int
    skipped: int
    errors: int
    duration_seconds: float
    raw_output: str

    def all_passed(self) -> bool:
        return self.failed == 0 and self.errors == 0

    def summary(self) -> str:
        return (
            f"{self.testfile}: {self.passed} passed, "
            f"{self.failed} failed, {self.skipped} skipped, "
            f"{self.errors} errors in {self.duration_seconds:.1f}s"
        )


class BackgroundTestRunner:
    """Launch tests in the background and poll their status."""

    LOG_DIR = ".gate-logs"
    PID_DIR = ".gate-logs"

    def __init__(self, log_dir: Optional[str] = None):
        self.log_dir = Path(log_dir or self.LOG_DIR)
        self.pid_dir = Path(log_dir or self.PID_DIR)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ── Launch ────────────────────────────────────────────

    def launch(
        self,
        testfile: str,
        wait: bool = False,
        timeout_min: int = 30,
    ) -> TestStatus | TestResults:
        """Launch a test file in the background via nohup.

        Args:
            testfile: Path to the test file (e.g., 'tests/unit/test_foo.py').
            wait: If True, block until the test finishes or timeout expires.
            timeout_min: Maximum wait time in minutes (only if wait=True).

        Returns:
            TestStatus if wait=False (launched and returned immediately).
            TestResults if wait=True (blocked until completion or timeout).

        Raises:
            TestAlreadyRunningError: If this test file is already running.
            TestTimeoutError: If wait=True and test does not finish within timeout.
        """
        sanitized = self._sanitize(testfile)
        pid_file = self.pid_dir / f".test-{sanitized}.pid"

        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            if self._is_process_alive(pid):
                raise TestAlreadyRunningError(
                    f"Test {testfile} is already running [PID: {pid}]"
                )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"test-{sanitized}-{timestamp}.log"

        pid = os.fork()
        if pid == 0:
            # Child: redirect stdout/stderr to log file, exec pytest
            os.setsid()
            with open(log_file, "w") as log:
                os.dup2(log.fileno(), 1)
                os.dup2(log.fileno(), 2)
            os.execlp(
                "python", "python",
                "-m", "pytest", testfile, "-v",
            )
        else:
            # Parent: record PID and return
            pid_file.write_text(str(pid))
            print(f"Launched {testfile} [PID: {pid}]")
            print(f"Log: {log_file}")

            if wait:
                return self._wait(testfile, timeout_min)
            return self.status(testfile)

    # ── Status ────────────────────────────────────────────

    def status(self, testfile: str) -> TestStatus:
        """Get the current status of a background test.

        Non-blocking — returns immediately with current state.
        """
        sanitized = self._sanitize(testfile)
        pid_file = self.pid_dir / f".test-{sanitized}.pid"

        if not pid_file.exists():
            raise TestNotTrackedError(f"No PID file for {testfile}")

        pid = int(pid_file.read_text().strip())
        is_alive = self._is_process_alive(pid)

        # Find the log file
        log_files = sorted(
            self.log_dir.glob(f"test-{sanitized}-*.log"),
            reverse=True,
        )
        log_file = str(log_files[0]) if log_files else "(no log found)"

        # Read last lines
        last_log_lines = self._tail_log(log_file, lines=15) if log_files else []

        # Detect terminal marker
        full_log = "\n".join(last_log_lines) if log_files else ""
        terminal_marker = None
        if "=== GATE: PASSED ===" in full_log or re.search(
            r"=+ \d+ passed", full_log
        ):
            terminal_marker = "PASS"
        elif "=== GATE: FAILED ===" in full_log or re.search(r"=+ \d+ failed", full_log):
            terminal_marker = "FAIL"

        # Detect current phase
        current_phase = None
        phase_match = re.search(r"=== GATE PHASE: (\w+) ===", full_log)
        if phase_match:
            current_phase = phase_match.group(1)
        elif not is_alive and terminal_marker is None:
            current_phase = "(unknown — process ended without marker)"

        # Calculate elapsed
        pid_stat = Path(f"/proc/{pid}/stat")  # Linux
        if not pid_stat.exists():
            pid_stat = Path(f"/proc/{pid}/status")  # fallback
        started_at = ""
        elapsed = 0
        try:
            if pid_file.exists():
                mtime = pid_file.stat().st_mtime
                started_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                elapsed = int(time.time() - mtime)
            else:
                elapsed = 0
        except OSError:
            pass

        return TestStatus(
            testfile=testfile,
            pid=pid,
            status="RUNNING" if is_alive else "FINISHED",
            started_at=started_at,
            elapsed_seconds=elapsed,
            terminal_marker=terminal_marker,
            current_phase=current_phase,
            last_log_lines=last_log_lines,
            log_file=log_file,
            pid_file=str(pid_file),
        )

    # ── Poll all ──────────────────────────────────────────

    def poll_all(self) -> list[TestStatus]:
        """Get status of all tracked background tests."""
        statuses: list[TestStatus] = []
        for pid_file in sorted(self.pid_dir.glob(".test-*.pid")):
            testfile = self._unsanitize(pid_file.stem)
            try:
                statuses.append(self.status(testfile))
            except (TestNotTrackedError, FileNotFoundError):
                continue
        return statuses

    # ── Results ──────────────────────────────────────────

    def results(self, testfile: str) -> TestResults:
        """Parse the pytest output for a completed test.

        Raises:
            TestNotFinishedError: If the test is still running.
        """
        st = self.status(testfile)
        if st.is_running():
            raise TestNotFinishedError(
                f"Test {testfile} is still running [PID: {st.pid}]"
            )

        raw = Path(st.log_file).read_text()

        # Parse pytest summary line: "847 passed, 2 skipped in 45.67s"
        summary_match = re.search(
            r"(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped,?\s*(\d+)\s+errors?"
            r"\s+in\s+([\d.]+)s",
            raw,
        )
        if not summary_match:
            # Try simpler patterns
            passed = len(re.findall(r"\bPASSED\b", raw))
            failed = len(re.findall(r"\bFAILED\b", raw))
            skipped = len(re.findall(r"\bSKIPPED\b", raw))
            errors = len(re.findall(r"\bERROR\b", raw))
            duration = 0.0
        else:
            passed = int(summary_match.group(1))
            failed = int(summary_match.group(2) or 0)
            skipped = int(summary_match.group(3) or 0)
            errors = int(summary_match.group(4) or 0)
            duration = float(summary_match.group(5))

        return TestResults(
            testfile=testfile,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            duration_seconds=duration,
            raw_output=raw,
        )

    # ── Kill ──────────────────────────────────────────────

    def kill(self, testfile: str) -> bool:
        """Kill a running test. Returns True if killed, False if already dead."""
        st = self.status(testfile)
        if not st.is_running():
            return False

        os.kill(st.pid, signal.SIGTERM)
        for _ in range(5):
            time.sleep(1)
            if not self._is_process_alive(st.pid):
                self.cleanup(testfile)
                return True

        os.kill(st.pid, signal.SIGKILL)
        self.cleanup(testfile)
        return True

    # ── Wait (blocking, for scripts) ──────────────────────

    def _wait(self, testfile: str, timeout_min: int = 30) -> TestResults:
        """Block until the test finishes or timeout expires.

        Prints a heartbeat every 30 seconds.
        """
        deadline = time.time() + (timeout_min * 60)
        last_heartbeat = 0

        while time.time() < deadline:
            st = self.status(testfile)
            now = time.time()

            # Heartbeat every 30 seconds
            if now - last_heartbeat >= 30:
                print(
                    f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                    f"{testfile}: {st.status}"
                    + (f" (phase: {st.current_phase})" if st.current_phase else "")
                    + f" [{st.elapsed_seconds}s elapsed]"
                )
                last_heartbeat = now

            if st.is_finished():
                return self.results(testfile)

            time.sleep(5)

        raise TestTimeoutError(
            f"Test {testfile} did not finish within {timeout_min} minutes"
        )

    # ── Cleanup ───────────────────────────────────────────

    def cleanup(self, testfile: str) -> None:
        """Remove PID file for a test (log files are preserved)."""
        sanitized = self._sanitize(testfile)
        pid_file = self.pid_dir / f".test-{sanitized}.pid"
        if pid_file.exists():
            pid_file.unlink()

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _sanitize(testfile: str) -> str:
        return testfile.replace("/", "_").replace(".", "_")

    @staticmethod
    def _unsanitize(stem: str) -> str:
        # Remove the leading ".test-" prefix
        name = stem.removeprefix(".test-")
        # We can't perfectly reverse sanitize, so return the sanitized name
        # The caller uses this for display purposes
        return name.replace("_", "/").replace("//", "/")

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _tail_log(log_file: str, lines: int = 15) -> list[str]:
        try:
            content = Path(log_file).read_text().splitlines()
            return content[-lines:]
        except FileNotFoundError:
            return ["(log file not found)"]
```

### Custom exceptions

```python
class TestAlreadyRunningError(Exception):
    """Raised when trying to launch a test that is already running."""


class TestNotTrackedError(Exception):
    """Raised when no PID file exists for the requested test."""


class TestNotFinishedError(Exception):
    """Raised when trying to get results for a still-running test."""


class TestTimeoutError(Exception):
    """Raised when a test does not complete within the wait timeout."""
```

---

## pytest Integration Patterns

### Pattern A: Launch from conftest.py as a session-scoped fixture

```python
# tests/conftest.py
import pytest
from general_ludd.runner.background_test_runner import BackgroundTestRunner


@pytest.fixture(scope="session")
def bg_runner():
    """Background test runner available for the entire test session."""
    runner = BackgroundTestRunner()
    yield runner
    # Cleanup: kill any tests left running
    for status in runner.poll_all():
        if status.is_running():
            print(f"Cleaning up: killing {status.testfile} [PID: {status.pid}]")
            runner.kill(status.testfile)


@pytest.fixture
def long_running_setup(bg_runner: BackgroundTestRunner):
    """Fixture that launches a slow setup test in background."""
    bg_runner.launch("tests/unit/test_slow_setup.py")
    yield
    # Teardown: wait for it to finish and assert it passed
    results = bg_runner._wait("tests/unit/test_slow_setup.py", timeout_min=5)
    assert results.all_passed(), f"Setup test failed: {results.summary()}"
```

### Pattern B: Launch programmatically in a test

```python
# tests/integration/test_background_runner_integration.py
import time
from general_ludd.runner.background_test_runner import (
    BackgroundTestRunner,
    TestAlreadyRunningError,
)


class TestBackgroundTestRunnerIntegration:

    def test_launch_and_poll_until_completion(self):
        runner = BackgroundTestRunner()

        # Launch a fast test in the background
        status = runner.launch("tests/unit/test_trivial_pass.py")
        assert status.is_running()
        assert status.pid > 0

        # Poll until finished
        deadline = time.time() + 30
        while time.time() < deadline:
            status = runner.status("tests/unit/test_trivial_pass.py")
            if status.is_finished():
                break
            time.sleep(0.5)

        assert status.is_finished()
        assert status.passed()

        # Get structured results
        results = runner.results("tests/unit/test_trivial_pass.py")
        assert results.passed > 0
        assert results.failed == 0

    def test_launch_twice_raises_error(self):
        runner = BackgroundTestRunner()
        runner.launch("tests/unit/test_trivial_pass.py")

        with pytest.raises(TestAlreadyRunningError):
            runner.launch("tests/unit/test_trivial_pass.py")

        runner.kill("tests/unit/test_trivial_pass.py")

    def test_poll_all_lists_all_tests(self):
        runner = BackgroundTestRunner()
        runner.launch("tests/unit/test_trivial_pass.py")

        all_statuses = runner.poll_all()
        assert len(all_statuses) >= 1
        assert any(
            s.testfile == "tests/unit/test_trivial_pass.py"
            for s in all_statuses
        )

        runner.kill("tests/unit/test_trivial_pass.py")
```

### Pattern C: Launch from an orchestration script

```python
# scripts/run_test_batch.py
"""Orchestrate multiple test files in background and collect results."""

from general_ludd.runner.background_test_runner import (
    BackgroundTestRunner,
    TestTimeoutError,
)

TEST_FILES = [
    "tests/unit/test_dispatcher.py",
    "tests/unit/test_review_loop.py",
    "tests/unit/test_worktree.py",
    "tests/unit/test_daemon.py",
]

def main():
    runner = BackgroundTestRunner()

    # Launch all tests in parallel
    for tf in TEST_FILES:
        try:
            runner.launch(tf)
        except Exception as e:
            print(f"Failed to launch {tf}: {e}")

    # Wait for all to complete
    results = {}
    for tf in TEST_FILES:
        try:
            results[tf] = runner._wait(tf, timeout_min=10)
            print(results[tf].summary())
        except TestTimeoutError:
            print(f"TIMEOUT: {tf}")
            runner.kill(tf)
            results[tf] = None

    # Summary
    passed = sum(1 for r in results.values() if r and r.all_passed())
    failed = sum(1 for r in results.values() if r and not r.all_passed())
    timed_out = sum(1 for r in results.values() if r is None)

    print(f"\nBATCH RESULT: {passed} passed, {failed} failed, {timed_out} timed out")
    return 0 if failed == 0 and timed_out == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Polling Loop Deep-Dive

### Full polling code with heartbeat, terminal detection, and stale PID handling

```python
def poll_with_heartbeat(
    runner: BackgroundTestRunner,
    testfile: str,
    interval_seconds: int = 30,
    timeout_minutes: int = 40,
) -> TestResults:
    """Poll a background test until completion with periodic heartbeat.

    This is the canonical polling function. Call from a subagent or script.
    Never run this on the main thread — dispatch it via a Task subagent.
    """
    deadline = time.time() + (timeout_minutes * 60)
    last_heartbeat = 0
    stale_pid_warnings = 0

    while time.time() < deadline:
        now = time.time()

        try:
            st = runner.status(testfile)
        except TestNotTrackedError:
            # PID file disappeared — test may have been killed externally
            print(f"WARNING: PID file for {testfile} disappeared. Assuming finished.")
            return TestResults(
                testfile=testfile,
                passed=0, failed=0, skipped=0, errors=1,
                duration_seconds=0,
                raw_output="PID file missing — test may have been killed.",
            )

        # Terminal marker detection — exit immediately
        if st.is_finished():
            if st.terminal_marker:
                print(f"[{_timestamp()}] {testfile}: FINISHED ({st.terminal_marker})")
            else:
                print(f"[{_timestamp()}] {testfile}: FINISHED (no marker — process ended)")
            return runner.results(testfile)

        # Stale PID detection — process ended but no terminal marker
        if not st.is_running() and st.terminal_marker is None:
            stale_pid_warnings += 1
            if stale_pid_warnings >= 3:
                print(
                    f"[{_timestamp()}] WARNING: {testfile} PID {st.pid} not alive "
                    f"and no terminal marker after {stale_pid_warnings} checks. "
                    f"Process may have crashed. Returning partial results."
                )
                return runner.results(testfile)

        # Heartbeat
        if now - last_heartbeat >= interval_seconds:
            phase_str = f" (phase: {st.current_phase})" if st.current_phase else ""
            print(
                f"[{_timestamp()}] {testfile}: {st.status}{phase_str} "
                f"[{st.elapsed_seconds}s elapsed]"
            )
            last_heartbeat = now

        time.sleep(min(interval_seconds, 5))  # check every 5s, heartbeat every 30s

    # Timeout
    print(f"[{_timestamp()}] TIMEOUT: {testfile} after {timeout_minutes}min — killing")
    runner.kill(testfile)
    raise TestTimeoutError(f"{testfile} timed out after {timeout_minutes} minutes")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")
```

### Race condition handling

```python
def safe_status(runner: BackgroundTestRunner, testfile: str) -> TestStatus | None:
    """Get status with race-condition handling.

    Between the time we check if a PID file exists and the time we read it,
    the test may finish and the PID file may be removed by the test process
    itself or by a concurrent cleanup. This wrapper handles the race.
    """
    try:
        return runner.status(testfile)
    except TestNotTrackedError:
        # PID file was there a moment ago but isn't now — test finished between checks
        return None
    except FileNotFoundError:
        # Log file was there but isn't now — concurrent cleanup
        return None
```

---

## Error Handling

### Every failure mode and how it's handled

```python
class BackgroundTestRunner:

    # ── Error: test file not found ──
    def launch(self, testfile: str, **kwargs):
        path = Path(testfile)
        if not path.exists():
            raise FileNotFoundError(
                f"Test file not found: {testfile}\n"
                f"  Check the path and try again.\n"
                f"  Current directory: {os.getcwd()}"
            )
        # ... rest of launch

    # ── Error: pytest not installed ──
    def _verify_pytest(self):
        """Run at launch — fast check that pytest is available."""
        result = subprocess.run(
            ["python", "-m", "pytest", "--version"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "pytest is not available in the current environment.\n"
                "  Run `make sync` or `pip install pytest` first."
            )

    # ── Error: test process killed externally ──
    # Handled in status() — if process is not alive and no terminal marker,
    # the polling loop will detect this after 3 checks and return partial results.

    # ── Error: PID file corrupted ──
    def status(self, testfile: str) -> TestStatus:
        pid_file = self.pid_dir / f".test-{sanitized}.pid"
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, FileNotFoundError):
            raise TestNotTrackedError(
                f"PID file for {testfile} is corrupted or missing: {pid_file}"
            )

    # ── Error: log file truncated mid-write ──
    @staticmethod
    def _tail_log(log_file: str, lines: int = 15) -> list[str]:
        try:
            with open(log_file, "r") as f:
                content = f.readlines()
            return [line.rstrip("\n") for line in content[-lines:]]
        except (FileNotFoundError, OSError) as e:
            return [f"(log read error: {e})"]

    # ── Error: concurrent launches of same test file ──
    def launch(self, testfile: str, **kwargs):
        sanitized = self._sanitize(testfile)
        pid_file = self.pid_dir / f".test-{sanitized}.pid"

        if pid_file.exists():
            existing_pid = int(pid_file.read_text().strip())
            if self._is_process_alive(existing_pid):
                raise TestAlreadyRunningError(
                    f"Test {testfile} is already running [PID: {existing_pid}].\n"
                    f"  Wait for it to finish, or kill it with:\n"
                    f"  make test-bg-runner ACTION=kill TESTFILE='{testfile}'"
                )
            else:
                # PID file exists but process is dead — clean up stale file
                pid_file.unlink()
        # ... rest of launch

    # ── Error: disk full during log write ──
    # Handled at the OS level — nohup writes fail when disk is full.
    # The runner detects this when the test finishes without a terminal marker
    # and the log file is empty or truncated.
```

---

## Subagent Integration Pattern

### How to use from a dispatched subagent

**Subagent prompt template:**
```
You are a test-polling subagent. Your ONLY job: launch `make test-bg`
for TESTFILE='tests/unit/test_foo.py', then poll every 30s with
`make test-bg-runner ACTION=status TESTFILE='tests/unit/test_foo.py'`
until you see PASS or FAIL.

Return to the orchestrator in this EXACT format:
  TESTFILE: tests/unit/test_foo.py
  RESULT: PASS|FAIL|TIMEOUT
  SUMMARY: N passed, N failed, N skipped in X.Xs
  EVIDENCE: <paste the last 10 log lines>
```

**Subagent implementation:**
```python
# This is what the subagent runs internally:
from general_ludd.runner.background_test_runner import (
    BackgroundTestRunner,
    TestTimeoutError,
)

runner = BackgroundTestRunner()

# Launch
status = runner.launch("tests/unit/test_foo.py")
print(f"Launched [PID: {status.pid}]")

# Poll
try:
    results = poll_with_heartbeat(
        runner, "tests/unit/test_foo.py",
        interval_seconds=30, timeout_minutes=10,
    )
    print(f"TESTFILE: tests/unit/test_foo.py")
    print(f"RESULT: {'PASS' if results.all_passed() else 'FAIL'}")
    print(f"SUMMARY: {results.summary()}")
    print(f"EVIDENCE:")
    for line in results.raw_output.splitlines()[-10:]:
        print(f"  {line}")

except TestTimeoutError:
    print(f"TESTFILE: tests/unit/test_foo.py")
    print(f"RESULT: TIMEOUT")
    print(f"SUMMARY: Timed out after 10 minutes")
```

**Orchestrator ingestion:**
```
When the subagent returns, the orchestrator parses:
  TESTFILE → which file was tested
  RESULT → dispatch next action (fix failures, commit if green, etc.)
  SUMMARY → record in COMPLETION_REPORT.md
  EVIDENCE → paste as verification
```

---

## Integration with `make gate-background`

### Combined workflow

```
1. Launch gate in background:
   $ make gate-background
   → Gate launched [PID: 12345], log: .gate-logs/gate-20260725_143000.log

2. Launch a specific test in background (while gate runs):
   $ make test-bg TESTFILE='tests/unit/test_worktree_parse.py'
   → Launched tests/unit/test_worktree_parse.py [PID: 12346]

3. Dispatch real work (10 subagents) while BOTH run in background.

4. Poll gate from a subagent every 60s:
   $ make gate-status-check
   → GATE STATUS: RUNNING (phase: test-unit), 12345, elapsed: 320s

5. Poll test from a subagent every 30s:
   $ make test-bg-runner ACTION=status TESTFILE='tests/unit/test_worktree_parse.py'
   → FINISHED (PASS), 12346, elapsed: 85s

6. When gate finishes:
   $ make gate-status-check
   → GATE STATUS: PASSED

7. Ingest test result + gate result → commit if both green.
```

---

## Anti-Patterns

### AP-1: Running test-bg then immediately checking status

```bash
# WRONG — race condition: test may not have started yet
make test-bg TESTFILE='tests/unit/test_foo.py'
make test-bg-runner ACTION=status TESTFILE='tests/unit/test_foo.py'
# → "No PID file for tests/unit/test_foo.py" — test hasn't written PID yet
```

```bash
# RIGHT — give the test a moment to start
make test-bg TESTFILE='tests/unit/test_foo.py'
sleep 2  # allow PID file to be written
make test-bg-runner ACTION=status TESTFILE='tests/unit/test_foo.py'
```

### AP-2: Polling from main thread in a loop

```bash
# WRONG — blocks ALL subagent dispatch for the duration
while true; do
  make test-bg-runner ACTION=status TESTFILE='tests/unit/test_foo.py'
  sleep 30
done
```

```bash
# RIGHT — dispatch polling to a subagent
# (dispatch via Task tool: "poll test-bg status every 30s until FINISHED")
```

### AP-3: Declaring done before checking terminal marker

```python
# WRONG — assumes process exit = success
st = runner.status("tests/unit/test_foo.py")
if not st.is_running():
    print("Test done!")  # could have crashed with no output
```

```python
# RIGHT — check the terminal marker
st = runner.status("tests/unit/test_foo.py")
if st.is_finished():
    if st.passed():
        print("Test PASSED")
    elif st.failed():
        print(f"Test FAILED: check {st.log_file}")
    else:
        print(f"Test ended without marker — may have crashed: {st.log_file}")
```

### AP-4: Leaving stale PID files

```bash
# WRONG — PID files accumulate, poll-all shows ghost tests
make test-bg TESTFILE='tests/unit/test_foo.py'
# test finishes, PID file remains
# ... days later ...
make test-bg-runner ACTION=poll-all
# → "tests/unit/test_foo.py FINISHED (no marker) PID: 12345"
# → Process 12345 died days ago — PID file is stale
```

```bash
# RIGHT — cleanup after each test
make test-bg-runner ACTION=results TESTFILE='tests/unit/test_foo.py'
# runner.results() should also clean up PID file if process is dead
```

### AP-5: Launching same test file twice

```bash
# WRONG — second launch silently overwrites PID file, first test becomes untracked
make test-bg TESTFILE='tests/unit/test_foo.py'  # PID: 12345
make test-bg TESTFILE='tests/unit/test_foo.py'  # PID: 12346 (overwrites PID file)
# First test (12345) is now an orphan — still running, no one tracking it
```

```bash
# RIGHT — check if already running first
make test-bg-runner ACTION=poll-all  # check before launching
# → "tests/unit/test_foo.py RUNNING PID: 12345" — already running, don't re-launch
```

---

## Testing the Runner Itself

```python
# tests/unit/test_background_test_runner.py
import time
import pytest
from pathlib import Path
from general_ludd.runner.background_test_runner import (
    BackgroundTestRunner,
    TestAlreadyRunningError,
    TestNotFinishedError,
    TestTimeoutError,
    TestNotTrackedError,
)

# Create a small test file that we can launch
TRIVIAL_TEST = """
def test_always_passes():
    assert True

def test_another_passes():
    assert 1 + 1 == 2
"""

SLOW_TEST = """
import time
def test_slow():
    time.sleep(5)
    assert True
"""


@pytest.fixture
def temp_test_file(tmp_path):
    """Create a real pytest file in a temp directory."""
    test_file = tmp_path / "test_trivial_pass.py"
    test_file.write_text(TRIVIAL_TEST)
    return str(test_file)


@pytest.fixture
def slow_test_file(tmp_path):
    test_file = tmp_path / "test_slow_pass.py"
    test_file.write_text(SLOW_TEST)
    return str(test_file)


@pytest.fixture
def runner(tmp_path):
    """BackgroundTestRunner using temp directories."""
    return BackgroundTestRunner(
        log_dir=str(tmp_path / "logs"),
    )


class TestBackgroundTestRunner:

    def test_launch_returns_running_status(self, runner, temp_test_file):
        status = runner.launch(temp_test_file)
        assert status.is_running()
        assert status.pid > 0

    def test_poll_until_completion(self, runner, temp_test_file):
        runner.launch(temp_test_file)

        deadline = time.time() + 30
        finished = False
        while time.time() < deadline:
            st = runner.status(temp_test_file)
            if st.is_finished():
                finished = True
                break
            time.sleep(0.5)

        assert finished, "Test did not finish within 30s"
        st = runner.status(temp_test_file)
        assert st.passed()

    def test_results_parses_pass_count(self, runner, temp_test_file):
        runner.launch(temp_test_file)
        results = runner._wait(temp_test_file, timeout_min=1)

        assert results.passed == 2  # two test functions
        assert results.failed == 0
        assert results.all_passed()

    def test_launch_already_running_raises(self, runner, temp_test_file):
        runner.launch(temp_test_file)
        with pytest.raises(TestAlreadyRunningError):
            runner.launch(temp_test_file)
        runner.kill(temp_test_file)

    def test_kill_stops_running_test(self, runner, slow_test_file):
        runner.launch(slow_test_file)
        assert runner.status(slow_test_file).is_running()

        killed = runner.kill(slow_test_file)
        assert killed

        time.sleep(1)
        st = runner.status(slow_test_file)
        assert not st.is_running()

    def test_results_while_running_raises(self, runner, slow_test_file):
        runner.launch(slow_test_file)
        with pytest.raises(TestNotFinishedError):
            runner.results(slow_test_file)
        runner.kill(slow_test_file)

    def test_poll_all_lists_tracked_tests(self, runner, temp_test_file, slow_test_file):
        runner.launch(temp_test_file)
        runner.launch(slow_test_file)

        all_stats = runner.poll_all()
        assert len(all_stats) >= 2

        runner.kill(slow_test_file)
        # Wait for temp_test_file to finish
        runner._wait(temp_test_file, timeout_min=1)

    def test_timeout_raises(self, runner, slow_test_file):
        runner.launch(slow_test_file)
        with pytest.raises(TestTimeoutError):
            runner._wait(slow_test_file, timeout_min=0.001)  # 0.06 seconds
        runner.kill(slow_test_file)

    def test_cleanup_removes_pid_file(self, runner, temp_test_file):
        runner.launch(temp_test_file)
        runner._wait(temp_test_file, timeout_min=1)
        runner.cleanup(temp_test_file)

        with pytest.raises(TestNotTrackedError):
            runner.status(temp_test_file)

    def test_status_for_unknown_test_raises(self, runner):
        with pytest.raises(TestNotTrackedError):
            runner.status("tests/unit/nonexistent_test.py")
```

---

## Configuration

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `GLUDD_TEST_LOG_DIR` | `.gate-logs` | Directory for test log files and PID files |
| `GLUDD_TEST_HEARTBEAT_INTERVAL` | `30` | Seconds between heartbeat messages during wait |
| `GLUDD_TEST_DEFAULT_TIMEOUT_MIN` | `30` | Default timeout for `_wait()` in minutes |
| `GLUDD_TEST_KILL_GRACE_SEC` | `5` | Seconds to wait after SIGTERM before SIGKILL |

### Make target configuration

```makefile
# Override defaults in Makefile:
test-bg:
	@GLUDD_TEST_LOG_DIR=$(TEST_LOG_DIR) \
	GLUDD_TEST_HEARTBEAT_INTERVAL=$(HEARTBEAT_INTERVAL) \
	.venv/bin/python -m scripts.background_test_runner_cli \
		--action launch --testfile $(TESTFILE)
```

---

## Integration with gate-background

| Need | Command |
|---|---|
| Full project gate | `make gate-background` + `make gate-status-check` |
| Single test file | `make test-bg TESTFILE=...` + `make test-bg-runner ACTION=status TESTFILE=...` |
| List all running | `make test-bg-runner ACTION=poll-all` |
| Get structured results | `make test-bg-runner ACTION=results TESTFILE=...` |
| Kill a stuck test | `make test-bg-runner ACTION=kill TESTFILE=...` |
| Kill all background | `make test-bg-runner ACTION=poll-all` to list, then `ACTION=kill` per test |
| Launch test batch | Use Pattern C orchestration script |

---

## Rules

1. **Never run a test that takes >30s in the foreground** — it blocks ALL
   subagent dispatch. Use `make test-bg` instead.
2. **Never wait for a test result without dispatching other work** — launch the
   test in background, dispatch other subagents, then poll from a subagent.
3. **Poll from subagents, not the main thread** — use `make test-bg-runner ACTION=status`
   in a read-only research subagent, or call `runner.poll_all()` from a subagent task.
4. **Always verify the terminal marker** — a finished process does NOT mean a
   passing test. Check for PASS/FAIL before declaring done.
5. **Clean up PID files** — `runner.cleanup()` after results are ingested.
   Stale PID files cause false "already running" errors.
6. **Never launch the same test file twice** — check `poll_all()` first if unsure.
