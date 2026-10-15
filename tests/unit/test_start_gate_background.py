"""Behavioral tests for the session-isolated full-gate launcher."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO
from unittest.mock import Mock

import psutil
import pytest
from scripts import start_gate_background as launcher
from scripts.resource_arbiter import project_namespace
from scripts.start_gate_background import (
    GateIdentity,
    GatePaths,
    launch_gate,
    main,
    process_matches,
    watch_gate,
)

ROOT = Path(__file__).parent.parent.parent


def _sleep_command(seconds: float = 30.0) -> list[str]:
    return [
        sys.executable,
        "-c",
        f"import time; print('fake gate started', flush=True); time.sleep({seconds})",
    ]


def _write_after_trigger_command(trigger: Path) -> list[str]:
    script = (
        "import pathlib\n"
        "import time\n"
        f"trigger = pathlib.Path({str(trigger)!r})\n"
        "print('child-ready', flush=True)\n"
        "while not trigger.exists():\n"
        "    time.sleep(0.001)\n"
        "print('child-after-marker', flush=True)\n"
    )
    return [sys.executable, "-c", script]


def _wait_for_log_text(path: Path, expected: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and expected in path.read_text(encoding="utf-8"):
            return
        time.sleep(0.005)
    raise AssertionError(f"log did not contain {expected!r}")


def _stop(result: object) -> None:
    process = result.process  # type: ignore[attr-defined]
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)
    watchdog = result.watchdog_process  # type: ignore[attr-defined]
    if watchdog is not None:
        watchdog.wait(timeout=5)


def test_launch_publishes_exact_session_pid_state_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GLUDD_PROJECT_NAMESPACE", raising=False)
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    try:
        assert result.launched is True
        assert result.process is not None
        assert result.log_path is not None
        identity = result.identity
        assert identity is not None
        assert identity.pid == result.process.pid
        assert identity.process_group_id == identity.pid
        assert identity.session_id == identity.pid
        assert os.getpgid(identity.pid) == identity.pid
        assert os.getsid(identity.pid) == identity.pid
        assert process_matches(identity)

        paths = GatePaths.for_root(tmp_path)
        assert paths.pid_file.read_text(encoding="utf-8") == f"{identity.pid}\n"
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
        assert state["kind"] == "gludd_gate_background"
        assert state["state"] == "running"
        assert state["run_id"] == identity.run_id
        assert state["pid"] == identity.pid
        assert state["pid_started_at"] == identity.pid_started_at
        assert state["process_group_id"] == identity.pid
        assert state["session_id"] == identity.pid
        assert state["project_root"] == str(tmp_path.resolve())
        assert state["project_namespace"] == project_namespace(tmp_path)
        assert state["log_path"] == str(result.log_path)
        assert state["command"] == _sleep_command()
        assert state["timeout_seconds"] == 30
        assert result.log_path.exists()
        assert "namespace=" in result.log_path.read_text(encoding="utf-8")
    finally:
        _stop(result)


def test_live_duplicate_refuses_without_replacing_identity(tmp_path: Path) -> None:
    first = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert first.process is not None
    try:
        paths = GatePaths.for_root(tmp_path)
        original_state = paths.state_file.read_text(encoding="utf-8")
        duplicate = launch_gate(
            tmp_path,
            _sleep_command(),
            timeout_seconds=30,
            start_watcher=False,
        )
        assert duplicate.launched is False
        assert duplicate.process is None
        assert duplicate.identity == first.identity
        assert paths.state_file.read_text(encoding="utf-8") == original_state
        assert paths.pid_file.read_text(encoding="utf-8") == f"{first.process.pid}\n"
    finally:
        _stop(first)


def test_duplicate_refuses_when_live_identity_observation_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert first.identity is not None
    original_probe = launcher._probe_pid_started_at
    duplicate = None
    monkeypatch.setattr(
        launcher,
        "_probe_pid_started_at",
        lambda pid: (
            ("unknown", None)
            if pid == first.identity.pid
            else original_probe(pid)
        ),
    )
    try:
        duplicate = launch_gate(
            tmp_path,
            _sleep_command(),
            timeout_seconds=30,
            start_watcher=False,
        )

        assert duplicate.launched is False
        assert duplicate.process is None
        assert duplicate.identity == first.identity
        paths = GatePaths.for_root(tmp_path)
        assert paths.pid_file.read_text(encoding="utf-8") == f"{first.identity.pid}\n"
    finally:
        if duplicate is not None and duplicate.launched:
            _stop(duplicate)
        _stop(first)


def test_watcher_records_normal_finish_and_exits_with_gate(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(0.05),
        timeout_seconds=5,
        watcher_poll_seconds=0.01,
    )
    assert result.process is not None
    assert result.watchdog_process is not None
    assert result.process.wait(timeout=5) == 0
    assert result.watchdog_process.wait(timeout=5) == 0

    paths = GatePaths.for_root(tmp_path)
    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    assert state["state"] == "finished"
    assert state["termination_reason"] == "gate-exited"
    assert state["watchdog_pid"] is None
    assert not paths.pid_file.exists()
    assert not process_matches(result.identity)


def test_timeout_terminates_exact_session_and_removes_owned_pid(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.process is not None
    assert result.identity is not None
    assert result.log_path is not None
    paths = GatePaths.for_root(tmp_path)

    outcome = watch_gate(
        result.identity,
        paths,
        timeout_seconds=0.03,
        poll_seconds=0.005,
        grace_seconds=0.1,
    )
    result.process.wait(timeout=5)

    assert outcome == "timed_out"
    assert not process_matches(result.identity)
    assert not paths.pid_file.exists()
    assert "GATE_TIMEOUT" in paths.status_file.read_text(encoding="utf-8")
    assert "=== GATE: ABORTED (timeout 0.03s) ===" in result.log_path.read_text(
        encoding="utf-8"
    )
    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    assert state["state"] == "timed_out"
    assert state["termination_reason"] == "gate-timeout"


def test_timeout_retains_ownership_when_session_cannot_be_terminated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trigger = tmp_path / "release-child-output"
    result = launch_gate(
        tmp_path,
        _write_after_trigger_command(trigger),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.process is not None
    assert result.identity is not None
    assert result.log_path is not None
    paths = GatePaths.for_root(tmp_path)
    _wait_for_log_text(result.log_path, "child-ready")
    monkeypatch.setattr(launcher, "_signal_session", lambda *_args: False)

    try:
        outcome = watch_gate(
            result.identity,
            paths,
            timeout_seconds=0.01,
            poll_seconds=0.001,
            grace_seconds=0.01,
        )

        assert outcome == "termination_failed"
        assert process_matches(result.identity)
        assert paths.pid_file.read_text(encoding="utf-8") == f"{result.identity.pid}\n"
        assert "GATE_TIMEOUT_TERMINATION_FAILED" in paths.status_file.read_text(
            encoding="utf-8"
        )
        trigger.write_text("release\n", encoding="utf-8")
        assert result.process.wait(timeout=5) == 0
        log_text = result.log_path.read_text(encoding="utf-8")
        marker = "=== GATE: TERMINATION FAILED (timeout 0.01s) ==="
        assert marker in log_text
        assert log_text.index(marker) < log_text.index("child-after-marker")
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
        assert state["state"] == "termination_failed"
        assert state["termination_reason"] == "gate-timeout-termination-failed"
        assert state["watchdog_pid"] is None
    finally:
        _stop(result)


def test_identity_mismatch_never_signals_reused_pid(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.process is not None
    assert result.identity is not None
    mismatched = replace(result.identity, pid_started_at="not-the-live-start-token")
    try:
        outcome = watch_gate(
            mismatched,
            GatePaths.for_root(tmp_path),
            timeout_seconds=0.01,
            poll_seconds=0.001,
            grace_seconds=0.01,
        )
        assert outcome == "superseded"
        assert result.process.poll() is None
        assert process_matches(result.identity)
    finally:
        _stop(result)


def test_unavailable_process_identity_never_retires_a_live_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.identity is not None
    assert result.process is not None
    try:
        monkeypatch.setattr(
            launcher,
            "_probe_pid_started_at",
            lambda _pid: ("unknown", None),
        )

        assert launcher._watch_outcome_when_not_owned(result.identity) is None
        assert result.process.poll() is None
    finally:
        _stop(result)


def _mock_process_statuses(
    monkeypatch: pytest.MonkeyPatch,
    statuses: dict[int, str | Exception],
) -> GateIdentity:
    """Model unreaped processes without stealing their parent's exit status."""
    identity = GateIdentity("unreaped-run", 4242, "stable-token", 4242, 4242)

    class StatusProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def status(self) -> str:
            value = statuses[self.pid]
            if isinstance(value, Exception):
                raise value
            return value

    monkeypatch.setattr(
        launcher, "_probe_pid_started_at", lambda _pid: ("present", "stable-token")
    )
    monkeypatch.setattr(os, "getpgid", lambda _pid: identity.process_group_id)
    monkeypatch.setattr(os, "getsid", lambda _pid: identity.session_id)
    monkeypatch.setattr(psutil, "Process", StatusProcess)
    monkeypatch.setattr(psutil, "pids", lambda: list(statuses))
    return identity


