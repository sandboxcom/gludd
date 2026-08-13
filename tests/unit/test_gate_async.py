"""
Tests for scripts/gate_async.sh — the non-blocking, pollable gate launcher.

All tests use GATE_CMD and STATUS_FILE / LOCK_FILE env overrides so no real
pytest run is triggered and no real /tmp lock or repo .gate-status is touched.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

GATE_ASYNC_SH = Path(__file__).parent.parent.parent / "scripts" / "gate_async.sh"


def test_default_gate_command_invokes_run_gate_through_bash() -> None:
    text = GATE_ASYNC_SH.read_text(encoding="utf-8")
    assert 'GATE_CMD="${GATE_CMD:-bash scripts/run_gate.sh}"' in text


def _run(
    gate_cmd: str,
    *,
    status_file: str,
    lock_file: str,
    timeout: int = 15,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run gate_async.sh with the given overrides and return the completed process."""
    env = os.environ.copy()
    env["GATE_CMD"] = gate_cmd
    env["STATUS_FILE"] = status_file
    env["LOCK_FILE"] = lock_file
    env["GLUDD_GATE_AUTHORIZED"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(GATE_ASYNC_SH)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


class TestGateAsyncPassPath:
    """(a) A gate command that exits 0 should write PASS <epoch> to STATUS_FILE."""

    def test_pass_writes_pass_status(self, tmp_path: Path) -> None:
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        result = _run(
            gate_cmd="exit 0",
            status_file=status,
            lock_file=lock,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        content = Path(status).read_text().strip()
        assert content.startswith("PASS "), f"status was: {content!r}"
        # epoch should be a plausible unix timestamp
        parts = content.split()
        assert len(parts) == 2
        epoch = int(parts[1])
        assert epoch > 1_700_000_000


class TestGateAsyncFailPath:
    """(b) A gate command that exits nonzero should write FAIL <epoch> rc=<n>."""

    def test_fail_writes_fail_status(self, tmp_path: Path) -> None:
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        result = _run(
            gate_cmd="exit 42",
            status_file=status,
            lock_file=lock,
        )

        assert result.returncode == 42, f"stderr: {result.stderr}"
        content = Path(status).read_text().strip()
        assert content.startswith("FAIL "), f"status was: {content!r}"
        assert "rc=42" in content, f"status was: {content!r}"
        parts = content.split()
        # format: FAIL <epoch> rc=42
        assert len(parts) == 3
        epoch = int(parts[1])
        assert epoch > 1_700_000_000

    def test_fail_exit_code_propagated(self, tmp_path: Path) -> None:
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        result = _run(
            gate_cmd="exit 7",
            status_file=status,
            lock_file=lock,
        )

        assert result.returncode == 7


class TestGateAsyncInnerSubshellGuard:
    """
    (c) The gate cmd calling `exit 0` mid-run should still record PASS — not FAIL.

    The ship_async bug: if `exit` inside the gate cmd propagated out of the inner
    subshell and killed the status-writer before it could write PASS, the STATUS_FILE
    would be left as RUNNING or never updated.  The inner-subshell wrapping must
    prevent that.
    """

    def test_gate_cmd_bare_exit_records_pass(self, tmp_path: Path) -> None:
        """A gate cmd that calls `exit 0` directly still produces PASS."""
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        # This is the pathological case: bare `exit 0` inside the evaluated cmd.
        # Without the inner-subshell guard, `exit 0` would jump out of the script
        # entirely before the status-writer ran.
        result = _run(
            gate_cmd="exit 0",
            status_file=status,
            lock_file=lock,
        )

        # Script must complete successfully
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Status file must show PASS — not RUNNING or empty
        content = Path(status).read_text().strip()
        assert content.startswith("PASS "), (
            f"inner-subshell guard failed — status was: {content!r}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_gate_cmd_exit_nonzero_records_fail(self, tmp_path: Path) -> None:
        """A gate cmd that calls `exit 3` still gets FAIL written (not RUNNING)."""
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        result = _run(
            gate_cmd="exit 3",
            status_file=status,
            lock_file=lock,
        )

        assert result.returncode == 3
        content = Path(status).read_text().strip()
        assert content.startswith("FAIL "), f"status was: {content!r}"
        assert "rc=3" in content


class TestGateAsyncConcurrentRefused:
    """(d) A second concurrent gate-async launch is refused by the flock."""

    def test_second_launch_refused(self, tmp_path: Path) -> None:
        """
        Start a slow first gate, then immediately try a second one.
        The second must fail (nonzero exit) without overwriting the status file.
        """
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        # First gate: slow enough that the second sees the lock held
        gate_cmd_slow = "sleep 3; exit 0"

        env = os.environ.copy()
        env["GATE_CMD"] = gate_cmd_slow
        env["STATUS_FILE"] = status
        env["LOCK_FILE"] = lock
        env["GLUDD_GATE_AUTHORIZED"] = "1"

        # Launch first gate in background
        first = subprocess.Popen(
            ["bash", str(GATE_ASYNC_SH)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )

        # Give the first gate time to acquire the lock and write RUNNING
        time.sleep(0.5)

        # Verify RUNNING was written
        content_before = Path(status).read_text().strip()
        assert content_before.startswith("RUNNING "), (
            f"Expected RUNNING, got: {content_before!r}"
        )

        # Try second concurrent launch — must be refused
        second_result = _run(
            gate_cmd=gate_cmd_slow,
            status_file=status,
            lock_file=lock,
        )

        assert second_result.returncode != 0, (
            f"Second gate should have been refused but returned 0.\n"
            f"stdout: {second_result.stdout}\nstderr: {second_result.stderr}"
        )
        assert "refusing" in second_result.stderr.lower() or "already running" in second_result.stderr.lower(), (
            f"Expected refusal message, got stderr: {second_result.stderr!r}"
        )

        # Status file must still say RUNNING (not overwritten by refused second)
        content_during = Path(status).read_text().strip()
        assert content_during.startswith("RUNNING "), (
            f"Second gate overwrote status to: {content_during!r}"
        )

        # Clean up: wait for first gate to finish
        try:
            first.wait(timeout=10)
        except subprocess.TimeoutExpired:
            first.kill()
            first.wait()

        # After first gate completes the status should be PASS
        final_content = Path(status).read_text().strip()
        assert final_content.startswith("PASS "), f"Final status was: {final_content!r}"


class TestGateAsyncRunningWrittenImmediately:
    """STATUS_FILE must be written as RUNNING before the gate cmd is run."""

    def test_running_written_before_gate_runs(self, tmp_path: Path) -> None:
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        # Gate cmd that waits: if status is already RUNNING when it starts, we know
        # the write was immediate.  We check status from the outside via a poll.
        # Instead, start a slow gate in background and check status quickly.
        env = os.environ.copy()
        env["GATE_CMD"] = "sleep 2; exit 0"
        env["STATUS_FILE"] = status
        env["LOCK_FILE"] = lock
        env["GLUDD_GATE_AUTHORIZED"] = "1"

        proc = subprocess.Popen(
            ["bash", str(GATE_ASYNC_SH)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )

        # Poll until RUNNING appears (or timeout)
        deadline = time.time() + 5
        content = ""
        while time.time() < deadline:
            try:
                content = Path(status).read_text().strip()
                if content.startswith("RUNNING"):
                    break
            except FileNotFoundError:
                pass
            time.sleep(0.1)

        assert content.startswith("RUNNING "), (
            f"STATUS_FILE was not RUNNING within 5s; got: {content!r}"
        )

        # Verify epoch + pid fields
        parts = content.split()
        assert len(parts) == 3
        epoch = int(parts[1])
        pid = int(parts[2])
        assert epoch > 1_700_000_000
        assert pid > 0

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_stale_pid_lock_is_reclaimed_only_when_pid_is_not_gate(tmp_path: Path) -> None:
    """A live, unrelated PID must not make an async gate look active."""
    status = tmp_path / "gate-status"
    lock = tmp_path / "gate-async.lock"
    # Force the portable PID-file path so this exercises stale-owner handling
    # even on Linux hosts where GNU flock is installed.
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    status.write_text(f"RUNNING {int(time.time())} {os.getpid()}\n", encoding="utf-8")

    result = _run(
        gate_cmd="exit 0",
        status_file=str(status),
        lock_file=str(lock),
        extra_env={"GLUDD_GATE_ASYNC_FORCE_PIDFILE": "1"},
    )

    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert status.read_text(encoding="utf-8").startswith("PASS ")
