"""
Tests for scripts/gate_async.sh — the non-blocking, pollable gate launcher.

All tests use GATE_CMD plus checkout, status, lock, and resource-root overrides,
so no real pytest run is triggered and no enclosing-worktree state is touched.
"""

from __future__ import annotations

import json
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


def _inherited_signal_command(
    command: list[str],
    mode: str,
    witness: Path,
) -> list[str]:
    """Poison only this child before exec; never reset the real Bash fixture."""
    source = r"""
import json
import os
import signal
import sys
from pathlib import Path

modes = {
    "ignored-int": ((signal.SIGINT,), ()),
    "blocked-int": ((), (signal.SIGINT,)),
    "blocked-term": ((), (signal.SIGTERM,)),
    "ignored-int-blocked-term": ((signal.SIGINT,), (signal.SIGTERM,)),
}
ignored, blocked = modes[sys.argv[1]]
watched = (signal.SIGINT, signal.SIGTERM)
unrelated = signal.pthread_sigmask(signal.SIG_BLOCK, ()) - set(watched)
# Own a deterministic bad-state input, then prove it survives the actual exec.
for signum in watched:
    signal.signal(signum, signal.SIG_DFL)
signal.pthread_sigmask(signal.SIG_UNBLOCK, watched)
for signum in ignored:
    signal.signal(signum, signal.SIG_IGN)
signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
Path(sys.argv[2]).write_text(json.dumps({
    "pid": os.getpid(), "pgid": os.getpgid(0), "sid": os.getsid(0),
    "ignored_int": signal.getsignal(signal.SIGINT) == signal.SIG_IGN,
    "blocked_int": signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, ()),
    "blocked_term": signal.SIGTERM in signal.pthread_sigmask(signal.SIG_BLOCK, ()),
    "unrelated_blocked": sorted(int(signum) for signum in unrelated),
    "carrier": os.environ.get("GLUDD_SIGNAL_TEST_CARRIER"),
}), encoding="utf-8")
os.execvpe(sys.argv[3], sys.argv[3:], os.environ.copy())
"""
    return [sys.executable, "-P", "-c", source, mode, str(witness), *command]


def _interrupt_running_gate(
    tmp_path: Path,
    caught_signal: signal.Signals,
    *,
    signal_process_group: bool = True,
    inherited_signal_mode: str | None = None,
) -> tuple[int, str, str, Path, Path]:
    """Start an isolated gate, signal its process group, and collect evidence."""
    status = tmp_path / "gate-status"
    lock = tmp_path / "gate-async.lock"
    env = _isolated_gate_env(
        "sleep 30",
        status_file=status,
        lock_file=lock,
        extra_env={
            "GLUDD_GATE_ASYNC_FORCE_PIDFILE": "1",
            "SYSTEM_PYTHON": sys.executable,
            "GLUDD_SIGNAL_TEST_CARRIER": "owned unrelated carrier",
        },
    )
    command = ["bash", str(GATE_ASYNC_SH)]
    if inherited_signal_mode is not None:
        command = _inherited_signal_command(
            command,
            inherited_signal_mode,
            tmp_path / "inherited-launch.json",
        )
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        running = _wait_for_status(status, "RUNNING ")
        if inherited_signal_mode is not None:
            witness = json.loads((tmp_path / "inherited-launch.json").read_text(encoding="utf-8"))
            assert int(running.split()[2]) == proc.pid == witness["pid"]
            assert os.getpgid(proc.pid) == os.getsid(proc.pid) == proc.pid
            assert witness["pgid"] == witness["sid"] == proc.pid
            assert witness["carrier"] == "owned unrelated carrier"
            assert witness["ignored_int"] is (inherited_signal_mode in {"ignored-int", "ignored-int-blocked-term"})
            assert witness["blocked_int"] is (inherited_signal_mode == "blocked-int")
            assert witness["blocked_term"] is (inherited_signal_mode in {"blocked-term", "ignored-int-blocked-term"})
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


