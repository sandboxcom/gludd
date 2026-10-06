"""
Tests for scripts/gate_async.sh — the non-blocking, pollable gate launcher.

All tests use GATE_CMD and STATUS_FILE / LOCK_FILE env overrides so no real
pytest run is triggered and no real /tmp lock or repo .gate-status is touched.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

GATE_ASYNC_SH = Path(__file__).parent.parent.parent / "scripts" / "gate_async.sh"
MAKEFILE = GATE_ASYNC_SH.parent.parent / "Makefile"


def test_default_gate_command_invokes_whole_gate_without_recursion() -> None:
    text = GATE_ASYNC_SH.read_text(encoding="utf-8")
    assert 'GATE_CMD="${GATE_CMD:-make gate gludd_watchdog_owned_gate=1}"' in text

    makefile = MAKEFILE.read_text(encoding="utf-8")
    gate_start = makefile.index("gate: _gate-run-lock-acquire")
    gate_end = makefile.index("# gate-lite:", gate_start)
    assert "gate_async.sh" not in makefile[gate_start:gate_end]


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


def _wait_for_status(path: Path, prefix: str, timeout: float = 5.0) -> str:
    """Return one complete status record once *prefix* is atomically visible."""
    deadline = time.monotonic() + timeout
    content = ""
    while time.monotonic() < deadline:
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        if content.startswith(prefix):
            return content
        time.sleep(0.05)
    raise AssertionError(f"status never started with {prefix!r}; last value: {content!r}")


def _interrupt_running_gate(
    tmp_path: Path,
    caught_signal: signal.Signals,
    *,
    signal_process_group: bool = True,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str, Path, Path]:
    """Start an isolated gate, signal its process group, and collect evidence."""
    status = tmp_path / "gate-status"
    lock = tmp_path / "gate-async.lock"
    env = os.environ.copy()
    env.update(
        {
            "GATE_CMD": "sleep 30",
            "STATUS_FILE": str(status),
            "LOCK_FILE": str(lock),
            "GLUDD_GATE_AUTHORIZED": "1",
            "GLUDD_GATE_ASYNC_FORCE_PIDFILE": "1",
        }
    )
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        ["bash", str(GATE_ASYNC_SH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        _wait_for_status(status, "RUNNING ")
        if signal_process_group:
            os.killpg(proc.pid, caught_signal)
        else:
            proc.send_signal(caught_signal)
        stdout, stderr = proc.communicate(timeout=10)
    except BaseException:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)
        raise
    return proc.returncode, stdout, stderr, status, lock


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

    def test_full_gate_pass_receipt_is_not_replaced(self, tmp_path: Path) -> None:
        """The wrapper must retain the full gate's signed-success-shaped receipt."""
        status = tmp_path / "gate-status"
        full_receipt = "=== GATE 2026-10-05T00:00:00Z ===\n=== GATE: PASSED ===\n"
        command = (
            'printf "=== GATE 2026-10-05T00:00:00Z ===\\n'
            '=== GATE: PASSED ===\\n" > "$STATUS_FILE"'
        )

        result = _run(
            gate_cmd=command,
            status_file=str(status),
            lock_file=str(tmp_path / "gate-async.lock"),
        )

        assert result.returncode == 0, result.stderr
        assert status.read_text(encoding="utf-8") == full_receipt


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
        lines = content.splitlines()
        parts = lines[0].split()
        # format: FAIL <epoch> rc=42
        assert len(parts) == 3
        epoch = int(parts[1])
        assert epoch > 1_700_000_000
        assert lines[1] == "=== GATE: FAILED ==="

    def test_fail_exit_code_propagated(self, tmp_path: Path) -> None:
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        result = _run(
            gate_cmd="exit 7",
            status_file=status,
            lock_file=lock,
        )

        assert result.returncode == 7

    def test_signal_killed_child_writes_failed_evidence(self, tmp_path: Path) -> None:
        """An abnormally terminated gate child must not leave RUNNING behind."""
        status = tmp_path / "gate-status"
        lock = tmp_path / "gate-async.lock"

        result = _run(
            gate_cmd='kill -KILL "$$"',
            status_file=str(status),
            lock_file=str(lock),
            extra_env={"GLUDD_GATE_ASYNC_FORCE_PIDFILE": "1"},
        )

        assert result.returncode == 137, result.stderr
        lines = status.read_text(encoding="utf-8").splitlines()
        parts = lines[0].split()
        assert parts[0] == "FAIL"
        assert int(parts[1]) > 1_700_000_000
        assert parts[2] == "rc=137"
        assert lines[1] == "=== GATE: FAILED ==="
        assert not lock.exists(), "abnormal completion must release its PID lock"

    def test_full_gate_failed_receipt_is_not_replaced(self, tmp_path: Path) -> None:
        """A whole-gate FAILED marker remains the authoritative terminal record."""
        status = tmp_path / "gate-status"
        full_receipt = "=== GATE 2026-10-05T00:00:00Z ===\n=== GATE: FAILED ===\n"
        command = (
            'printf "=== GATE 2026-10-05T00:00:00Z ===\\n'
            '=== GATE: FAILED ===\\n" > "$STATUS_FILE"; exit 17'
        )

        result = _run(
            gate_cmd=command,
            status_file=str(status),
            lock_file=str(tmp_path / "gate-async.lock"),
        )

        assert result.returncode == 17
        assert status.read_text(encoding="utf-8") == full_receipt


