"""TDD tests for B3.1.4: ``WriterSupervisor`` (WP-B2).

Pins the application-level supervisor that owns the writer subprocess
lifecycle:

  * start the writer on construction / ``start()``
  * poll ``is_alive()`` on a fixed cadence (default 5s)
  * on unexpected death, restart with bounded retry + exponential backoff
    (1s, 2s, 4s, 8s, ... capped at 60s)
  * after ``max_retries`` (default 5) attempts, escalate to
    ``PERMANENT_FAILURE`` and emit a critical event (no infinite spin loop)
  * every recovery is observable: emits ``SupervisorRecoveryEvent`` to the
    EventBus (No Unseen Events invariant)
  * clean shutdown (writer exit 0) does NOT trigger a restart
  * ``stop()`` is graceful and idempotent, safe to call during an in-flight
    restart

Satisfies beta.3.4 (self-healing pattern) — distinct from the
process-level ``scripts/agent_watchdog.py``.

These tests use a FAKE writer factory + FAKE writer instance so the
supervisor logic is exercised deterministically without spawning real
subprocesses (the real-subprocess integration is covered by
``test_writer_process.py``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event
from general_ludd.writer.supervisor import (
    SupervisorFailureEscalatedEvent,
    SupervisorRecoveryEvent,
    SupervisorState,
    WriterSupervisor,
)


# --------------------------------------------------------------------------- #
# Test fakes
# --------------------------------------------------------------------------- #
class FakeWriter:
    """Deterministic stand-in for ``WriterProcess``.

    Tracks call counts and lets the test simulate death / clean-exit / raise
    on the next ``start()``. ``is_alive()`` flips based on ``_alive``.
    """

    def __init__(self, *, alive_after_start: bool = True) -> None:
        self.start_calls: int = 0
        self.stop_calls: int = 0
        self._alive: bool = False
        self._alive_after_start: bool = alive_after_start
        # exit_code semantics: None = still running, 0 = clean exit,
        # non-zero / negative = crash. Tests manipulate this directly.
        self.exit_code: int | None = None
        # If set, ``start()`` raises this instead of starting.
        self.start_exception: Exception | None = None

    def start(self, timeout: float = 30.0) -> bool:
        self.start_calls += 1
        if self.start_exception is not None:
            raise self.start_exception
        self._alive = self._alive_after_start
        self.exit_code = None
        return True

    def stop(self, sigterm_timeout: float = 10.0) -> bool:
        self.stop_calls += 1
        self._alive = False
        return True

    def is_alive(self) -> bool:
        return self._alive

    def kill(self, exit_code: int = -9) -> None:
        """Test helper: simulate the writer dying unexpectedly."""
        self._alive = False
        self.exit_code = exit_code


class RecordingEventBus(EventBus):
    """EventBus that records every published event for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[Event] = []

    def publish(self, event: Event) -> int:  # type: ignore[override]
        self.published.append(event)
        return 1


def _make_factory(
    writer: FakeWriter,
) -> Callable[[], FakeWriter]:
    """Return a no-arg factory that always returns the same FakeWriter."""
    def factory() -> FakeWriter:
        return writer
    return factory


# --------------------------------------------------------------------------- #
# 1. start() starts the writer and transitions to RUNNING
# --------------------------------------------------------------------------- #
def test_supervisor_starts_writer_on_init() -> None:
    writer = FakeWriter()
    bus = RecordingEventBus()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=bus,
    )
    assert writer.start_calls == 0
    sup.start()
    assert writer.start_calls == 1
    assert sup.state is SupervisorState.RUNNING
    sup.stop()


# --------------------------------------------------------------------------- #
# 2. Unexpected death triggers a restart within a bounded window
# --------------------------------------------------------------------------- #
def test_supervisor_restarts_on_unexpected_death() -> None:
    writer = FakeWriter()
    bus = RecordingEventBus()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=bus,
        health_check_interval=0.05,
        base_backoff=0.0,
    )
    sup.start()
    assert writer.start_calls == 1

    # Kill the writer (crash, not clean exit).
    writer.kill(exit_code=-9)

    # Wait for the supervisor to detect + restart. health_check_interval=0.05s,
    # base_backoff=0.0s, so restart should land well under 5s.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and writer.start_calls < 2:
        time.sleep(0.02)

    assert writer.start_calls >= 2, "supervisor did not restart after death"
    assert sup.state is SupervisorState.RUNNING
    sup.stop()


