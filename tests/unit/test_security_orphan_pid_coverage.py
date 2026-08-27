"""Behavioral coverage for fail-closed PID ownership helpers."""

from __future__ import annotations

import os
import signal
import subprocess
from os import PathLike
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import general_ludd.security.orphan_pid as orphan


def _record(**overrides: object) -> orphan.PidRecord:
    values: dict[str, object] = {
        "pid": 1234,
        "start_time": 100.0,
        "boot_id": "boot",
        "executable": "/bin/tool",
        "owner_uid": 501,
        "lease_seconds": 20.0,
    }
    values.update(overrides)
    return orphan.PidRecord(
        pid=cast(int, values["pid"]),
        start_time=cast(float, values["start_time"]),
        boot_id=cast(str, values["boot_id"]),
        executable=cast(str, values["executable"]),
        owner_uid=cast(int, values["owner_uid"]),
        lease_seconds=cast(float, values["lease_seconds"]),
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"boot": OSError("gone")}, False),
        ({"boot": "other"}, False),
        ({"exe": OSError("gone")}, False),
        ({"exe": "/bin/other"}, False),
        ({"uid": OSError("gone")}, False),
        ({"uid": 999}, False),
        ({"start": 200.0}, False),
        ({"start": None}, True),
        ({"start": 101.0}, True),
    ],
)
def test_verify_pid_identity_checks_every_observed_field(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    expected: bool,
) -> None:
    def observed(name: str, default: object) -> object:
        value = overrides.get(name, default)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(orphan, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(orphan, "_read_boot_for_pid", lambda _pid: observed("boot", "boot"))
    monkeypatch.setattr(orphan, "_read_exe_for_pid", lambda _pid: observed("exe", "/bin/tool"))
    monkeypatch.setattr(orphan, "_read_uid_for_pid", lambda _pid: observed("uid", 501))
    monkeypatch.setattr(orphan, "_read_start_time_for_pid", lambda _pid: observed("start", 100.0))

    assert orphan.verify_pid_identity(_record()) is expected


def test_reaper_refuses_unverified_or_unsafe_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orphan, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(orphan, "verify_pid_identity", lambda _record: True)
    monkeypatch.setattr(orphan, "is_reaper_safe", lambda **_kwargs: False)

    assert orphan.reap_orphan_tree(_record()) is False


@pytest.mark.parametrize(
    ("exists", "expected_signals", "expected"),
    [([True, False, False], [signal.SIGTERM], True),
     ([True, True, False], [signal.SIGTERM, signal.SIGKILL], True),
     ([True, True, True], [signal.SIGTERM, signal.SIGKILL], False)],
)
def test_reaper_owns_term_then_bounded_kill(
    monkeypatch: pytest.MonkeyPatch,
    exists: list[bool],
    expected_signals: list[signal.Signals],
    expected: bool,
) -> None:
    observations = iter(exists)
    sent: list[signal.Signals] = []
    monkeypatch.setattr(orphan, "_pid_exists", lambda _pid: next(observations))
    monkeypatch.setattr(orphan, "verify_pid_identity", lambda _record: True)
    monkeypatch.setattr(orphan, "is_reaper_safe", lambda **_kwargs: True)
    monkeypatch.setattr(orphan, "_send_signal_tree", lambda _pid, sig: sent.append(sig))
    monkeypatch.setattr("general_ludd.security.orphan_pid.time.sleep", lambda _seconds: None)

    assert orphan.reap_orphan_tree(_record()) is expected
    assert sent == expected_signals


@pytest.mark.parametrize(
    ("failure", "expected"),
    [(None, True), (PermissionError(), True), (ProcessLookupError(), False)],
)
def test_pid_exists_distinguishes_permission_from_absence(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException | None,
    expected: bool,
) -> None:
    def probe(_pid: int, _signal: int) -> None:
        if failure is not None:
            raise failure

    monkeypatch.setattr("general_ludd.security.orphan_pid.os.kill", probe)
    assert orphan._pid_exists(1234) is expected


@pytest.mark.parametrize(("target_uid", "expected"), [(-1, True), (501, True), (502, False)])
def test_reaper_safety_uses_observed_owner(
    monkeypatch: pytest.MonkeyPatch,
    target_uid: int,
    expected: bool,
) -> None:
    monkeypatch.setattr("general_ludd.security.orphan_pid.os.getuid", lambda: 501)
    monkeypatch.setattr(orphan, "_read_uid_for_pid", lambda _pid: target_uid)
    assert orphan.is_reaper_safe(pid=1234, owner_uid=999) is expected


def test_compute_boot_id_linux_sysctl_and_fallback_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orphan, "_BOOT_ID_CACHE", None)
    monkeypatch.setattr(orphan, "_path_exists", lambda path: str(path).endswith("boot_id"))
    monkeypatch.setattr(orphan, "_path_read_text", lambda _path: " linux-boot \n")
    assert orphan.compute_boot_id() == "linux-boot"
    assert orphan.compute_boot_id() == "linux-boot"

    monkeypatch.setattr(orphan, "_BOOT_ID_CACHE", None)
    monkeypatch.setattr(
        orphan,
        "_path_read_text",
        lambda _path: (_ for _ in ()).throw(OSError("denied")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="sysctl-boot\n"),
        raising=False,
    )
    assert orphan.compute_boot_id() == "sysctl-boot"

    monkeypatch.setattr(orphan, "_BOOT_ID_CACHE", None)
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
        raising=False,
    )
    assert orphan.compute_boot_id().startswith("fallback-")

    monkeypatch.setattr(orphan, "_BOOT_ID_CACHE", None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
        raising=False,
    )
    assert orphan.compute_boot_id().startswith("fallback-")


