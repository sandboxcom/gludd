"""Tests for the gludd managed-process registry.

Cover the safety boundary (only managed PIDs, identity-checked, allow-listed
signals) and a real spawn -> SIGTERM -> exit round-trip.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

import pytest

from general_ludd.process.registry import (
    ManagedProcess,
    ProcessRegistry,
    ProcessRegistryError,
    default_registry,
)


def _spawn_sleeper(seconds: int = 30) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, test-controlled
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        start_new_session=True,
    )


def test_default_registry_is_singleton() -> None:
    assert default_registry() is default_registry()


def test_register_captures_metadata_and_create_time() -> None:
    reg = ProcessRegistry()
    proc = _spawn_sleeper()
    try:
        rec = reg.register(
            proc.pid, ["python", "-c", "sleep"], job_id="J1", project_id="P1", origin="test"
        )
        assert isinstance(rec, ManagedProcess)
        assert rec.pid == proc.pid
        assert rec.job_id == "J1"
        assert rec.project_id == "P1"
        assert rec.origin == "test"
        # psutil is a core dep, so create_time should have been captured.
        assert rec.create_time is not None
        assert reg.is_managed(proc.pid)
        assert reg.is_alive(proc.pid)
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_signal_refuses_unmanaged_pid() -> None:
    reg = ProcessRegistry()
    proc = _spawn_sleeper()
    try:
        # Live process, but never registered -> must refuse.
        with pytest.raises(ProcessRegistryError, match="not a gludd-managed process"):
            reg.signal(proc.pid, "SIGTERM")
        # And it is still alive (the refusal was a no-op at the kernel).
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_signal_refuses_disallowed_signal() -> None:
    reg = ProcessRegistry()
    proc = _spawn_sleeper()
    try:
        reg.register(proc.pid, ["sleeper"])
        # SIGCHLD is real but deliberately outside the allow-list.
        with pytest.raises(ProcessRegistryError, match="allow-list"):
            reg.signal(proc.pid, "SIGCHLD")
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_signal_refuses_on_pid_reuse_identity_mismatch() -> None:
    reg = ProcessRegistry()
    proc = _spawn_sleeper()
    try:
        rec = reg.register(proc.pid, ["sleeper"])
        # Simulate the kernel recycling this PID for a different process by
        # corrupting the recorded identity: the live create_time no longer
        # matches, so the signal must be refused.
        rec.create_time = (rec.create_time or 0.0) - 9999.0
        assert reg.is_alive(proc.pid) is False
        with pytest.raises(ProcessRegistryError, match="identity check failed"):
            reg.signal(proc.pid, "SIGKILL")
        # The real process is untouched.
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_signal_delivers_sigterm_and_process_exits() -> None:
    reg = ProcessRegistry()
    proc = _spawn_sleeper()
    reg.register(proc.pid, ["sleeper"])
    reg.signal(proc.pid, "SIGTERM")
    # The sleeper has no handler, so SIGTERM terminates it.
    rc = proc.wait(timeout=5)
    assert rc != 0
    assert proc.poll() is not None


def test_signal_group_terminates_session() -> None:
    reg = ProcessRegistry()
    proc = _spawn_sleeper()  # start_new_session -> pid is its own pgid leader
    reg.register(proc.pid, ["sleeper"])
    reg.signal(proc.pid, "SIGTERM", group=True)
    rc = proc.wait(timeout=5)
    assert rc != 0


def test_resolve_signal_accepts_name_number_and_rejects_bad() -> None:
    assert ProcessRegistry.resolve_signal("SIGTERM") == int(signal.SIGTERM)
    assert ProcessRegistry.resolve_signal("term") == int(signal.SIGTERM)
    assert ProcessRegistry.resolve_signal(int(signal.SIGHUP)) == int(signal.SIGHUP)
    with pytest.raises(ProcessRegistryError):
        ProcessRegistry.resolve_signal("SIGCHLD")
    with pytest.raises(ProcessRegistryError):
        ProcessRegistry.resolve_signal(99)


def test_allowed_signals_contains_core_set() -> None:
    allowed = ProcessRegistry.allowed_signals()
    for name in ("SIGTERM", "SIGHUP", "SIGINT", "SIGUSR1", "SIGUSR2", "SIGKILL", "SIGCONT"):
        assert name in allowed


def test_deregister_and_list_active_only() -> None:
    reg = ProcessRegistry()
    proc = _spawn_sleeper()
    reg.register(proc.pid, ["sleeper"])
    assert len(reg.list()) == 1
    assert len(reg.list(active_only=True)) == 1
    proc.kill()
    proc.wait(timeout=5)
    # Give the kernel a moment to fully reap the zombie.
    time.sleep(0.2)
    # Dead process: still in the raw list, but filtered out of active_only.
    assert len(reg.list(active_only=True)) == 0
    assert reg.deregister(proc.pid) is not None
    assert reg.deregister(proc.pid) is None
    assert len(reg.list()) == 0


def test_reap_evicts_dead_entries() -> None:
    reg = ProcessRegistry()
    proc = _spawn_sleeper()
    reg.register(proc.pid, ["sleeper"])
    proc.kill()
    proc.wait(timeout=5)
    time.sleep(0.2)
    evicted = reg.reap()
    assert proc.pid in evicted
    assert not reg.is_managed(proc.pid)