@pytest.mark.parametrize(
    "terminal_status", (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD, psutil.ZombieProcess(4242))
)
def test_unreaped_exact_gate_is_terminal_without_reaping(
    monkeypatch: pytest.MonkeyPatch, terminal_status: str | Exception
) -> None:
    identity = _mock_process_statuses(monkeypatch, {4242: terminal_status})

    def forbidden_waitpid(*_args: object) -> None:
        pytest.fail("the watcher must not consume the launcher's child exit status")

    monkeypatch.setattr(os, "waitpid", forbidden_waitpid)
    assert launcher._process_state(identity) == "gone"
    assert not process_matches(identity)
    assert not launcher._identity_may_be_live(identity)


@pytest.mark.parametrize("peer_status", (psutil.STATUS_SLEEPING, psutil.STATUS_STOPPED))
def test_unreaped_leader_keeps_live_group_descendant_owned(
    monkeypatch: pytest.MonkeyPatch, peer_status: str
) -> None:
    identity = _mock_process_statuses(
        monkeypatch, {4242: psutil.STATUS_ZOMBIE, 4243: peer_status}
    )
    assert launcher._process_state(identity) == "owned"
    assert launcher._identity_may_be_live(identity)


@pytest.mark.parametrize(
    "failure", (psutil.AccessDenied(4242), OSError("status unavailable"), "?")
)
@pytest.mark.parametrize("unavailable_pid", (4242, 4243))
def test_unavailable_gate_or_descendant_status_retains_uncertain_ownership(
    monkeypatch: pytest.MonkeyPatch, failure: str | Exception, unavailable_pid: int
) -> None:
    statuses: dict[int, str | Exception] = {
        4242: psutil.STATUS_ZOMBIE,
        4243: psutil.STATUS_ZOMBIE,
    }
    statuses[unavailable_pid] = failure
    identity = _mock_process_statuses(monkeypatch, statuses)
    assert launcher._process_state(identity) == "unknown"
    assert launcher._identity_may_be_live(identity)
    assert launcher._watch_outcome_when_not_owned(identity) is None