# --------------------------------------------------------------------------- #
# 3. Successive restarts use exponential backoff (1s, 2s, 4s, 8s, cap 60s)
# --------------------------------------------------------------------------- #
def test_supervisor_uses_exponential_backoff() -> None:
    writer = FakeWriter()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=None,
        max_retries=5,
        base_backoff=0.01,
        max_backoff=0.08,
        health_check_interval=0.02,
    )
    # We test the backoff *sequence* computation directly. The supervisor
    # exposes ``_next_backoff(attempt)`` so the test can assert the curve
    # without waiting wall-clock seconds per attempt.
    # attempt 0 (first restart) -> base_backoff
    # attempt 1 -> base_backoff * 2
    # attempt 2 -> base_backoff * 4
    # ...
    # capped at max_backoff.
    base = 0.01
    cap = 0.08
    expected = [base, base * 2, base * 4, base * 8, cap]
    for attempt, want in enumerate(expected):
        got = sup._next_backoff(attempt)
        assert got == pytest.approx(want), (
            f"attempt {attempt}: expected backoff {want}, got {got}"
        )
    # Beyond the cap, backoff stays at max_backoff.
    assert sup._next_backoff(100) == pytest.approx(cap)


# --------------------------------------------------------------------------- #
# 4. After max_retries, escalate to PERMANENT_FAILURE + critical event
# --------------------------------------------------------------------------- #
def test_supervisor_escalates_after_max_retries() -> None:
    writer = FakeWriter()
    bus = RecordingEventBus()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=bus,
        max_retries=3,
        base_backoff=0.0,
        health_check_interval=0.02,
    )
    sup.start()

    # Crash the writer ``max_retries + 1`` times. Each cycle: kill -> wait for
    # restart -> repeat until the supervisor gives up.
    for _ in range(4):  # 3 retries then escalation on the 4th failure
        writer.kill(exit_code=-9)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if sup.state is SupervisorState.PERMANENT_FAILURE:
                break
            if writer.start_calls > 0 and writer.is_alive():
                # Restarted; loop to kill again.
                break
            time.sleep(0.01)
        if sup.state is SupervisorState.PERMANENT_FAILURE:
            break

    assert sup.state is SupervisorState.PERMANENT_FAILURE, (
        "supervisor should escalate after max_retries"
    )
    # The escalation MUST emit a critical event.
    escalated = [
        e for e in bus.published if isinstance(e, SupervisorFailureEscalatedEvent)
    ]
    assert len(escalated) >= 1, "permanent failure did not emit escalation event"
    sup.stop()


# --------------------------------------------------------------------------- #
# 5. Every recovery emits a SupervisorRecoveryEvent
# --------------------------------------------------------------------------- #
def test_supervisor_emits_event_on_each_recovery() -> None:
    writer = FakeWriter()
    bus = RecordingEventBus()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=bus,
        max_retries=5,
        base_backoff=0.0,
        health_check_interval=0.05,
    )
    sup.start()

    writer.kill(exit_code=-9)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not any(
        isinstance(event, SupervisorRecoveryEvent) for event in bus.published
    ):
        time.sleep(0.02)

    recoveries = [
        e for e in bus.published if isinstance(e, SupervisorRecoveryEvent)
    ]
    assert len(recoveries) >= 1, "recovery did not emit SupervisorRecoveryEvent"
    # The event must carry useful payload: attempt number, exit code, backoff.
    payload = recoveries[0].payload
    assert "attempt" in payload
    assert "exit_code" in payload
    sup.stop()


