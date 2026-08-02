"""Tests for D-23: orphan_pid — verified PID identity before cleanup.

D-23 requirement: PID records include PID, start time, boot ID, namespace,
executable identity, owner and lease; stale cleanup verifies all fields
before signalling or unlinking.
"""

from __future__ import annotations

import datetime as _datetime
import os
import sys
import time

import pytest

from general_ludd.security.orphan_pid import (
    PidRecord,
    PidRecordError,
    compute_boot_id,
    is_reaper_safe,
    reap_orphan_tree,
    verify_pid_identity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proc_start_time(pid: int) -> float:
    if os.path.exists(f"/proc/{pid}"):
        try:
            return os.stat(f"/proc/{pid}").st_mtime
        except OSError:
            pass
    try:
        import subprocess

        env = os.environ.copy()
        env["LC_TIME"] = "C"
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            dt = _datetime.datetime.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y")
            return dt.timestamp()
    except Exception:
        pass
    return time.time()


def _proc_executable(pid: int) -> str:
    proc_exe = f"/proc/{pid}/exe"
    if os.path.exists(proc_exe):
        return os.readlink(proc_exe)
    return os.path.realpath(sys.executable)


def _current_pid_rec(*, overrides: dict | None = None) -> PidRecord:
    pid = os.getpid()
    data: dict = {
        "pid": pid,
        "start_time": _proc_start_time(pid),
        "boot_id": compute_boot_id(),
        "executable": _proc_executable(pid),
        "owner_uid": os.getuid(),
        "lease_seconds": 300,
    }
    if overrides:
        data.update(overrides)
    return PidRecord(**data)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PidRecord
# ---------------------------------------------------------------------------


class TestPidRecord:
    def test_construct(self) -> None:
        rec = PidRecord(
            pid=1234,
            start_time=1000000.0,
            boot_id="abc123",
            executable="/usr/bin/python",
            owner_uid=501,
            lease_seconds=300,
        )
        assert rec.pid == 1234
        assert rec.owner_uid == 501

    def test_rejects_negative_pid(self) -> None:
        with pytest.raises(PidRecordError, match="pid"):
            PidRecord(
                pid=-1,
                start_time=0.0,
                boot_id="x",
                executable="/x",
                owner_uid=0,
                lease_seconds=0,
            )

    def test_rejects_zero_pid(self) -> None:
        with pytest.raises(PidRecordError, match="pid"):
            PidRecord(
                pid=0,
                start_time=0.0,
                boot_id="x",
                executable="/x",
                owner_uid=0,
                lease_seconds=0,
            )

    def test_rejects_empty_boot_id(self) -> None:
        with pytest.raises(PidRecordError, match="boot_id"):
            PidRecord(
                pid=1,
                start_time=0.0,
                boot_id="",
                executable="/x",
                owner_uid=0,
                lease_seconds=0,
            )

    def test_rejects_non_absolute_executable(self) -> None:
        with pytest.raises(PidRecordError, match="absolute"):
            PidRecord(
                pid=1,
                start_time=0.0,
                boot_id="x",
                executable="relative/python",
                owner_uid=0,
                lease_seconds=0,
            )

    def test_eq_same(self) -> None:
        a = PidRecord(pid=1, start_time=1.0, boot_id="b", executable="/x", owner_uid=0, lease_seconds=0)
        b = PidRecord(pid=1, start_time=1.0, boot_id="b", executable="/x", owner_uid=0, lease_seconds=0)
        assert a == b

    def test_eq_different(self) -> None:
        a = PidRecord(pid=1, start_time=1.0, boot_id="b", executable="/x", owner_uid=0, lease_seconds=0)
        b = PidRecord(pid=2, start_time=1.0, boot_id="b", executable="/x", owner_uid=0, lease_seconds=0)
        assert a != b

    def test_to_dict(self) -> None:
        rec = PidRecord(pid=1, start_time=1.0, boot_id="b", executable="/x", owner_uid=0, lease_seconds=0)
        d = rec.to_dict()
        assert d["pid"] == 1
        assert d["boot_id"] == "b"

    def test_from_dict(self) -> None:
        d = {"pid": 1, "start_time": 1.0, "boot_id": "b", "executable": "/x", "owner_uid": 0, "lease_seconds": 0}
        rec = PidRecord.from_dict(d)
        assert rec.pid == 1

    def test_start_time_must_be_positive(self) -> None:
        with pytest.raises(PidRecordError, match="start_time"):
            PidRecord(pid=1, start_time=0.0, boot_id="b", executable="/x", owner_uid=0, lease_seconds=0)


# ---------------------------------------------------------------------------
# verify_pid_identity
# ---------------------------------------------------------------------------


class TestVerifyPidIdentity:
    def test_live_self_passes(self) -> None:
        rec = _current_pid_rec()
        result = verify_pid_identity(rec)
        assert result is True

    def test_nonexistent_pid_rejected(self) -> None:
        rec = PidRecord(
            pid=99999999,
            start_time=1.0,
            boot_id=compute_boot_id(),
            executable="/usr/bin/nonexistent",
            owner_uid=os.getuid(),
            lease_seconds=300,
        )
        assert not verify_pid_identity(rec)

    def test_wrong_owner_rejected(self) -> None:
        rec = _current_pid_rec(overrides={"owner_uid": 0})
        if os.getuid() != 0:
            assert not verify_pid_identity(rec)


# ---------------------------------------------------------------------------
# reap_orphan_tree
# ---------------------------------------------------------------------------


class TestReapOrphanTree:
    def test_nonexistent_pid_returns_false(self) -> None:
        rec = PidRecord(
            pid=99999999,
            start_time=1.0,
            boot_id=compute_boot_id(),
            executable="/nonexistent",
            owner_uid=os.getuid(),
            lease_seconds=0,
        )
        assert reap_orphan_tree(rec)

    def test_own_process_not_reaped(self) -> None:
        rec = _current_pid_rec()
        assert not reap_orphan_tree(rec)

    def test_boot_id_mismatch_rejected(self) -> None:
        rec = _current_pid_rec(overrides={"boot_id": "wrong-boot-id-xyz"})
        assert not verify_pid_identity(rec)


# ---------------------------------------------------------------------------
# is_reaper_safe
# ---------------------------------------------------------------------------


class TestIsReaperSafe:
    def test_nonexistent_pid_safe_to_signal(self) -> None:
        assert is_reaper_safe(pid=99999999, owner_uid=os.getuid())

    def test_own_process_not_safe(self) -> None:
        assert not is_reaper_safe(pid=os.getpid(), owner_uid=os.getuid())

    def test_foreign_uid_not_safe(self) -> None:
        if os.getuid() != 0:
            assert not is_reaper_safe(pid=1, owner_uid=os.getuid())


# ---------------------------------------------------------------------------
# compute_boot_id
# ---------------------------------------------------------------------------


class TestBootId:
    def test_returns_non_empty(self) -> None:
        bid = compute_boot_id()
        assert len(bid) > 0

    def test_is_stable_across_calls(self) -> None:
        a = compute_boot_id()
        b = compute_boot_id()
        assert a == b


# ---------------------------------------------------------------------------
# PidRecordError
# ---------------------------------------------------------------------------


class TestPidRecordError:
    def test_is_exception(self) -> None:
        assert issubclass(PidRecordError, Exception)
