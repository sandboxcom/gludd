"""D-23: Verified PID identity before cleanup — no forged or stale signals."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from general_ludd.security.orphan_pid import (
    PidRecord,
    PidRecordError,
    compute_boot_id,
    is_reaper_safe,
    reap_orphan_tree,
    verify_pid_identity,
)


class TestPidRecord:
    def test_create_valid(self) -> None:
        r = PidRecord(
            pid=1234,
            start_time=1600000000.0,
            boot_id="abc-def",
            executable="/usr/bin/python",
            owner_uid=501,
            lease_seconds=3600.0,
        )
        assert r.pid == 1234
        assert r.owner_uid == 501

    def test_rejects_zero_pid(self) -> None:
        with pytest.raises(PidRecordError, match="positive"):
            PidRecord(pid=0, start_time=1.0, boot_id="x", executable="/bin/sh", owner_uid=0, lease_seconds=1)

    def test_rejects_empty_boot_id(self) -> None:
        with pytest.raises(PidRecordError, match="boot_id"):
            PidRecord(pid=1, start_time=1.0, boot_id="", executable="/bin/sh", owner_uid=0, lease_seconds=1)

    def test_rejects_relative_executable(self) -> None:
        with pytest.raises(PidRecordError, match="absolute"):
            PidRecord(pid=1, start_time=1.0, boot_id="x", executable="python", owner_uid=0, lease_seconds=1)

    def test_rejects_negative_start_time(self) -> None:
        with pytest.raises(PidRecordError, match="start_time"):
            PidRecord(pid=1, start_time=-1.0, boot_id="x", executable="/bin/sh", owner_uid=0, lease_seconds=1)

    def test_rejects_negative_lease(self) -> None:
        with pytest.raises(PidRecordError, match="lease_seconds"):
            PidRecord(pid=1, start_time=1.0, boot_id="x", executable="/bin/sh", owner_uid=0, lease_seconds=-1)

    def test_to_dict(self) -> None:
        r = PidRecord(pid=1, start_time=2.0, boot_id="b", executable="/bin/sh", owner_uid=0, lease_seconds=3)
        d = r.to_dict()
        assert d["pid"] == 1
        assert d["boot_id"] == "b"

    def test_from_dict_roundtrip(self) -> None:
        r = PidRecord(pid=99, start_time=3.0, boot_id="x", executable="/bin/echo", owner_uid=501, lease_seconds=10)
        d = r.to_dict()
        r2 = PidRecord.from_dict(d)
        assert r2.pid == r.pid
        assert r2.boot_id == r.boot_id
        assert r2.executable == r.executable


class TestComputeBootId:
    def test_returns_cached_value(self) -> None:
        a = compute_boot_id()
        b = compute_boot_id()
        assert a == b
        assert isinstance(a, str)
        assert len(a) > 0


class TestIsReaperSafe:
    def test_never_safe_for_own_pid(self) -> None:
        assert is_reaper_safe(pid=os.getpid(), owner_uid=os.getuid()) is False

    def test_safe_when_pid_does_not_exist(self) -> None:
        assert is_reaper_safe(pid=99999999, owner_uid=os.getuid()) is True


class TestVerifyPidIdentity:
    def test_returns_false_for_nonexistent_pid(self) -> None:
        r = PidRecord(
            pid=99999999,
            start_time=time_mock(),
            boot_id=compute_boot_id(),
            executable="/bin/true",
            owner_uid=0,
            lease_seconds=10,
        )
        assert verify_pid_identity(r) is False

    def test_returns_false_for_wrong_owner(self) -> None:
        with (
            patch("general_ludd.security.orphan_pid._pid_exists", return_value=True),
            patch("general_ludd.security.orphan_pid._read_boot_for_pid", return_value=compute_boot_id()),
            patch("general_ludd.security.orphan_pid._read_exe_for_pid", return_value="/bin/sh"),
            patch("general_ludd.security.orphan_pid._read_uid_for_pid", return_value=9999),
        ):
            r = PidRecord(
                pid=12345,
                start_time=1.0,
                boot_id=compute_boot_id(),
                executable="/bin/sh",
                owner_uid=0,
                lease_seconds=10,
            )
            assert verify_pid_identity(r) is False


class TestReapOrphanTree:
    def test_never_reaps_own_pid(self) -> None:
        r = PidRecord(
            pid=os.getpid(),
            start_time=0.1,
            boot_id=compute_boot_id(),
            executable="/usr/bin/python",
            owner_uid=os.getuid(),
            lease_seconds=10,
        )
        assert reap_orphan_tree(r) is False

    def test_returns_true_for_gone_pid(self) -> None:
        r = PidRecord(
            pid=99999999,
            start_time=1.0,
            boot_id="x",
            executable="/bin/sh",
            owner_uid=0,
            lease_seconds=1,
        )
        assert reap_orphan_tree(r) is True

    def test_returns_false_when_identity_verification_fails(self) -> None:
        with (
            patch("general_ludd.security.orphan_pid._pid_exists", return_value=True),
            patch("general_ludd.security.orphan_pid.verify_pid_identity", return_value=False),
        ):
            r = PidRecord(pid=12345, start_time=1.0, boot_id="x", executable="/bin/sh", owner_uid=0, lease_seconds=1)
            assert reap_orphan_tree(r) is False


def time_mock() -> float:
    return 1_600_000_000.0
