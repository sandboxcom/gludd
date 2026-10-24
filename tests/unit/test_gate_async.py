"""
Tests for scripts/gate_async.sh — the non-blocking, pollable gate launcher.

All tests use GATE_CMD plus checkout, status, lock, and resource-root overrides,
so no real pytest run is triggered and no enclosing-worktree state is touched.
"""

from __future__ import annotations

import os
import select
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

GATE_ASYNC_SH = Path(__file__).parent.parent.parent / "scripts" / "gate_async.sh"
MAKEFILE = GATE_ASYNC_SH.parent.parent / "Makefile"


def _isolated_gate_env(
    gate_cmd: str,
    *,
    status_file: Path,
    lock_file: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment whose process cleanup is confined to one fixture."""
    fixture_root = status_file.parent.resolve()
    env = os.environ.copy()
    env.update(
        {
            "GATE_CMD": gate_cmd,
            "STATUS_FILE": str(status_file),
            "LOCK_FILE": str(lock_file),
            "GLUDD_GATE_AUTHORIZED": "1",
            "GLUDD_PROJECT_ROOT": str(fixture_root),
            "GLUDD_RESOURCE_ROOT": str(fixture_root / "resources"),
        }
    )
    if extra_env:
        env.update(extra_env)
    return env


def test_default_gate_command_invokes_whole_gate_without_recursion() -> None:
    text = GATE_ASYNC_SH.read_text(encoding="utf-8")
    assert 'GATE_CMD="${GATE_CMD:-make gate gludd_watchdog_owned_gate=1}"' in text

    makefile = compose_makefile(MAKEFILE)
    gate_start = makefile.index("\ngate: ") + 1
    gate_header = makefile[gate_start:].splitlines()[0]
    # Canonical release-profile admission precedes ownership acquisition.
    assert gate_header.startswith("gate: gate-admission-config ")
    assert "$(if $(filter release,$(GATE_TEST_PROFILE)),_gate-release-state-check,)" in gate_header
    assert gate_header.count("_gate-run-lock-acquire") == 1
    assert gate_header.endswith(" _gate-run-lock-acquire")
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
    env = _isolated_gate_env(
        gate_cmd,
        status_file=Path(status_file),
        lock_file=Path(lock_file),
        extra_env=extra_env,
    )
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


@contextmanager
def _held_gate(
    tmp_path: Path,
    *,
    gate_cmd: str | None = None,
) -> Iterator[tuple[subprocess.Popen[str], str]]:
    """Hold one real isolated gate until release, including on assertion faults."""
    status = tmp_path / "gate-status"
    lock = tmp_path / "gate-async.lock"
    entry = tmp_path / "gate-entry-status"
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    with (
        os.fdopen(ready_read, "rb", buffering=0) as ready,
        os.fdopen(ready_write, "wb", buffering=0) as ready_sender,
        os.fdopen(release_read, "rb", buffering=0) as release_receiver,
        os.fdopen(release_write, "wb", buffering=0) as release,
    ):
        child = (
            "import os, select; from pathlib import Path; "
            f"Path({str(entry)!r}).write_text("
            "Path(os.environ['STATUS_FILE']).read_text(encoding='utf-8'), encoding='utf-8'); "
            f"os.write({ready_write}, b'R'); os.close({ready_write}); "
            f"readable = select.select([{release_read}], [], [], 15)[0]; "
            f"token = os.read({release_read}, 1) if readable else b''; "
            f"os.close({release_read}); raise SystemExit(0 if token == b'R' else 1)"
        )
        env = _isolated_gate_env(
            gate_cmd if gate_cmd is not None else shlex.join([sys.executable, "-c", child]),
            status_file=status,
            lock_file=lock,
        )
        deadline = time.monotonic() + 5
        process = subprocess.Popen(
            ["bash", str(GATE_ASYNC_SH)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
            start_new_session=True,
            pass_fds=(ready_write, release_read),
        )
        ready_sender.close()
        release_receiver.close()
        try:
            readable, _, _ = select.select([ready], [], [], max(0.0, deadline - time.monotonic()))
            assert readable and ready.read(1) == b"R", "gate child did not acknowledge readiness"
            content = _wait_for_status(status, "RUNNING ", timeout=max(0.0, deadline - time.monotonic()))
            assert process.poll() is None, "acknowledged gate must remain live until release"
            yield process, content.strip()
        finally:
            try:
                with suppress(BrokenPipeError):
                    release.write(b"R")
            finally:
                release.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired as exc:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    raise AssertionError("held gate did not drain within 10s") from exc


def _reset_signal_mask() -> None:
    """Ensure the child process starts with INT/TERM unblocked and defaulted."""
    # pytest-xdist and other harnesses may block or ignore SIGINT in workers.
    # The wrapper script also resets these, but resetting here makes the test
    # contract explicit and prevents the wrapper from hanging on signal tests.
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress((ValueError, OSError)):
            signal.signal(sig, signal.SIG_DFL)
        with suppress((ValueError, OSError)):
            signal.pthread_sigmask(signal.SIG_UNBLOCK, [sig])


def _interrupt_running_gate(
    tmp_path: Path,
    caught_signal: signal.Signals,
    *,
    signal_process_group: bool = True,
) -> tuple[int, str, str, Path, Path]:
    """Start an isolated gate, signal its process group, and collect evidence."""
    status = tmp_path / "gate-status"
    lock = tmp_path / "gate-async.lock"
    env = _isolated_gate_env(
        "sleep 30",
        status_file=status,
        lock_file=lock,
        extra_env={"GLUDD_GATE_ASYNC_FORCE_PIDFILE": "1"},
    )
    proc = subprocess.Popen(
        ["bash", str(GATE_ASYNC_SH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
        preexec_fn=_reset_signal_mask,
    )
    try:
        _wait_for_status(status, "RUNNING ")
        if signal_process_group:
            os.killpg(proc.pid, caught_signal)
        else:
            proc.send_signal(caught_signal)
        stdout, stderr = proc.communicate(timeout=30)
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
        command = 'printf "=== GATE 2026-10-05T00:00:00Z ===\\n=== GATE: PASSED ===\\n" > "$STATUS_FILE"'

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
        command = 'printf "=== GATE 2026-10-05T00:00:00Z ===\\n=== GATE: FAILED ===\\n" > "$STATUS_FILE"; exit 17'

        result = _run(
            gate_cmd=command,
            status_file=str(status),
            lock_file=str(tmp_path / "gate-async.lock"),
        )

        assert result.returncode == 17
        assert status.read_text(encoding="utf-8") == full_receipt


class TestGateAsyncSignalTerminalState:
    """Signals must publish terminal evidence before releasing owned resources."""

    def test_signal_cleanup_uses_fixture_project_root(self, tmp_path: Path) -> None:
        """Nested signal tests must never inspect the enclosing worktree's gate."""
        result, stdout, stderr, _status, _lock = _interrupt_running_gate(
            tmp_path,
            signal.SIGTERM,
        )

        expected_lock = tmp_path / ".gate-logs" / "gate-run.lock"
        assert result == 143, f"stdout: {stdout}\nstderr: {stderr}"
        assert f"no readable owned gate lock at {expected_lock}" in stdout
        assert str(GATE_ASYNC_SH.parent.parent / ".gate-logs") not in stdout

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
        # Send SIGINT to the wrapper only: the wrapper's trap must still clean up
        # the owned child. The child sleep ignores SIGINT when launched as a
        # background job, so a process-group signal can race and hang.
        result, stdout, stderr, status, lock = _interrupt_running_gate(
            tmp_path,
            signal.SIGINT,
            signal_process_group=False,
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
            gate_cmd=(f'printf "{successful_status}\\n" > "$STATUS_FILE"; kill -TERM "$PPID"'),
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
            f"inner-subshell guard failed — status was: {content!r}\nstdout: {result.stdout}\nstderr: {result.stderr}"
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
        Hold the first gate at readiness, then immediately try a second one.
        The second must fail (nonzero exit) without overwriting the status file.
        """
        status = str(tmp_path / "gate-status")
        lock = str(tmp_path / "gate-async.lock")

        with _held_gate(tmp_path) as (first, content_before):
            # Verify RUNNING was written while the actual gate child is held.
            assert content_before.startswith("RUNNING "), f"Expected RUNNING, got: {content_before!r}"

            # Try second concurrent launch — must be refused before its command runs.
            second_result = _run(
                gate_cmd="exit 0",
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

            # Status file must still say RUNNING (not overwritten by refused second).
            content_during = Path(status).read_text().strip()
            assert content_during.startswith("RUNNING "), f"Second gate overwrote status to: {content_during!r}"
            assert first.poll() is None, "refusal must be observed while the first gate is live"
            assert content_during == content_before

        assert first.returncode == 0
        # After first gate completes the status should be PASS.
        final_content = Path(status).read_text().strip()
        assert final_content.startswith("PASS "), f"Final status was: {final_content!r}"


class TestGateAsyncRunningWrittenImmediately:
    """STATUS_FILE must be written as RUNNING before the gate cmd is run."""

    def test_running_written_before_gate_runs(self, tmp_path: Path) -> None:
        status = str(tmp_path / "gate-status")

        with _held_gate(tmp_path) as (proc, content):
            assert content.startswith("RUNNING "), f"STATUS_FILE was not RUNNING within 5s; got: {content!r}"

            # Verify epoch + pid fields.
            parts = content.split()
            assert len(parts) == 3
            epoch = int(parts[1])
            pid = int(parts[2])
            assert epoch > 1_700_000_000
            assert pid > 0
            assert pid == proc.pid
            assert (tmp_path / "gate-entry-status").read_text(encoding="utf-8").strip() == content
            assert proc.poll() is None

        assert proc.returncode == 0
        assert Path(status).read_text(encoding="utf-8").startswith("PASS ")


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


@pytest.mark.parametrize(
    "case",
    [
        TestGateAsyncConcurrentRefused().test_second_launch_refused,
        TestGateAsyncRunningWrittenImmediately().test_running_written_before_gate_runs,
    ],
    ids=["concurrent-refusal", "running-before-entry"],
)
def test_held_gate_fixture_drains_after_assertion_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: Callable[[Path], None],
) -> None:
    """An assertion after live RUNNING publication must still drain the fixture."""
    status = tmp_path / "gate-status"
    launched: list[subprocess.Popen[str]] = []
    original_popen = subprocess.Popen
    original_read = Path.read_text

    def capture(
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        env: dict[str, str],
        start_new_session: bool = False,
        pass_fds: tuple[int, ...] = (),
    ) -> subprocess.Popen[str]:
        process = original_popen(
            args,
            stdout=stdout,
            stderr=stderr,
            text=text,
            env=env,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        launched.append(process)
        return process

    def fault_after_running(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        content = original_read(path, encoding=encoding, errors=errors)
        if path == status and content.startswith("RUNNING "):
            assert len(launched) == 1 and launched[0].poll() is None
            raise AssertionError("intentional live gate assertion failure")
        return content

    monkeypatch.setattr(subprocess, "Popen", capture)
    monkeypatch.setattr(Path, "read_text", fault_after_running)
    try:
        with pytest.raises(AssertionError, match="intentional live gate assertion failure"):
            case(tmp_path)
        assert len(launched) == 1
        assert launched[0].poll() is not None, "assertion failure must release and drain the held gate"
        assert launched[0].returncode == 0
        assert original_read(status, encoding="utf-8").startswith("PASS ")
        assert not (tmp_path / "gate-async.lock").exists()
    finally:
        for process in launched:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def test_held_gate_fixture_keeps_child_live_until_explicit_release(tmp_path: Path) -> None:
    """Readiness is real child entry, with an owned lock until context release."""
    with _held_gate(tmp_path) as (process, content):
        assert process.poll() is None
        assert content.startswith("RUNNING ")
        assert (tmp_path / "gate-entry-status").read_text(encoding="utf-8").strip() == content
        assert (tmp_path / "gate-async.lock").read_text(encoding="utf-8").strip() == str(process.pid)
        assert os.getpgid(process.pid) == process.pid
    assert process.returncode == 0
    assert (tmp_path / "gate-status").read_text(encoding="utf-8").startswith("PASS ")
    assert not (tmp_path / "gate-async.lock").exists()


@pytest.mark.parametrize("gate_cmd, terminal", [("exit 0", "PASS "), ("exit 7", "FAIL ")])
def test_held_gate_fixture_refuses_completion_without_readiness(
    tmp_path: Path,
    gate_cmd: str,
    terminal: str,
) -> None:
    """Fast exit cannot stand in for a child held behind the release barrier."""
    with (
        pytest.raises(AssertionError, match="gate child did not acknowledge readiness"),
        _held_gate(tmp_path, gate_cmd=gate_cmd),
    ):
        pytest.fail("a child without readiness must never enter the test body")
    assert (tmp_path / "gate-status").read_text(encoding="utf-8").startswith(terminal)
    assert not (tmp_path / "gate-async.lock").exists()


@pytest.mark.parametrize("fault", ["missing-running", "released-lock"])
def test_original_gate_assertions_reject_broken_fixture_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Known-bad fixture commands must fail the original status/refusal checks."""
    native_env = _isolated_gate_env

    def broken_env(
        gate_cmd: str,
        *,
        status_file: Path,
        lock_file: Path,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        prefix = (
            'printf "PASS 1700000001\\n" > "$STATUS_FILE"; ' if fault == "missing-running" else 'rm -f "$LOCK_FILE"; '
        )
        env = native_env(prefix + gate_cmd, status_file=status_file, lock_file=lock_file, extra_env=extra_env)
        env["GLUDD_GATE_ASYNC_FORCE_PIDFILE"] = "1"
        return env

    monkeypatch.setattr(f"{__name__}._isolated_gate_env", broken_env)
    if fault == "missing-running":
        # The mutation is already stable at child entry; keep this negative
        # control immediate while exercising the original RUNNING assertion.
        def published_status(path: Path, prefix: str, timeout: float = 5.0) -> str:
            return path.read_text(encoding="utf-8")

        monkeypatch.setattr(f"{__name__}._wait_for_status", published_status)
        case = TestGateAsyncRunningWrittenImmediately().test_running_written_before_gate_runs
        message = "STATUS_FILE was not RUNNING within 5s"
    else:
        case = TestGateAsyncConcurrentRefused().test_second_launch_refused
        message = "Second gate should have been refused but returned 0"
    with pytest.raises(AssertionError, match=message):
        case(tmp_path)
    assert not (tmp_path / "gate-async.lock").exists()
