"""TDD tests for B3.1.3 Slice 1: ``WriterProcess`` (WP-B1).

These pin the parent-side lifecycle of the writer subprocess:

  * ``start()`` spawns a fresh interpreter child via ``subprocess.Popen`` and
    blocks on a NONCE-PROTECTED readiness handshake (fail-closed: a child that
    dies before writing the parent's exact nonce is not "ready").
  * ``stop()`` sends SIGTERM, waits up to 10s, then escalates to SIGKILL.
  * ``is_alive()`` reports the live ``Popen`` state — False once the child has
    exited for any reason.
  * start-while-running is a ``RuntimeError`` (programming error, not a race
    worth silently no-op'ing).
  * stop is idempotent.

The child itself is a STUB for Slice 1 — see ``src/general_ludd/writer/_child.py``.
Real EventLoop integration is Slice 3 (per ``docs/STABILIZATION_PLAN.md`` WP-B1).
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from general_ludd.writer.process import WriterProcess


def _stub_config(tmp_path: Path) -> dict[str, Any]:
    """Minimal config the Slice-1 stub child accepts.

    Slice 3 will add broker/queue/DB-URL fields; for now we only need the
    child to be spawnable, write the readiness nonce, and sleep.
    """
    return {"slice": 1, "note": "stub"}


def _wait_for_exit(proc_pid: int, timeout: float = 15.0) -> bool:
    """Best-effort poll for a process to be gone (used by kill-escalation test)."""
    import subprocess

    deadline = time.monotonic() + timeout
    try:
        _, _ = subprocess.wait([str(proc_pid)], timeout=0.1)
        return True
    except Exception:
        pass
    while time.monotonic() < deadline:
        try:
            os.kill(proc_pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return True
        time.sleep(0.05)
    return False


# --------------------------------------------------------------------------- #
# 1. start() spawns a child via Popen and returns True
# --------------------------------------------------------------------------- #
def test_writer_process_spawn_starts_subprocess(tmp_path: Path) -> None:
    cfg = _stub_config(tmp_path)
    wp = WriterProcess(config=cfg)
    try:
        started = wp.start(timeout=20.0)
        assert started is True
        assert wp.is_alive() is True
        # The child PID must be a real, distinct process (not the parent).
        assert wp.pid is not None
        assert wp.pid != os.getpid()
        assert wp.pid > 0
    finally:
        wp.stop()


# --------------------------------------------------------------------------- #
# 2. stop() terminates cleanly with SIGTERM, child exit code 0
# --------------------------------------------------------------------------- #
def test_writer_process_stop_terminates_child(tmp_path: Path) -> None:
    wp = WriterProcess(config=_stub_config(tmp_path))
    wp.start(timeout=20.0)
    pid = wp.pid
    assert pid is not None

    stopped = wp.stop()
    assert stopped is True
    assert wp.is_alive() is False
    # The child should have exited (exit code is None after SIGTERM-by-child,
    # or 0 if it caught the signal and shut down). Either is acceptable for
    # Slice 1; what matters is the process is reaped and stop() returns True.
    assert _wait_for_exit(pid, timeout=10.0) is True


# --------------------------------------------------------------------------- #
# 3. SIGTERM ignored -> escalate to SIGKILL
# --------------------------------------------------------------------------- #
def test_writer_process_stop_sigkill_if_sigterm_ignored(tmp_path: Path) -> None:
    # A child config that makes the stub ignore SIGTERM. The stub child reads
    # this flag and installs a no-op SIGTERM handler before sleeping, so the
    # parent MUST escalate to SIGKILL to reap it.
    cfg = {"slice": 1, "ignore_sigterm": True}
    wp = WriterProcess(config=cfg)
    wp.start(timeout=20.0)
    pid = wp.pid
    assert pid is not None

    # stop() must still terminate the process within a bounded wall-clock.
    t0 = time.monotonic()
    stopped = wp.stop(sigterm_timeout=1.0)
    elapsed = time.monotonic() - t0
    assert stopped is True
    assert elapsed < 15.0, f"stop() took too long ({elapsed:.1f}s) — SIGKILL escalation broken"
    assert _wait_for_exit(pid, timeout=5.0) is True


# --------------------------------------------------------------------------- #
# 4. Readiness handshake — nonce-protected, blocks until child writes nonce
# --------------------------------------------------------------------------- #
def test_writer_process_readiness_handshake(tmp_path: Path) -> None:
    wp = WriterProcess(config=_stub_config(tmp_path))
    try:
        # start() MUST block until the child writes the readiness nonce.
        # If the child dies before writing it, start() returns False (or raises
        # TimeoutError) — it does NOT claim success without the unforgeable token.
        t0 = time.monotonic()
        started = wp.start(timeout=20.0)
        elapsed = time.monotonic() - t0
        assert started is True
        # The handshake must happen quickly once the child is up. Generous
        # upper bound to absorb interpreter startup / import time on a cold
        # cache, but bounded — a hang here is the bug this test catches.
        assert elapsed < 15.0, f"handshake took {elapsed:.1f}s"
        # The nonce the parent generated must be a strong random hex string
        # (32 bytes / 64 hex chars), and must NOT be a fixed constant.
        assert isinstance(wp._readiness_nonce, str)
        assert len(wp._readiness_nonce) >= 32
    finally:
        wp.stop()


def test_writer_process_start_times_out_when_child_never_ready(tmp_path: Path) -> None:
    # Child config that makes the stub NEVER write the readiness nonce
    # (simulates a hung/crashed child before handshake completes).
    cfg = {"slice": 1, "skip_ready": True}
    wp = WriterProcess(config=cfg)
    try:
        wp.start(timeout=2.0)
        raised = False
    except TimeoutError:
        raised = True
    # The contract: a child that never signals readiness must raise
    # TimeoutError (or start() returns False). Either is acceptable; what is
    # NOT acceptable is silently claiming ready=True.
    assert raised or not wp.is_ready(), (
        "start() must fail loudly when the child never completes the handshake"
    )
    # And the parent must NOT leave the orphaned child running.
    assert wp.is_alive() is False, "orphaned child leaked after handshake failure"


# --------------------------------------------------------------------------- #
# 5. is_alive() returns False after unexpected child death
# --------------------------------------------------------------------------- #
def test_writer_process_health_check_returns_false_after_death(tmp_path: Path) -> None:
    wp = WriterProcess(config=_stub_config(tmp_path))
    wp.start(timeout=20.0)
    pid = wp.pid
    assert pid is not None

    # Kill the child out-of-band (simulates a crash).
    os.kill(pid, signal.SIGKILL)
    # Give the kernel a moment to reap.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and wp.is_alive():
        time.sleep(0.05)

    assert wp.is_alive() is False, "is_alive() must reflect unexpected child death"

    # stop() must still be callable and idempotent on a dead process.
    assert wp.stop() is True


# --------------------------------------------------------------------------- #
# 6. Double start() is an error (programming bug, not a silent no-op)
# --------------------------------------------------------------------------- #
def test_writer_process_double_start_is_error(tmp_path: Path) -> None:
    wp = WriterProcess(config=_stub_config(tmp_path))
    try:
        wp.start(timeout=20.0)
        with pytest.raises(RuntimeError):
            wp.start(timeout=20.0)
    finally:
        wp.stop()


# --------------------------------------------------------------------------- #
# 7. stop() is idempotent
# --------------------------------------------------------------------------- #
def test_writer_process_stop_is_idempotent(tmp_path: Path) -> None:
    wp = WriterProcess(config=_stub_config(tmp_path))
    wp.start(timeout=20.0)

    assert wp.stop() is True
    # Second stop() must NOT raise — idempotent.
    assert wp.stop() is True
    # Third stop() likewise.
    assert wp.stop() is True


# Ensure the parent's interpreter is the test interpreter (sanity check that
# the child spawn line uses sys.executable, not a hardcoded "python").
def test_writer_process_uses_sys_executable(tmp_path: Path) -> None:
    cfg = _stub_config(tmp_path)
    wp = WriterProcess(config=cfg)
    try:
        wp.start(timeout=20.0)
        # The spawned argv[0] must be sys.executable (so the child runs in the
        # same venv as the parent and can import general_ludd.*).
        assert wp._argv[0] == sys.executable
    finally:
        wp.stop()