# --------------------------------------------------------------------------- #
# 6. Permanent failure emits SupervisorFailureEscalatedEvent
# --------------------------------------------------------------------------- #
def test_supervisor_emits_event_on_permanent_failure() -> None:
    writer = FakeWriter()
    bus = RecordingEventBus()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=bus,
        max_retries=1,
        base_backoff=0.0,
        health_check_interval=0.02,
    )
    sup.start()

    # Kill twice: first triggers a retry, second trips escalation.
    for _ in range(2):
        writer.kill(exit_code=-9)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if sup.state is SupervisorState.PERMANENT_FAILURE:
                break
            time.sleep(0.01)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not any(
        isinstance(event, SupervisorFailureEscalatedEvent)
        for event in bus.published
    ):
        time.sleep(0.01)

    escalated = [
        e for e in bus.published if isinstance(e, SupervisorFailureEscalatedEvent)
    ]
    assert len(escalated) == 1
    assert sup.state is SupervisorState.PERMANENT_FAILURE
    sup.stop()


# --------------------------------------------------------------------------- #
# 7. stop() is graceful: cancels health-check, stops writer
# --------------------------------------------------------------------------- #
def test_supervisor_stop_is_graceful() -> None:
    writer = FakeWriter()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=None,
        health_check_interval=0.05,
    )
    sup.start()
    assert writer.start_calls == 1
    assert writer.stop_calls == 0

    stopped = sup.stop()
    assert stopped is True
    assert writer.stop_calls >= 1
    assert sup.state is SupervisorState.STOPPED

    # Idempotent: calling stop() again is a no-op.
    assert sup.stop() is True
    assert writer.stop_calls == 1  # writer.stop() not called a second time


# --------------------------------------------------------------------------- #
# 8. Health-check polls is_alive() on the configured interval
# --------------------------------------------------------------------------- #
def test_supervisor_health_check_periodic() -> None:
    writer = FakeWriter()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=None,
        health_check_interval=0.05,
    )
    sup.start()

    # Wait long enough for several health-check ticks to fire.
    deadline = time.monotonic() + 0.5
    checks = 0
    # Patch is_alive to count invocations.
    original = writer.is_alive
    counter = {"n": 0}

    def counting_is_alive() -> bool:
        counter["n"] += 1
        return original()

    writer.is_alive = counting_is_alive  # type: ignore[assignment]
    while time.monotonic() < deadline:
        time.sleep(0.01)
    checks = counter["n"]

    sup.stop()
    # In 0.5s with a 0.05s interval, we expect >= 3 polls (allow slack).
    assert checks >= 3, f"health-check fired only {checks} times in 0.5s"


# --------------------------------------------------------------------------- #
# 9. stop() during an in-flight restart does not corrupt state
# --------------------------------------------------------------------------- #
def test_supervisor_concurrent_stop_during_restart_safe() -> None:
    writer = FakeWriter()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=None,
        max_retries=10,
        base_backoff=0.5,
        health_check_interval=0.02,
    )
    sup.start()

    # Kill to trigger a restart (which will be in the backoff window when we
    # call stop()).
    writer.kill(exit_code=-9)
    # Give the health-check a moment to notice the death.
    time.sleep(0.05)

    # stop() concurrently with the pending restart. Must not raise, must leave
    # the supervisor in STOPPED.
    errors: list[BaseException] = []

    def call_stop() -> None:
        try:
            sup.stop()
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=call_stop)
    t.start()
    t.join(timeout=5.0)

    assert not errors, f"stop() during restart raised: {errors}"
    assert sup.state is SupervisorState.STOPPED


# --------------------------------------------------------------------------- #
# 10. Clean shutdown (writer exit 0) does NOT trigger a restart
# --------------------------------------------------------------------------- #
def test_supervisor_does_not_restart_on_clean_shutdown() -> None:
    writer = FakeWriter()
    sup = WriterSupervisor(
        writer_process_factory=_make_factory(writer),
        event_bus=None,
        max_retries=5,
        base_backoff=0.0,
        health_check_interval=0.05,
    )
    sup.start()
    start_calls_after_init = writer.start_calls

    # Simulate a clean self-shutdown: writer exits 0.
    writer._alive = False
    writer.exit_code = 0

    # Wait long enough that a restart WOULD have happened if it were going to.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        time.sleep(0.02)

    assert writer.start_calls == start_calls_after_init, (
        "supervisor restarted after a clean exit — it should NOT"
    )
    sup.stop()