@pytest.mark.parametrize("mismatch", ("pid_started_at", "process_group_id", "session_id"))
def test_zombie_status_never_overrides_exact_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    identity = _mock_process_statuses(monkeypatch, {4242: psutil.STATUS_ZOMBIE})
    if mismatch == "pid_started_at":
        mismatched = replace(identity, pid_started_at="reused-token")
    elif mismatch == "process_group_id":
        mismatched = replace(identity, process_group_id=4244)
    else:
        mismatched = replace(identity, session_id=4244)
    assert launcher._process_state(mismatched) == "reused"
    assert not launcher._signal_session(mismatched, signal.SIGKILL)


@pytest.mark.parametrize("term_ignored", (False, True))
def test_terminal_zombie_verification_keeps_bounded_term_kill_contract(
    monkeypatch: pytest.MonkeyPatch, term_ignored: bool
) -> None:
    statuses: dict[int, str | Exception] = {4242: psutil.STATUS_SLEEPING}
    identity = _mock_process_statuses(monkeypatch, statuses)
    signals: list[signal.Signals] = []

    def signal_group(group: int, signum: signal.Signals) -> None:
        assert group == identity.process_group_id
        signals.append(signum)
        if signum == signal.SIGKILL or not term_ignored:
            statuses[identity.pid] = psutil.STATUS_ZOMBIE

    monkeypatch.setattr(os, "killpg", signal_group)
    assert launcher._terminate_session(identity, grace_seconds=0.01, poll_seconds=0.001)
    assert signals == ([signal.SIGTERM, signal.SIGKILL] if term_ignored else [signal.SIGTERM])


def test_zombie_leader_does_not_hide_term_ignoring_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses: dict[int, str | Exception] = {
        4242: psutil.STATUS_SLEEPING,
        4243: psutil.STATUS_SLEEPING,
    }
    identity = _mock_process_statuses(monkeypatch, statuses)
    signals: list[signal.Signals] = []

    def signal_group(group: int, signum: signal.Signals) -> None:
        assert group == identity.process_group_id
        signals.append(signum)
        statuses[4242] = psutil.STATUS_ZOMBIE
        if signum == signal.SIGKILL:
            statuses[4243] = psutil.STATUS_ZOMBIE

    monkeypatch.setattr(os, "killpg", signal_group)
    assert launcher._terminate_session(identity, grace_seconds=0.01, poll_seconds=0.001)
    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize("foreign_identity", ("group", "session"))
def test_terminal_gate_never_adopts_a_foreign_group_or_session_peer(
    monkeypatch: pytest.MonkeyPatch, foreign_identity: str
) -> None:
    identity = _mock_process_statuses(
        monkeypatch, {4242: psutil.STATUS_ZOMBIE, 4243: psutil.STATUS_SLEEPING}
    )
    if foreign_identity == "group":
        monkeypatch.setattr(os, "getpgid", lambda pid: 4244 if pid == 4243 else 4242)
    else:
        monkeypatch.setattr(os, "getsid", lambda pid: 4244 if pid == 4243 else 4242)
    assert launcher._process_state(identity) == "gone"


def test_terminal_leader_retains_ownership_when_group_membership_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _mock_process_statuses(
        monkeypatch, {4242: psutil.STATUS_ZOMBIE, 4243: psutil.STATUS_SLEEPING}
    )

    def group_id(pid: int) -> int:
        if pid == 4243:
            raise PermissionError("group identity unavailable")
        return identity.process_group_id

    monkeypatch.setattr(os, "getpgid", group_id)
    assert launcher._process_state(identity) == "unknown"