def test_executable_uid_and_start_time_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="n/usr/bin/python\n"),
            SimpleNamespace(returncode=0, stdout="501\n"),
            SimpleNamespace(returncode=0, stdout="Thu Aug 27 01:00:00 2026\n"),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr("general_ludd.security.orphan_pid.sys.platform", "linux")

    assert orphan._read_exe_for_pid(1234) == os.path.realpath("/usr/bin/python")
    assert orphan._read_uid_for_pid(1234) == 501
    assert orphan._read_start_time_for_pid(1234) is not None


def test_uid_and_start_time_fail_closed_without_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
        raising=False,
    )
    monkeypatch.setattr(
        orphan,
        "_stat_uid",
        lambda _path: (_ for _ in ()).throw(OSError("gone")),
    )
    assert orphan._read_uid_for_pid(1234) == -1
    assert orphan._read_start_time_for_pid(1234) is None


def test_start_time_rejects_malformed_ps_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful command with an unparseable timestamp remains fail closed."""
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="not-a-process-start-time\n",
        ),
    )

    assert orphan._read_start_time_for_pid(1234) is None


def test_proc_introspection_and_signal_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = tmp_path / "status"
    status.write_text("Name:\ttest\nUid:\t777\t777\n", encoding="utf-8")
    stat = tmp_path / "stat"
    stat.write_text("1 (cmd) S 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 100", encoding="utf-8")

    original_path = Path

    def fake_path(value: str | PathLike[str]) -> Path:
        text = str(value)
        if text.endswith("/status"):
            return status
        if text.endswith("/stat"):
            return stat
        return original_path(value)

    monkeypatch.setattr("general_ludd.security.orphan_pid.Path", fake_path)
    monkeypatch.setattr(orphan, "_ticks_to_epoch", lambda ticks: float(ticks))
    assert orphan._read_uid_for_pid(1234) == 777
    assert orphan._read_start_time_for_pid(1234) == 100.0

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(orphan, "_child_pids", lambda _pid: [8, 9])
    monkeypatch.setattr(
        "general_ludd.security.orphan_pid.os.kill",
        lambda pid, sig: sent.append((pid, sig)),
    )
    orphan._send_signal_tree(7, signal.SIGTERM)
    assert sent == [(7, signal.SIGTERM), (8, signal.SIGTERM), (9, signal.SIGTERM)]


def test_boot_epoch_and_child_process_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="{ sec=1234, usec=0 }\n"),
            SimpleNamespace(returncode=0, stdout="10\n11\n"),
            SimpleNamespace(returncode=1, stdout=""),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(responses))
    assert orphan._boot_time_epoch() == 1234.0
    assert orphan._child_pids(7) == [10, 11]
    assert orphan._child_pids(7) == []

    monkeypatch.setattr("general_ludd.security.orphan_pid.os.sysconf", lambda _name: 100)
    monkeypatch.setattr(orphan, "_boot_time_epoch", lambda: 1000.0)
    assert orphan._ticks_to_epoch(250) == 1002.5


def test_macos_executable_fallback_uses_absolute_ps_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    monkeypatch.setattr("general_ludd.security.orphan_pid.sys.platform", "darwin")
    monkeypatch.setattr("ctypes.util.find_library", lambda _name: None)
    responses = iter(
        [
            SimpleNamespace(returncode=1, stdout=""),
            SimpleNamespace(returncode=0, stdout="/usr/bin/python worker.py\n"),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(responses))
    assert orphan._read_exe_for_pid(1234) == os.path.realpath("/usr/bin/python")


def test_executable_and_child_fallbacks_tolerate_missing_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    monkeypatch.setattr("general_ludd.security.orphan_pid.sys.platform", "linux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
    )
    assert orphan._read_exe_for_pid(1234) == "/proc/1234/exe"
    assert orphan._child_pids(1234) == []


def test_boot_time_prefers_linux_uptime_and_handles_malformed_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uptime = tmp_path / "uptime"
    uptime.write_text("10.5 2.0\n", encoding="utf-8")
    original_path = Path

    def uptime_path(value: str | PathLike[str]) -> Path:
        if str(value) == "/proc/uptime":
            return uptime
        return original_path(value)

    monkeypatch.setattr("general_ludd.security.orphan_pid.Path", uptime_path)
    monkeypatch.setattr("general_ludd.security.orphan_pid.time.time", lambda: 100.0)
    assert orphan._boot_time_epoch() == 89.5

    uptime.write_text("not-a-number\n", encoding="utf-8")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert orphan._boot_time_epoch() == 0.0


def test_lsof_skips_non_paths_until_absolute_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    monkeypatch.setattr("general_ludd.security.orphan_pid.sys.platform", "linux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="p1234\nnrelative/tool\nn/opt/gludd/bin/python\n",
        ),
    )
    assert orphan._read_exe_for_pid(1234) == "/opt/gludd/bin/python"


@pytest.mark.parametrize(
    "ps_result",
    [
        SimpleNamespace(returncode=0, stdout="python worker.py\n"),
        SimpleNamespace(returncode=1, stdout=""),
    ],
)
def test_macos_ps_rejects_unbound_executable_observations(
    monkeypatch: pytest.MonkeyPatch,
    ps_result: SimpleNamespace,
) -> None:
    monkeypatch.setattr(orphan, "_path_exists", lambda _path: False)
    monkeypatch.setattr("general_ludd.security.orphan_pid.sys.platform", "darwin")
    monkeypatch.setattr("ctypes.util.find_library", lambda _name: None)
    responses = iter((SimpleNamespace(returncode=1, stdout=""), ps_result))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(responses))
    assert orphan._read_exe_for_pid(1234) == "/proc/1234/exe"


def test_proc_readers_skip_malformed_rows_before_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = tmp_path / "status"
    status.write_text("Name:\ttest\nUid:\n", encoding="utf-8")
    stat = tmp_path / "stat"
    stat.write_text("1 (cmd) S 0\n", encoding="utf-8")
    original_path = Path

    def fake_path(value: str | PathLike[str]) -> Path:
        text = str(value)
        if text.endswith("/status"):
            return status
        if text.endswith("/stat"):
            return stat
        return original_path(value)

    monkeypatch.setattr("general_ludd.security.orphan_pid.Path", fake_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    monkeypatch.setattr(orphan, "_stat_uid", lambda _path: 808)
    assert orphan._read_uid_for_pid(1234) == 808
    assert orphan._read_start_time_for_pid(1234) is None