@pytest.mark.parametrize(
    "mode",
    [
        "ignored-int",
        "blocked-int",
        "blocked-term",
        "ignored-int-blocked-term",
    ],
)
def test_inherited_signal_state_survives_unguarded_native_bash_exec(
    tmp_path: Path,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active unguarded witness proves the same child input reaches Bash."""
    monkeypatch.setenv("GLUDD_SIGNAL_TEST_CARRIER", "owned unrelated carrier")
    before_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    before_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    witness = tmp_path / "before-exec.json"
    observed = tmp_path / "after-bash-exec.json"
    probe = r"""
import json
import os
import signal
import sys
from pathlib import Path

watched = (signal.SIGINT, signal.SIGTERM)
mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
Path(sys.argv[1]).write_text(json.dumps({
    "pid": os.getpid(), "pgid": os.getpgid(0), "sid": os.getsid(0),
    "ignored_int": signal.getsignal(signal.SIGINT) == signal.SIG_IGN,
    "blocked_int": signal.SIGINT in mask,
    "blocked_term": signal.SIGTERM in mask,
    "unrelated_blocked": sorted(int(signum) for signum in mask - set(watched)),
    "carrier": os.environ.get("GLUDD_SIGNAL_TEST_CARRIER"),
}), encoding="utf-8")
"""
    command = [
        "bash",
        "-c",
        'exec "$@"',
        "gludd-inherited-signal-control",
        sys.executable,
        "-P",
        "-c",
        probe,
        str(observed),
    ]
    result = subprocess.run(
        _inherited_signal_command(command, mode, witness),
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        start_new_session=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    before = json.loads(witness.read_text(encoding="utf-8"))
    after = json.loads(observed.read_text(encoding="utf-8"))
    assert after == before
    assert before["pid"] == before["pgid"] == before["sid"]
    assert before["ignored_int"] is (mode in {"ignored-int", "ignored-int-blocked-term"})
    assert before["blocked_int"] is (mode == "blocked-int")
    assert before["blocked_term"] is (mode in {"blocked-term", "ignored-int-blocked-term"})
    assert before["carrier"] == "owned unrelated carrier"
    assert {signum: signal.getsignal(signum) for signum in before_handlers} == before_handlers
    assert signal.pthread_sigmask(signal.SIG_BLOCK, ()) == before_mask
    assert os.environ["GLUDD_SIGNAL_TEST_CARRIER"] == "owned unrelated carrier"


@pytest.mark.parametrize(
    ("mode", "caught_signal"),
    [
        ("ignored-int", signal.SIGINT),
        ("blocked-int", signal.SIGINT),
        ("blocked-term", signal.SIGTERM),
        ("ignored-int-blocked-term", signal.SIGTERM),
    ],
)
@pytest.mark.parametrize("signal_process_group", [True, False], ids=["group", "wrapper"])
def test_native_wrapper_resets_inherited_signal_state(
    tmp_path: Path,
    mode: str,
    caught_signal: signal.Signals,
    signal_process_group: bool,
) -> None:
    """The original Bash entrypoint owns its reset, terminal record and drain."""
    result, stdout, stderr, status, lock = _interrupt_running_gate(
        tmp_path,
        caught_signal,
        signal_process_group=signal_process_group,
        inherited_signal_mode=mode,
    )
    expected = 128 + caught_signal
    assert result == expected, f"stdout: {stdout}\nstderr: {stderr}"
    lines = status.read_text(encoding="utf-8").splitlines()
    parts = lines[0].split()
    assert parts[0] == "ABORTED" and int(parts[1]) > 1_700_000_000
    assert parts[2:] == [
        f"signal={caught_signal.name.removeprefix('SIG')}",
        f"rc={expected}",
        "cleanup_rc=0",
    ]
    assert lines[1] == "=== GATE: ABORTED ==="
    assert "[gate-kill]" in stdout
    assert not lock.exists()
    assert not list(tmp_path.glob("gate-status.*.tmp"))


def test_signal_bootstrap_preserves_native_import_boundary_and_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise an active CWD shadow before the same dirty-state native entry."""
    carrier = tmp_path / "explicit-pythonpath-carrier"
    carrier.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(carrier))
    marker = tmp_path / "owned import shadow"
    (tmp_path / "signal.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('active owned signal import')\n"
        "raise RuntimeError('owned import-shadow witness')\n",
        encoding="utf-8",
    )
    env = _isolated_gate_env(
        "exit 0",
        status_file=tmp_path / "gate-status",
        lock_file=tmp_path / "gate-async.lock",
        extra_env={"SYSTEM_PYTHON": sys.executable, "GLUDD_GATE_ASYNC_FORCE_PIDFILE": "1"},
    )
    unguarded = subprocess.run(
        [sys.executable, "-c", "import signal"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert unguarded.returncode != 0
    assert marker.read_text(encoding="utf-8") == "active owned signal import"
    marker.unlink()
    reference = "owned reference with spaces"
    result = subprocess.run(
        _inherited_signal_command(
            ["bash", str(GATE_ASYNC_SH), reference],
            "ignored-int-blocked-term",
            tmp_path / "before-native-exec.json",
        ),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
        start_new_session=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), "the actual bootstrap must retain its native -P boundary"
    assert f"ref='{reference}'" in result.stdout
    assert (tmp_path / "gate-status").read_text(encoding="utf-8").startswith("PASS ")
    assert not (tmp_path / "gate-async.lock").exists()
    assert env["PYTHONPATH"] == os.environ["PYTHONPATH"] == str(carrier)


@pytest.mark.parametrize("handoff_second_signal", [False, True])
def test_parent_term_at_native_child_pid_handoff_drains_owned_pipes(
    tmp_path: Path,
    handoff_second_signal: bool,
) -> None:
    """Exercise the actual launch and pending-signal handoff without sleeps."""
    status = tmp_path / "gate-status"
    lock = tmp_path / "gate-async.lock"
    witness = tmp_path / "handoff-witness"
    witness_tmp = tmp_path / "handoff-witness.tmp"
    second_witness = tmp_path / "second-signal-witness"
    second_tmp = tmp_path / "second-signal-witness.tmp"
    startup = tmp_path / "owned-bash-startup"
    ready_read, ready_write = os.pipe()
    with (
        os.fdopen(ready_read, "rb", buffering=0) as ready,
        os.fdopen(ready_write, "wb", buffering=0) as ready_sender,
    ):
        payload = (
            f"import os, signal; os.close({ready_read}); os.write({ready_write}, "
            + "(str(os.getpid()) + ' ' + str(os.getpgrp()) + ' ' + str"
            "(os.getsid(0)) + ' ' + str(int(os.environ.get('GLUDD_SI"
            "GNAL_TEST_CARRIER') == 'owned unrelated carrier')) + '\\"
            "n').encode()); " + f"os.close({ready_write}); signal.pause()"
        )
        first_probe = (
            "if [[ \"$BASH_COMMAND\" == 'GATE_CHILD_PID=$!' ]]; then\n"
            + ("" if handoff_second_signal else "    trap - DEBUG\n")
            + f"    read -r -t 5 handoff_pid handoff_pgid handoff_sid handoff_kept <&{ready_read}"
            + " || exit 73\n"
            + '    printf \'ready %s %s %s %s %s %s %s\\n\' "$$" "$!" "$h'
            'andoff_pid" "$handoff_pgid" "$handoff_sid" "$handoff_ke'
            'pt" "$GATE_CHILD_PID" > '
            + shlex.quote(str(witness_tmp))
            + "\n"
            + "    mv -f "
            + shlex.quote(str(witness_tmp))
            + " "
            + shlex.quote(str(witness))
            + "\n"
            + '    kill -TERM "$$"\n'
        )
        second_probe = (
            f"elif [[ {int(handoff_second_signal)} == 1 && "
            + '"${GATE_LAUNCHING:-}" == 0 && -n "${GATE_CHILD_PID:-}" && "${GATE_PENDING_SIGNAL:-}" == TERM ]]; then\n'
            + "    trap - DEBUG\n"
            + "    printf 'second\\n' > "
            + shlex.quote(str(second_tmp))
            + "\n"
            + "    mv -f "
            + shlex.quote(str(second_tmp))
            + " "
            + shlex.quote(str(second_witness))
            + "\n"
            + '    kill -INT "$$"\n'
            + "fi"
        )
        debug = (
            'if [[ "$0" == '
            + shlex.quote(str(GATE_ASYNC_SH))
            + " ]]; then\n"
            + first_probe
            + second_probe
            + "\nfi"
        )
        startup_source = "trap " + shlex.quote(debug) + " DEBUG\n"
        startup.write_text(startup_source, encoding="utf-8")
        env = _isolated_gate_env(
            shlex.join(["exec", sys.executable, "-P", "-c", payload]),
            status_file=status,
            lock_file=lock,
            extra_env={
                "GLUDD_GATE_ASYNC_FORCE_PIDFILE": "1",
                "SYSTEM_PYTHON": sys.executable,
                "GLUDD_SIGNAL_TEST_CARRIER": "owned unrelated carrier",
                "BASH_ENV": str(startup),
            },
        )
        process = subprocess.Popen(
            ["bash", str(GATE_ASYNC_SH)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
            pass_fds=(ready.fileno(), ready_sender.fileno()),
        )
        ready_sender.close()
        ready.close()
        try:
            acknowledgement = _wait_for_status(witness, "ready ", timeout=5)
            fields = acknowledgement.rstrip("\n").split(" ")
            assert len(fields) == 8, "native handoff probe did not preserve its owned fields"
            assert fields[0] == "ready"
            assert int(fields[1]) == process.pid
            assert int(fields[2]) == int(fields[3]) > 0
            assert int(fields[4]) == int(fields[5]) == process.pid
            assert fields[6] == "1", "native child lost the unrelated owned carrier"
            assert fields[7] == "", "probe did not run before the original PID assignment"
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 143, f"stdout: {stdout}\nstderr: {stderr}"
            terminal = status.read_text(encoding="utf-8").splitlines()
            assert len(terminal) == 2 and terminal[1] == "=== GATE: ABORTED ==="
            parts = terminal[0].split()
            assert parts[0] == "ABORTED" and int(parts[1]) > 1_700_000_000
            assert parts[2:] == ["signal=TERM", "rc=143", "cleanup_rc=0"]
            assert not lock.exists(), "native handoff cancellation retained its owned lock"
            assert not list(tmp_path.glob("gate-status.*.tmp"))
            if handoff_second_signal:
                assert second_witness.read_text(encoding="utf-8") == "second\n"
            else:
                assert not second_witness.exists()
        finally:
            # Own the entire new session, including a child holding pipes after
            # its wrapper has exited. Never leave this RED fixture running.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                process.communicate(timeout=5)
            finally:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