@pytest.mark.parametrize("identity_call", ("getpgid", "getsid"))
def test_unavailable_exact_group_or_session_never_claims_gate_exit(
    monkeypatch: pytest.MonkeyPatch, identity_call: str
) -> None:
    identity = _mock_process_statuses(monkeypatch, {4242: psutil.STATUS_SLEEPING})

    def unavailable(_pid: int) -> int:
        raise PermissionError("exact process identity unavailable")

    monkeypatch.setattr(os, identity_call, unavailable)
    assert launcher._process_state(identity) == "unknown"
    assert not launcher._signal_session(identity, signal.SIGTERM)


@pytest.mark.parametrize("missing_snapshot", (False, True))
def test_terminal_leader_requires_a_complete_process_snapshot(
    monkeypatch: pytest.MonkeyPatch, missing_snapshot: bool
) -> None:
    identity = _mock_process_statuses(monkeypatch, {4242: psutil.STATUS_ZOMBIE})

    def process_ids() -> list[int]:
        if missing_snapshot:
            return []
        raise OSError("process enumeration unavailable")

    monkeypatch.setattr(psutil, "pids", process_ids)
    assert launcher._process_state(identity) == "unknown"


def test_real_unreaped_gate_is_terminal_before_parent_waits(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path, _sleep_command(), timeout_seconds=30, start_watcher=False
    )
    assert result.identity is not None
    assert result.process is not None
    try:
        os.killpg(result.identity.process_group_id, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if psutil.Process(result.identity.pid).status() == psutil.STATUS_ZOMBIE:
                break
            time.sleep(0.001)
        else:
            pytest.fail("terminated child did not enter its unreaped terminal state")

        assert launcher._process_state(result.identity) == "gone"
        assert result.process.returncode is None
        assert result.process.wait(timeout=5) == -signal.SIGTERM
    finally:
        _stop(result)


def test_validate_only_is_side_effect_free(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("gate:\n\t@true\n", encoding="utf-8")

    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--make-command",
                "make",
                "--makefile",
                str(makefile),
                "--timeout-seconds",
                "30",
                "--validate-only",
                "1",
            ]
        )
        == 0
    )
    assert "VALIDATE" in capsys.readouterr().out
    paths = GatePaths.for_root(tmp_path)
    assert not paths.pid_file.exists()
    assert not paths.state_file.exists()
    assert not paths.log_dir.exists()


def test_invalid_timeout_fails_without_side_effects(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("gate:\n\t@true\n", encoding="utf-8")

    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--make-command",
                "make",
                "--makefile",
                str(makefile),
                "--timeout-seconds",
                "0",
                "--validate-only",
                "1",
            ]
        )
        == 2
    )
    assert not GatePaths.for_root(tmp_path).log_dir.exists()


def test_malformed_receipts_and_process_identities_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = GatePaths.for_root(tmp_path)
    assert launcher._read_json(paths.state_file) is None
    paths.log_dir.mkdir(parents=True)
    paths.state_file.write_text("{broken", encoding="utf-8")
    assert launcher._read_json(paths.state_file) is None
    paths.state_file.write_text("[]", encoding="utf-8")
    assert launcher._read_json(paths.state_file) is None
    paths.pid_file.write_text("not-a-pid", encoding="utf-8")
    assert launcher._read_pid(paths.pid_file) is None
    assert launcher._pid_started_at(1) is None
    assert launcher._capture_identity(1, "run") is None
    assert launcher._json_int(None) is None
    assert launcher._json_int("invalid") is None

    valid: dict[str, object] = {
        "schema_version": 1,
        "kind": "gludd_gate_background",
        "run_id": "run",
        "pid": "2",
        "pid_started_at": "token",
        "process_group_id": "2",
        "session_id": "2",
    }
    assert launcher._identity_from_state(valid) == GateIdentity("run", 2, "token", 2, 2)
    assert launcher._identity_from_state({**valid, "run_id": None}) is None
    assert launcher._identity_from_state({**valid, "schema_version": 99}) is None
    assert launcher._identity_from_state({**valid, "pid": object()}) is None
    assert launcher._identity_from_state({**valid, "pid": 1}) is None

    current = launcher._capture_identity(os.getpid(), "self")
    assert current is not None
    assert launcher._process_state(
        replace(current, process_group_id=current.process_group_id + 1)
    ) == "reused"

    def fail_ps(*_args: object, **_kwargs: object) -> object:
        raise OSError("ps unavailable")

    monkeypatch.setattr(subprocess, "run", fail_ps)
    assert launcher._pid_started_at(os.getpid()) is None


def test_process_probe_and_capture_fail_closed_on_unavailable_os_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unexpected = subprocess.CompletedProcess(
        args=["/bin/ps"],
        returncode=2,
        stdout="",
        stderr="ps failed",
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: unexpected)

    assert launcher._probe_pid_started_at(os.getpid()) == ("unknown", None)
    assert launcher._pid_started_at(os.getpid()) is None

    monkeypatch.setattr(launcher, "_pid_started_at", lambda _pid: "stable-token")

    def deny_process_group(_pid: int) -> int:
        raise PermissionError("identity unavailable")

    monkeypatch.setattr(os, "getpgid", deny_process_group)
    assert launcher._capture_identity(os.getpid(), "run") is None