class TestGateAsyncSignalTerminalState:
    """Signals must publish terminal evidence before releasing owned resources."""

    def test_sigterm_writes_aborted_and_preserves_143(self, tmp_path: Path) -> None:
        result, stdout, stderr, status, lock = _interrupt_running_gate(
            tmp_path,
            signal.SIGTERM,
        )

        assert result == 143, f"stdout: {stdout}\nstderr: {stderr}"
        lines = status.read_text(encoding="utf-8").splitlines()
        parts = lines[0].split()
        assert parts[0] == "ABORTED"
        assert int(parts[1]) > 1_700_000_000
        assert parts[2:] == ["signal=TERM", "rc=143", "cleanup_rc=0"]
        assert lines[1] == "=== GATE: ABORTED ==="
        assert not lock.exists(), "SIGTERM must release its owned PID lock"
        assert not list(tmp_path.glob("gate-status.*.tmp"))

    def test_sigint_writes_aborted_and_preserves_130(self, tmp_path: Path) -> None:
        result, stdout, stderr, status, lock = _interrupt_running_gate(
            tmp_path,
            signal.SIGINT,
        )

        assert result == 130, f"stdout: {stdout}\nstderr: {stderr}"
        lines = status.read_text(encoding="utf-8").splitlines()
        parts = lines[0].split()
        assert parts[0] == "ABORTED"
        assert int(parts[1]) > 1_700_000_000
        assert parts[2:] == ["signal=INT", "rc=130", "cleanup_rc=0"]
        assert lines[1] == "=== GATE: ABORTED ==="
        assert not lock.exists(), "SIGINT must release its owned PID lock"
        assert not list(tmp_path.glob("gate-status.*.tmp"))

    @pytest.mark.parametrize(
        ("caught_signal", "signal_name", "signal_rc"),
        [
            (signal.SIGINT, "INT", 130),
            (signal.SIGTERM, "TERM", 143),
        ],
    )
    def test_injected_command_does_not_claim_unrelated_gate_lock(
        self,
        tmp_path: Path,
        caught_signal: signal.Signals,
        signal_name: str,
        signal_rc: int,
    ) -> None:
        """Stub cleanup must not inspect or mutate an enclosing gate's lock."""
        gate_log_dir = tmp_path / ".gate-logs"
        gate_log_dir.mkdir()
        unrelated_lock = gate_log_dir / "gate-run.lock"
        unrelated_lock.write_text("not-this-launcher's-lock\n", encoding="utf-8")

        result, stdout, stderr, status, _lock = _interrupt_running_gate(
            tmp_path,
            caught_signal,
            extra_env={"GLUDD_PROJECT_ROOT": str(tmp_path)},
        )

        assert result == signal_rc, f"stdout: {stdout}\nstderr: {stderr}"
        parts = status.read_text(encoding="utf-8").splitlines()[0].split()
        assert parts[2:] == [
            f"signal={signal_name}",
            f"rc={signal_rc}",
            "cleanup_rc=0",
        ]
        assert unrelated_lock.read_text(encoding="utf-8") == (
            "not-this-launcher's-lock\n"
        )

    def test_parent_only_signal_terminates_owned_child(self, tmp_path: Path) -> None:
        """External TERM of the wrapper must not orphan its gate child."""
        result, stdout, stderr, status, lock = _interrupt_running_gate(
            tmp_path,
            signal.SIGTERM,
            signal_process_group=False,
        )

        assert result == 143, f"stdout: {stdout}\nstderr: {stderr}"
        assert status.read_text(encoding="utf-8").startswith("ABORTED ")
        assert "[gate-kill]" in stdout
        assert not lock.exists()

    def test_signal_cleanup_never_overwrites_published_pass(
        self,
        tmp_path: Path,
    ) -> None:
        """A late TERM after PASS publication may clean up but cannot turn red."""
        status = tmp_path / "gate-status"
        lock = tmp_path / "gate-async.lock"
        successful_status = "PASS 1700000001"

        result = _run(
            gate_cmd=(
                f'printf "{successful_status}\\n" > "$STATUS_FILE"; '
                'kill -TERM "$PPID"'
            ),
            status_file=str(status),
            lock_file=str(lock),
            extra_env={"GLUDD_GATE_ASYNC_FORCE_PIDFILE": "1"},
        )

        assert result.returncode == 143, result.stderr
        assert status.read_text(encoding="utf-8").strip() == successful_status
        assert not lock.exists(), "late signal cleanup must release its owned lock"


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
