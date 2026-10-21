"""Additive native signal controls for the existing watchdog lifecycle owner."""

from __future__ import annotations

import importlib
import os
import signal
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import psutil
import pytest

from tests.unit.test_watchdog_secrets_lifecycle import (
    LABEL,
    ScanFixture,
    _live_owned,
    _record,
)
from tests.unit.test_watchdog_secrets_lifecycle import (
    watchdog_scan as watchdog_scan,
)


@pytest.mark.parametrize(
    ("watchdog_signal", "watchdog_signal_code"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_native_observer_shutdown_invalidates_then_propagates(
    watchdog_scan: ScanFixture, monkeypatch: pytest.MonkeyPatch,
    watchdog_signal: signal.Signals, watchdog_signal_code: int,
) -> None:
    fixture = watchdog_scan
    (fixture.root / "mode").write_text("timeout", encoding="utf-8")
    path = Path(fixture.runtime.SECRETS_VIOLATION_FILE)
    path.write_text('{"ts": 0, "violation": false}', encoding="utf-8")
    observed = importlib.import_module("scripts.stream_command")
    publisher = observed._StatusPublisher
    native_publish = cast(
        Callable[[object, Mapping[str, object]], None], publisher.publish,
    )

    def signal_running(owner: object, payload: Mapping[str, object]) -> None:
        native_publish(owner, payload)
        if payload["state"] == "running":
            deadline = time.monotonic() + 0.6
            while not (fixture.root / "owned.json").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            os.kill(os.getpid(), watchdog_signal)

    monkeypatch.setattr(publisher, "publish", signal_running)
    try:
        with pytest.raises(SystemExit) as stopped:
            fixture.module._check_secrets_committed(fixture.runtime)
        live_at_return = [process.pid for process in _live_owned(fixture.root)]
    finally:
        remaining = _live_owned(fixture.root)
        if remaining:
            _gone, still_running = psutil.wait_procs(remaining, timeout=4)
            assert not still_running
    assert stopped.value.code == watchdog_signal_code
    assert not live_at_return
    current = _record(fixture.root / ".gate-logs/observed" / LABEL / "current.json")
    assert current["state"] == "interrupted"
    assert current["exit_code"] == watchdog_signal_code
    assert current["termination_reason"] == f"observer-signal:{watchdog_signal.name}"
    assert _record(path)["violation"] is None
    assert _record(path)["reason"] == "interrupted"
    assert fixture.marks == ["secrets_scan"]
    assert fixture.legacy_calls == []


@pytest.mark.parametrize(
    ("watchdog_child_signal", "watchdog_child_code"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_native_child_signal_does_not_request_daemon_shutdown(
    watchdog_scan: ScanFixture, monkeypatch: pytest.MonkeyPatch,
    watchdog_child_signal: signal.Signals, watchdog_child_code: int,
) -> None:
    fixture = watchdog_scan
    observed = importlib.import_module("scripts.stream_command")
    publisher = observed._StatusPublisher
    native_publish = cast(
        Callable[[object, Mapping[str, object]], None], publisher.publish,
    )

    def signal_child(owner: object, payload: Mapping[str, object]) -> None:
        native_publish(owner, payload)
        if payload["state"] == "running":
            child_pid = payload["child_pid"]
            if type(child_pid) is not int or child_pid <= 1:
                raise RuntimeError("Native child PID is invalid")
            os.kill(child_pid, watchdog_child_signal)

    monkeypatch.setattr(publisher, "publish", signal_child)
    try:
        result: object = fixture.module._check_secrets_committed(fixture.runtime)
        live_at_return = [process.pid for process in _live_owned(fixture.root)]
    finally:
        remaining = _live_owned(fixture.root)
        if remaining:
            _gone, still_running = psutil.wait_procs(remaining, timeout=4)
            assert not still_running
    assert isinstance(result, dict)
    assert result["violation"] is True
    assert result["exit_code"] == watchdog_child_code
    assert not live_at_return
    current = _record(fixture.root / ".gate-logs/observed" / LABEL / "current.json")
    assert current["state"] == "interrupted"
    assert current["exit_code"] == watchdog_child_code
    assert current["termination_reason"] == f"child-signal:{watchdog_child_signal.name}"
    assert fixture.marks == ["secrets_scan"]
    assert fixture.legacy_calls == []