def test_stale_receipt_helpers_never_mutate_or_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = GatePaths.for_root(tmp_path)
    identity = GateIdentity("run", 42, "token", 42, 42)

    assert launcher._merge_state(paths, identity, {"state": "finished"}) is False
    assert launcher._current_log_path(paths, identity) is None

    paths.log_dir.mkdir(parents=True)
    paths.state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "gludd_gate_background",
                "run_id": identity.run_id,
                "pid": identity.pid,
                "pid_started_at": identity.pid_started_at,
                "process_group_id": identity.process_group_id,
                "session_id": identity.session_id,
                "log_path": "",
            }
        ),
        encoding="utf-8",
    )
    assert launcher._current_log_path(paths, identity) is None

    monkeypatch.setattr(launcher, "_process_state", lambda _identity: "gone")
    assert launcher._signal_session(identity, signal.SIGTERM) is False

    monkeypatch.setattr(launcher, "_process_state", lambda _identity: "owned")

    def deny_signal(_process_group_id: int, _signum: signal.Signals) -> None:
        raise PermissionError("signal unavailable")

    monkeypatch.setattr(os, "killpg", deny_signal)
    assert launcher._signal_session(identity, signal.SIGTERM) is False

    monkeypatch.setattr(launcher, "_state_is_current", lambda *_args: True)
    monkeypatch.setattr(
        launcher, "_watch_outcome_when_not_owned", lambda _identity: "superseded"
    )
    assert launcher._retire_finished_gate(paths, identity) == "superseded"

    monkeypatch.setattr(
        launcher, "_watch_outcome_when_not_owned", lambda _identity: "finished"
    )
    monkeypatch.setattr(launcher, "_merge_state", lambda *_args: False)
    assert launcher._retire_finished_gate(paths, identity) == "finished"


@pytest.mark.parametrize(
    ("timeout_seconds", "poll_seconds", "grace_seconds"),
    ((0.0, 0.1, 0.1), (1.0, 0.0, 0.1), (1.0, 0.1, 0.0)),
)
def test_watcher_rejects_each_nonpositive_duration(
    tmp_path: Path,
    timeout_seconds: float,
    poll_seconds: float,
    grace_seconds: float,
) -> None:
    identity = GateIdentity("run", 2, "token", 2, 2)

    with pytest.raises(ValueError, match="watcher durations must be positive"):
        watch_gate(
            identity,
            GatePaths.for_root(tmp_path),
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            grace_seconds=grace_seconds,
        )


