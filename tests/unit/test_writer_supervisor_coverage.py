"""Branch coverage for the writer supervisor's fail-closed lifecycle edges."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event
from general_ludd.writer.supervisor import (
    SupervisorRecoveryEvent,
    SupervisorState,
    WriterSupervisor,
)


class _Writer:
    """Small deterministic writer double for direct lifecycle tests."""

    def __init__(self, *, alive: bool = True, raises_on_health: bool = False) -> None:
        self.alive = alive
        self.raises_on_health = raises_on_health
        self.exit_code: int | None = -9
        self.stop_calls = 0

    def start(self, timeout: float = 30.0) -> bool:
        del timeout
        self.alive = True
        return True

    def stop(self, sigterm_timeout: float = 10.0) -> bool:
        del sigterm_timeout
        self.stop_calls += 1
        self.alive = False
        return True

    def is_alive(self) -> bool:
        if self.raises_on_health:
            raise RuntimeError("health probe failed")
        return self.alive


class _LoopEvent:
    """Event-shaped sequence that deterministically bounds a health loop."""

    def __init__(self, is_set_values: list[bool]) -> None:
        self._is_set_values = iter(is_set_values)

    def is_set(self) -> bool:
        return next(self._is_set_values, True)

    def wait(self, timeout: float | None = None) -> bool:
        del timeout
        return False


class _RaisingBus(EventBus):
    """Event bus double that exposes the observable publication failure path."""

    def publish(self, event: Event) -> int:
        del event
        raise RuntimeError("bus unavailable")


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: WriterSupervisor(lambda: _Writer(), max_retries=-1), "max_retries"),
        (
            lambda: WriterSupervisor(
                lambda: _Writer(),
                health_check_interval=0.0,
            ),
            "health_check_interval",
        ),
        (lambda: WriterSupervisor(lambda: _Writer(), base_backoff=-1.0), "base_backoff"),
        (
            lambda: WriterSupervisor(
                lambda: _Writer(),
                base_backoff=2.0,
                max_backoff=1.0,
            ),
            "max_backoff",
        ),
    ],
)
def test_constructor_rejects_invalid_lifecycle_bounds(
    build: Callable[[], WriterSupervisor],
    message: str,
) -> None:
    """Invalid retry and timing bounds fail before any writer is acquired."""
    with pytest.raises(ValueError, match=message):
        build()


def test_start_guards_running_and_terminal_states() -> None:
    """A running supervisor is idempotent while terminal failure is immutable."""
    supervisor = WriterSupervisor(lambda: _Writer())
    supervisor._state = SupervisorState.RUNNING
    assert supervisor.start() is True

    supervisor._state = SupervisorState.PERMANENT_FAILURE
    with pytest.raises(RuntimeError, match="PERMANENT_FAILURE"):
        supervisor.start()


def test_stop_handles_running_state_without_acquired_writer() -> None:
    """Shutdown remains idempotent when startup failed before writer transfer."""
    supervisor = WriterSupervisor(lambda: _Writer())
    supervisor._state = SupervisorState.RUNNING
    supervisor._writer = None

    assert supervisor.stop() is True
    assert supervisor.state is SupervisorState.STOPPED


def test_health_loop_handles_terminal_empty_and_probe_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every non-happy health-loop branch exits or escalates deterministically."""
    stopped = WriterSupervisor(lambda: _Writer())
    stopped._stop_event.set()
    stopped._health_check_loop()

    wrong_state = WriterSupervisor(lambda: _Writer())
    wrong_state._state = SupervisorState.RESTARTING
    monkeypatch.setattr(wrong_state, "_stop_event", _LoopEvent([False]))
    wrong_state._health_check_loop()

    missing_writer = WriterSupervisor(lambda: _Writer())
    missing_writer._state = SupervisorState.RUNNING
    missing_writer._writer = None
    monkeypatch.setattr(missing_writer, "_stop_event", _LoopEvent([False, True]))
    missing_writer._health_check_loop()

    broken_probe = _Writer(raises_on_health=True)
    escalated = WriterSupervisor(lambda: broken_probe, max_retries=0)
    escalated._state = SupervisorState.RUNNING
    escalated._writer = broken_probe
    monkeypatch.setattr(escalated, "_stop_event", _LoopEvent([False]))
    escalated._health_check_loop()
    assert escalated.state is SupervisorState.PERMANENT_FAILURE


def test_exit_code_fallbacks_cover_real_and_unknown_writer_shapes() -> None:
    """Exit status supports the real Popen shape and unknown test doubles."""
    supervisor = WriterSupervisor(lambda: _Writer())
    real_shape = SimpleNamespace(_proc=SimpleNamespace(returncode=17))

    assert supervisor._read_exit_code(real_shape) == 17
    assert supervisor._read_exit_code(SimpleNamespace()) is None


def test_recovery_honors_each_shutdown_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery never acquires a writer after either shutdown observation."""
    stopped = WriterSupervisor(lambda: _Writer())
    assert stopped._recover(-9) is False

    signalled = WriterSupervisor(lambda: _Writer(), base_backoff=0.0)
    signalled._state = SupervisorState.RUNNING
    signalled._stop_event.set()
    assert signalled._recover(-9) is False

    changed_during_backoff = WriterSupervisor(lambda: _Writer(), base_backoff=0.0)
    changed_during_backoff._state = SupervisorState.RUNNING

    def stop_while_computing(attempt: int) -> float:
        del attempt
        changed_during_backoff._state = SupervisorState.STOPPED
        return 0.0

    monkeypatch.setattr(changed_during_backoff, "_next_backoff", stop_while_computing)
    assert changed_during_backoff._recover(-9) is False


def test_failed_restart_and_event_bus_failure_remain_observable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Acquisition and publication failures are logged without hiding state."""
    def failing_factory() -> _Writer:
        raise RuntimeError("writer acquisition failed")

    supervisor = WriterSupervisor(failing_factory, base_backoff=0.0)
    supervisor._state = SupervisorState.RUNNING
    assert supervisor._recover(-9) is True
    assert supervisor._writer is None
    assert supervisor.state is SupervisorState.RESTARTING

    without_bus = WriterSupervisor(lambda: _Writer())
    without_bus._emit(SupervisorRecoveryEvent(0, -9, 0.0))

    with_failing_bus = WriterSupervisor(
        lambda: _Writer(),
        event_bus=_RaisingBus(),
    )
    with_failing_bus._emit(SupervisorRecoveryEvent(0, -9, 0.0))

    assert "writer acquisition failed" in caplog.text
    assert "EventBus.publish raised" in caplog.text
