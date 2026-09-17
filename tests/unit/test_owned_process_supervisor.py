"""Behavioral proof for Gludd-owned blocking-process supervision."""

from __future__ import annotations

import multiprocessing
import threading
import time
from typing import Any, NoReturn

import pytest

from general_ludd.util import owned_process as owned_process_module
from general_ludd.util.owned_process import (
    OwnedProcessCancelled,
    OwnedProcessPolicy,
    OwnedProcessTerminationError,
    OwnedProcessTimeout,
    run_owned_process,
)


def _return_sum(left: int, right: int) -> int:
    return left + right


def _raise_failure() -> NoReturn:
    raise ValueError("bounded failure")


def _wedge() -> None:
    threading.Event().wait(60.0)


def _owned_children(name: str) -> list[multiprocessing.Process]:
    return [child for child in multiprocessing.active_children() if child.name == name]


def test_owned_process_returns_child_result_and_reaps_it() -> None:
    name = "gludd-test-owned-success"

    result = run_owned_process(
        _return_sum,
        (19, 23),
        policy=OwnedProcessPolicy(timeout_seconds=5.0),
        process_name=name,
    )

    assert result == 42
    assert _owned_children(name) == []


def test_owned_process_propagates_child_failure_and_reaps_it() -> None:
    name = "gludd-test-owned-failure"

    with pytest.raises(ValueError, match="bounded failure"):
        run_owned_process(
            _raise_failure,
            (),
            policy=OwnedProcessPolicy(timeout_seconds=5.0),
            process_name=name,
        )

    assert _owned_children(name) == []


def test_deliberately_wedged_child_is_terminated_joined_and_traced() -> None:
    name = "gludd-test-owned-wedge"
    events: list[str] = []
    started = time.monotonic()

    with pytest.raises(OwnedProcessTimeout, match="deadline exceeded"):
        run_owned_process(
            _wedge,
            (),
            policy=OwnedProcessPolicy(
                timeout_seconds=0.75,
                shutdown_grace_seconds=0.25,
                poll_interval_seconds=0.05,
                heartbeat_interval_seconds=0.1,
            ),
            process_name=name,
            event_sink=lambda event: events.append(event),
        )

    assert time.monotonic() - started < 3.0
    assert events[0] == f"OWNED_PROCESS_STARTED name={name}"
    assert any(event.startswith(f"OWNED_PROCESS_HEARTBEAT name={name} ") for event in events)
    assert events[-1] == f"OWNED_PROCESS_TIMED_OUT name={name}"
    assert _owned_children(name) == []


def test_internal_cancellation_terminates_exact_owned_child() -> None:
    name = "gludd-test-owned-cancel"
    cancel = threading.Event()
    cancel.set()
    events: list[str] = []

    with pytest.raises(OwnedProcessCancelled, match="cancellation requested"):
        run_owned_process(
            _wedge,
            (),
            policy=OwnedProcessPolicy(timeout_seconds=5.0),
            process_name=name,
            cancel_requested=cancel.is_set,
            event_sink=events.append,
        )

    assert events == [
        f"OWNED_PROCESS_STARTED name={name}",
        f"OWNED_PROCESS_CANCELLED name={name}",
    ]
    assert _owned_children(name) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", 0.0),
        ("shutdown_grace_seconds", -1.0),
        ("poll_interval_seconds", 0.0),
        ("heartbeat_interval_seconds", 0.0),
    ],
)
def test_policy_rejects_unbounded_or_nonpositive_limits(field: str, value: float) -> None:
    values = {
        "timeout_seconds": 5.0,
        "shutdown_grace_seconds": 0.25,
        "poll_interval_seconds": 0.05,
        "heartbeat_interval_seconds": 0.1,
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        OwnedProcessPolicy(**values)


@pytest.mark.parametrize("value", [True, "5", float("nan"), float("inf"), 86_401])
def test_policy_rejects_wrong_type_nonfinite_and_excessive_timeout(
    value: object,
) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        OwnedProcessPolicy(timeout_seconds=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("operation", "args", "cancel", "name", "message"),
    [
        (object(), (), None, "valid-name", "operation must be callable"),
        (_return_sum, [], None, "valid-name", "args must be a tuple"),
        (_return_sum, (1, 2), object(), "valid-name", "cancel_requested"),
        (_return_sum, (1, 2), None, "space is invalid", "process_name"),
        (_return_sum, (1, 2), None, "x" * 129, "process_name"),
    ],
)
def test_owned_process_rejects_invalid_contract_before_spawning(
    operation: Any,
    args: Any,
    cancel: Any,
    name: str,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        run_owned_process(
            operation,
            args,
            policy=OwnedProcessPolicy(timeout_seconds=1.0),
            process_name=name,
            cancel_requested=cancel,
        )


def test_event_sink_failure_does_not_hide_owned_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_sink(_event: str) -> None:
        raise RuntimeError("sink unavailable")

    assert (
        run_owned_process(
            _return_sum,
            (20, 22),
            policy=OwnedProcessPolicy(timeout_seconds=5.0),
            process_name="gludd-test-broken-sink",
            event_sink=broken_sink,
        )
        == 42
    )
    assert "event sink failed" in caplog.text.lower()


class _FakeProcess:
    def __init__(self, *, alive: bool, stop_after_joins: int | None = None) -> None:
        self._alive = alive
        self._stop_after_joins = stop_after_joins
        self.join_count = 0

    @property
    def pid(self) -> int:
        return 987_654

    def is_alive(self) -> bool:
        return self._alive

    def join(self, _timeout: float | None = None) -> None:
        self.join_count += 1
        if (
            self._stop_after_joins is not None
            and self.join_count >= self._stop_after_joins
        ):
            self._alive = False

    def close(self) -> None:
        return None


def test_termination_helper_handles_already_dead_and_term_resistant_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[object] = []
    monkeypatch.setattr(
        owned_process_module,
        "_signal_owned_group",
        lambda _process, signal: signals.append(signal),
    )
    dead = _FakeProcess(alive=False)
    resistant = _FakeProcess(alive=True, stop_after_joins=2)

    owned_process_module._terminate_and_join(dead, 0.01)
    owned_process_module._terminate_and_join(resistant, 0.01)

    assert dead.join_count == 1
    assert resistant.join_count == 2
    assert len(signals) == 2


def test_termination_helper_fails_closed_when_child_cannot_be_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        owned_process_module,
        "_signal_owned_group",
        lambda _process, _signal: None,
    )
    child = _FakeProcess(alive=True)

    with pytest.raises(OwnedProcessTerminationError, match="confirmed stopped"):
        owned_process_module._terminate_and_join(child, 0.01)