def test_receipt_log_path_must_stay_inside_gate_log_directory(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.identity is not None
    assert result.process is not None
    paths = GatePaths.for_root(tmp_path)
    try:
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
        state["log_path"] = str(tmp_path / "outside.log")
        paths.state_file.write_text(json.dumps(state) + "\n", encoding="utf-8")
        assert launcher._current_log_path(paths, result.identity) is None
        assert (
            watch_gate(
                result.identity,
                paths,
                timeout_seconds=0.01,
                poll_seconds=0.001,
                grace_seconds=0.01,
            )
            == "superseded"
        )
        assert result.process.poll() is None
    finally:
        _stop(result)


def test_live_gate_with_replaced_current_receipt_is_not_signalled(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.identity is not None
    assert result.process is not None
    paths = GatePaths.for_root(tmp_path)
    try:
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
        state["run_id"] = "replacement"
        paths.state_file.write_text(json.dumps(state) + "\n", encoding="utf-8")
        assert (
            watch_gate(
                result.identity,
                paths,
                timeout_seconds=1,
                poll_seconds=0.001,
                grace_seconds=0.01,
            )
            == "superseded"
        )
        assert result.process.poll() is None
    finally:
        _stop(result)


def test_timeout_escalates_to_sigkill_for_term_ignoring_gate(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ignore-term-ready', flush=True); time.sleep(30)",
    ]
    result = launch_gate(
        tmp_path,
        command,
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.identity is not None
    assert result.process is not None
    assert result.process is not None
    assert result.log_path is not None
    try:
        _wait_for_log_text(result.log_path, "ignore-term-ready")
        assert (
            watch_gate(
                result.identity,
                GatePaths.for_root(tmp_path),
                timeout_seconds=0.01,
                poll_seconds=0.001,
                grace_seconds=0.01,
            )
            == "timed_out"
        )
        assert result.process.wait(timeout=5) == -signal.SIGKILL
    finally:
        _stop(result)


def test_stale_pid_is_replaced_and_early_exit_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = GatePaths.for_root(tmp_path)
    paths.pid_file.write_text("999999\n", encoding="utf-8")
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    try:
        assert result.identity is not None
        assert paths.pid_file.read_text(encoding="utf-8") == f"{result.identity.pid}\n"
    finally:
        _stop(result)

    monkeypatch.setattr(launcher, "_capture_identity", lambda *_args: None)
    with pytest.raises(RuntimeError, match="before its process identity"):
        launch_gate(
            tmp_path / "early",
            [sys.executable, "-c", "raise SystemExit(0)"],
            timeout_seconds=30,
            start_watcher=False,
        )


@pytest.mark.parametrize("startup", ["native", "delayed", "term-ignoring"])
def test_capture_failure_aborts_and_reaps_only_its_unpublished_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, startup: str
) -> None:
    paths = GatePaths.for_root(tmp_path)
    paths.log_dir.mkdir()
    previous_state = b'{"run_id": "previous-run", "state": "finished"}\n'
    paths.state_file.write_bytes(previous_state)
    application_marker = tmp_path / "application-ran"
    release_startup = tmp_path / "release-startup"
    native_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []
    actions: list[str] = []
    waits: list[float | None] = []
    logs: list[Path] = []

    class RecordedChild(subprocess.Popen[bytes]):
        def terminate(self) -> None:
            actions.append("terminate")
            assert paths.state_file.read_bytes() == previous_state
            assert not paths.pid_file.exists()
            super().terminate()

        def kill(self) -> None:
            actions.append("kill")
            super().kill()

        def wait(self, timeout: float | None = None) -> int:
            actions.append("wait")
            waits.append(timeout)
            return super().wait(timeout=timeout)

    def spawn(
        arguments: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdout: BinaryIO,
        stderr: int,
        start_new_session: bool,
    ) -> subprocess.Popen[bytes]:
        logs.append(Path(stdout.name))
        if startup != "native":
            bootstrap = (
                "import os, signal, sys, time\n"
                + (
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    if startup == "term-ignoring"
                    else ""
                )
                + "print('unpublished-startup-held', flush=True)\n"
                "while not os.path.exists(sys.argv[1]):\n"
                "    time.sleep(0.005)\n"
                "os.execv(sys.argv[2], sys.argv[2:])\n"
            )
            arguments = [
                sys.executable,
                "-c",
                bootstrap,
                str(release_startup),
                *arguments,
            ]
        child = RecordedChild(
            arguments,
            cwd=cwd,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=start_new_session,
        )
        children.append(child)
        return child

    def missing_identity(_pid: int, _run_id: str) -> None:
        if startup != "native":
            _wait_for_log_text(logs[0], "unpublished-startup-held")

    group_signal = Mock(side_effect=AssertionError("unverified group signal"))
    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher, "_capture_identity", missing_identity)
    monkeypatch.setattr(os, "killpg", group_signal)
    try:
        with pytest.raises(RuntimeError, match="before its process identity"):
            launch_gate(
                tmp_path,
                [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(application_marker)!r}).touch()",
                ],
                timeout_seconds=30,
            )

        assert len(children) == 1
        child = children[0]
        expected_signal = signal.SIGKILL if startup == "term-ignoring" else signal.SIGTERM
        assert child.returncode == -expected_signal
        assert actions == (
            ["terminate", "wait", "kill", "wait"]
            if startup == "term-ignoring"
            else ["terminate", "wait"]
        )
        assert all(timeout is not None and 0 < timeout <= 1 for timeout in waits)
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
        assert paths.state_file.read_bytes() == previous_state
        assert not paths.pid_file.exists()
        assert not paths.status_file.exists()
        assert not application_marker.exists()
        assert not release_startup.exists()
        assert len(logs) == 1
        log = logs[0].read_text(encoding="utf-8")
        assert "reason=identity-capture-failed" in log
        assert f"pid={child.pid}" in log
        group_signal.assert_not_called()
    finally:
        for child in children:
            if child.poll() is None:
                native_popen.kill(child)
            native_popen.wait(child, timeout=5)


@pytest.mark.parametrize(
    ("terminate_error", "wait_results", "kill_error", "cleanup_failed"),
    [
        (ProcessLookupError("already exited"), [0], None, False),
        (PermissionError("TERM refused"), [subprocess.TimeoutExpired("gate", 0.5), 0], None, False),
        (None, [OSError("wait unavailable"), 0], None, False),
        (None, [subprocess.TimeoutExpired("gate", 0.5), 0], ProcessLookupError("exit race"), False),
        (
            None,
            [subprocess.TimeoutExpired("gate", 0.5), subprocess.TimeoutExpired("gate", 0.5)],
            PermissionError("KILL refused"),
            True,
        ),
        (
            None,
            [subprocess.TimeoutExpired("gate", 0.5), OSError("reap unavailable")],
            None,
            True,
        ),
    ],
    ids=["term-exit-race", "term-denied", "wait-error", "kill-exit-race", "kill-denied", "reap-error"],
)
def test_capture_failure_cleanup_errors_remain_bounded_and_unpublished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminate_error: OSError | None,
    wait_results: list[int | Exception],
    kill_error: OSError | None,
    cleanup_failed: bool,
) -> None:
    child = Mock(spec=subprocess.Popen)
    child.pid = 42000
    child.poll.return_value = None
    child.terminate.side_effect = terminate_error
    child.kill.side_effect = kill_error
    child.wait.side_effect = wait_results
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(launcher, "_capture_identity", lambda *_args: None)
    group_signal = Mock(side_effect=AssertionError("unverified group signal"))
    monkeypatch.setattr(os, "killpg", group_signal)

    with pytest.raises(RuntimeError, match="before its process identity") as failure:
        launch_gate(tmp_path, _sleep_command(), timeout_seconds=30)

    actions = [entry[0] for entry in child.method_calls if entry[0] != "poll"]
    assert actions == (
        ["terminate", "wait"]
        if len(wait_results) == 1
        else ["terminate", "wait", "kill", "wait"]
    )
    assert ("cleanup failed" in str(failure.value)) is cleanup_failed
    for call in child.wait.call_args_list:
        assert 0 < call.kwargs["timeout"] <= 1
    paths = GatePaths.for_root(tmp_path)
    assert not paths.pid_file.exists()
    assert not paths.state_file.exists()
    assert not paths.status_file.exists()
    log = next(paths.log_dir.glob("gate-*.log")).read_text(encoding="utf-8")
    assert "reason=identity-capture-failed" in log
    assert ("cleanup failed" in log) is cleanup_failed
    group_signal.assert_not_called()


def test_finished_gate_state_is_not_overwritten_by_old_watcher(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.identity is not None
    assert result.process is not None
    paths = GatePaths.for_root(tmp_path)
    newer = json.loads(paths.state_file.read_text(encoding="utf-8"))
    newer["run_id"] = "newer-run"
    paths.state_file.write_text(json.dumps(newer) + "\n", encoding="utf-8")
    os.killpg(result.process.pid, signal.SIGKILL)
    result.process.wait(timeout=5)

    assert (
        watch_gate(
            result.identity,
            paths,
            timeout_seconds=1,
            poll_seconds=0.001,
            grace_seconds=0.01,
        )
        == "finished"
    )
    assert json.loads(paths.state_file.read_text(encoding="utf-8"))["run_id"] == "newer-run"


def test_gate_process_is_not_left_running_if_watcher_start_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_watcher(*_args: object, **_kwargs: object) -> object:
        raise OSError("watcher unavailable")

    monkeypatch.setattr(launcher, "_start_watcher", fail_watcher)
    with pytest.raises(OSError, match="watcher unavailable"):
        launch_gate(tmp_path, _sleep_command(), timeout_seconds=30)

    paths = GatePaths.for_root(tmp_path)
    assert not paths.pid_file.exists()
    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    assert state["state"] == "launch_failed"
    assert state["termination_reason"] == "watcher-start-failed"


def test_gate_session_survives_caller_process_group_sigterm(tmp_path: Path) -> None:
    """A managed runner killing the caller group must not collect the gate."""
    receipt = tmp_path / "caller-receipt.json"
    source = "\n".join(
        (
            "import json, sys, time",
            "from pathlib import Path",
            "from scripts.start_gate_background import launch_gate",
            "root, receipt = Path(sys.argv[1]), Path(sys.argv[2])",
            "command = [sys.executable, '-c', 'import time; time.sleep(30)']",
            "result = launch_gate(root, command, timeout_seconds=30, start_watcher=False)",
            "identity = result.identity",
            "payload = {'pid': identity.pid, 'started': identity.pid_started_at}",
            "payload.update({'pgid': identity.process_group_id, 'sid': identity.session_id})",
            "receipt.write_text(json.dumps(payload), encoding='utf-8')",
            "time.sleep(30)",
        )
    )
    caller = subprocess.Popen(
        [sys.executable, "-c", source, str(tmp_path), str(receipt)],
        cwd=ROOT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not receipt.exists() and time.monotonic() < deadline:
        if caller.poll() is not None:
            break
        time.sleep(0.01)
    if not receipt.exists():
        if caller.poll() is None:
            caller.terminate()
        caller.wait(timeout=5)
        pytest.fail("caller exited without publishing the gate receipt")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    paths = GatePaths.for_root(tmp_path)
    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    launched_identity = replace(
        _identity_from_state_for_test(state),
        pid=int(payload["pid"]),
    )

    os.killpg(caller.pid, signal.SIGTERM)
    caller.wait(timeout=5)
    try:
        assert process_matches(launched_identity)
        assert os.getpgid(launched_identity.pid) == launched_identity.pid
        assert os.getsid(launched_identity.pid) == launched_identity.pid
    finally:
        os.killpg(launched_identity.process_group_id, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while process_matches(launched_identity) and time.monotonic() < deadline:
            time.sleep(0.01)


def _identity_from_state_for_test(state: dict[str, object]) -> GateIdentity:
    run_id = state["run_id"]
    pid = state["pid"]
    pid_started_at = state["pid_started_at"]
    process_group_id = state["process_group_id"]
    session_id = state["session_id"]
    assert isinstance(run_id, str)
    assert isinstance(pid, int) and not isinstance(pid, bool)
    assert isinstance(pid_started_at, str)
    assert isinstance(process_group_id, int) and not isinstance(process_group_id, bool)
    assert isinstance(session_id, int) and not isinstance(session_id, bool)
    return GateIdentity(
        run_id=run_id,
        pid=pid,
        pid_started_at=pid_started_at,
        process_group_id=process_group_id,
        session_id=session_id,
    )


def test_timeout_never_removes_a_replaced_pid_receipt(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.identity is not None
    assert result.process is not None
    paths = GatePaths.for_root(tmp_path)
    paths.pid_file.write_text("999999\n", encoding="utf-8")

    assert (
        watch_gate(
            result.identity,
            paths,
            timeout_seconds=0.01,
            poll_seconds=0.001,
            grace_seconds=0.05,
        )
        == "timed_out"
    )
    result.process.wait(timeout=5)
    assert paths.pid_file.read_text(encoding="utf-8") == "999999\n"


def test_cli_watch_and_launch_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("gate:\n\t@/bin/sleep 0.05\n", encoding="utf-8")
    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--watch-run-id",
                "missing",
                "--timeout-seconds",
                "1",
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--makefile",
                str(makefile),
                "--make-command",
                "'",
                "--validate-only",
                "1",
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--makefile",
                str(tmp_path / "absent"),
                "--validate-only",
                "1",
            ]
        )
        == 2
    )
    with pytest.raises(argparse.ArgumentTypeError):
        launcher._positive_float("not-a-number")

    def fail_launch(*_args: object, **_kwargs: object) -> object:
        raise OSError("launch unavailable")

    monkeypatch.setattr(launcher, "launch_gate", fail_launch)
    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--makefile",
                str(makefile),
                "--timeout-seconds",
                "1",
            ]
        )
        == 1
    )
    monkeypatch.setattr(launcher, "launch_gate", lambda *_args, **_kwargs: object())
    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--makefile",
                str(makefile),
                "--timeout-seconds",
                "1",
            ]
        )
        == 0
    )


def test_exec_gate_after_publication_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert launcher._exec_gate_after_publication([]) == 126

    published = tmp_path / "published.json"
    published.write_text(json.dumps({"run_id": "run-1"}), encoding="utf-8")

    def fail_exec(*_args: object, **_kwargs: object) -> None:
        raise OSError("exec unavailable")

    monkeypatch.setattr(os, "execvpe", fail_exec)
    assert (
        launcher._exec_gate_after_publication([str(published), "run-1", "gate"])
        == 126
    )

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    ticks = iter((0.0, 1.0, 6.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    assert (
        launcher._exec_gate_after_publication(
            [str(tmp_path / "missing.json"), "run-2", "gate"]
        )
        == 126
    )

    mismatched = tmp_path / "mismatched.json"
    mismatched.write_text(json.dumps({"run_id": "other-run"}), encoding="utf-8")
    ticks = iter((0.0, 1.0, 6.0))
    assert (
        launcher._exec_gate_after_publication([str(mismatched), "run-3", "gate"])
        == 126
    )


def test_healthy_wrapper_keeps_full_publication_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter((0.0, 4.99, 4.999))
    receipts = iter((None, {"run_id": "healthy-run"}))
    exec_gate = Mock()
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", Mock())
    monkeypatch.setattr(launcher, "_read_json", lambda _path: next(receipts))
    monkeypatch.setattr(os, "execvpe", exec_gate)

    launcher._exec_gate_after_publication(
        [str(tmp_path / "published.json"), "healthy-run", "gate", "--flag"]
    )

    exec_gate.assert_called_once_with("gate", ["gate", "--flag"], os.environ.copy())


def test_private_exec_dispatch_bypasses_public_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def private_exec(arguments: list[str]) -> int:
        captured.extend(arguments)
        return 23

    monkeypatch.setattr(launcher, "_exec_gate_after_publication", private_exec)
    assert (
        main(["--exec-gate-after-publication", "state", "run", "gate", "--flag"])
        == 23
    )
    assert captured == ["state", "run", "gate", "--flag"]


def test_cli_watch_mode_publishes_finished_result(tmp_path: Path) -> None:
    result = launch_gate(
        tmp_path,
        [sys.executable, "-c", "raise SystemExit(0)"],
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.process is not None
    assert result.identity is not None
    assert result.process.wait(timeout=5) == 0
    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--watch-run-id",
                result.identity.run_id,
                "--timeout-seconds",
                "1",
                "--watcher-poll-seconds",
                "0.01",
                "--termination-grace-seconds",
                "0.01",
            ]
        )
        == 0
    )


def test_cli_watch_mode_reports_unverified_termination_as_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = launch_gate(
        tmp_path,
        _sleep_command(),
        timeout_seconds=30,
        start_watcher=False,
    )
    assert result.identity is not None
    monkeypatch.setattr(
        launcher,
        "watch_gate",
        lambda *_args, **_kwargs: "termination_failed",
    )
    try:
        assert (
            main(
                [
                    "--project-root",
                    str(tmp_path),
                    "--watch-run-id",
                    result.identity.run_id,
                    "--timeout-seconds",
                    "1",
                ]
            )
            == 125
        )
    finally:
        _stop(result)
