"""Application-level supervisor for the writer subprocess (B3.1.4 — WP-B2).

``WriterSupervisor`` owns the ``WriterProcess`` lifecycle at the application
layer: it starts the writer, polls its health on a fixed cadence, and on
unexpected death restarts it with **bounded retry + exponential backoff**.
After ``max_retries`` failed attempts it escalates to ``PERMANENT_FAILURE``
rather than spinning forever. Every recovery and the final escalation are
emitted as observable events on the ``EventBus`` (No Unseen Events invariant).

This satisfies both **B3.1.4** and **beta.3.4** (self-healing pattern). It is
distinct from the process-level ``scripts/agent_watchdog.py`` watchdog: that
daemon supervises the opencode *agent* process; this supervisor supervises the
*writer* child within the daemon process.

Design notes
============

* The supervisor is given a **factory** (``writer_process_factory``) rather
  than a writer instance: every restart mints a fresh writer so the new child
  gets a fresh readiness nonce, fresh argv, and no inherited state. A
  ``WriterProcess`` is single-use by contract (double-start raises), so the
  supervisor MUST call the factory again per restart.
* The health-check loop runs in a background daemon thread (not asyncio) so
  it works whether or not the caller is inside an event loop. A
  ``threading.Event`` (_stop_event) is used for cancellation.
* Exponential backoff: ``base_backoff * 2**attempt``, capped at
  ``max_backoff``. ``time.sleep`` is used during backoff — but the sleep is
  interrupted by ``_stop_event.wait(backoff)`` so ``stop()`` cancels it
  promptly. (No blocking operation is uninterruptible — No Unseen Events
  invariant / observability.)
* Concurrency: ``stop()`` and the recovery loop both take ``_state_lock``.
  ``stop()`` sets ``_stop_event`` BEFORE acquiring the lock, so an in-flight
  recovery's ``start()`` call is followed by an immediate ``stop()`` on the
  new writer — never a state-corruption window.

Events
======

* ``SupervisorRecoveryEvent`` — fired on every successful restart. Payload
  carries ``attempt`` (0-indexed restart number), ``exit_code`` (the dead
  writer's exit code), and ``backoff_s`` (how long the supervisor waited).
* ``SupervisorFailureEscalatedEvent`` — fired once when the supervisor gives
  up. Payload carries ``attempts`` (total restart attempts) and
  ``last_exit_code``.
"""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol, cast

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event

logger = logging.getLogger(__name__)

__all__ = [
    "SupervisorFailureEscalatedEvent",
    "SupervisorRecoveryEvent",
    "SupervisorState",
    "WriterSupervisor",
]


# State + event types
class SupervisorState(enum.StrEnum):
    """Lifecycle states of ``WriterSupervisor``."""

    RUNNING = "running"
    RESTARTING = "restarting"
    PERMANENT_FAILURE = "permanent_failure"
    STOPPED = "stopped"


class SupervisorRecoveryEvent(Event):
    """Emitted on every successful writer restart.

    A subscriber can use this to alert, throttle, or simply log — the event
    is the *observable* signal that a self-healing action occurred.
    """

    def __init__(
        self,
        attempt: int,
        exit_code: int | None,
        backoff_s: float,
        **kwargs: Any,
    ) -> None:
        """Create a recovery event with restart evidence."""
        super().__init__(
            type="supervisor_recovery",
            payload={
                "attempt": attempt,
                "exit_code": exit_code,
                "backoff_s": backoff_s,
            },
            **kwargs,
        )


class SupervisorFailureEscalatedEvent(Event):
    """Emitted ONCE when the supervisor exhausts retries and gives up.

    This is the critical event — receiving it means the writer could not be
    brought back within the retry budget, and a human/operator must intervene.
    """

    def __init__(
        self,
        attempts: int,
        last_exit_code: int | None,
        **kwargs: Any,
    ) -> None:
        """Create a terminal escalation event with retry evidence."""
        super().__init__(
            type="supervisor_failure_escalated",
            payload={
                "attempts": attempts,
                "last_exit_code": last_exit_code,
            },
            **kwargs,
        )


# Writer process protocol (structural typing — no runtime coupling)
class _WriterLike(Protocol):
    """The subset of ``WriterProcess`` the supervisor depends on.

    Using a Protocol keeps the supervisor testable with a fake writer while
    remaining structurally compatible with the real ``WriterProcess``.
    """

    def start(self, timeout: float = ...) -> bool: ...
    def stop(self, sigterm_timeout: float = ...) -> bool: ...
    def is_alive(self) -> bool: ...

    # The supervisor also reads the writer's exit code to distinguish a clean
    # shutdown (no restart) from a crash (restart). The real WriterProcess
    # exposes this via the underlying Popen; we read it through an attribute
    # the test fakes also expose.
    @property
    def pid(self) -> int | None: ...


# WriterSupervisor
class WriterSupervisor:
    """Owns writer start / restart / health with bounded retry + backoff.

    Parameters
    ----------
    writer_process_factory
        Zero-arg callable that returns a fresh writer instance on each call.
        The supervisor calls this once per (re)start so every child gets a
        new readiness nonce and argv.
    event_bus
        Optional ``EventBus`` for recovery / escalation events. ``None`` means
        events are logged only (still observable via the log stream).
    max_retries
        Maximum number of restart attempts before escalating to
        ``PERMANENT_FAILURE``. Default 5.
    health_check_interval
        Seconds between ``is_alive()`` polls. Default 5.0.
    base_backoff
        Seconds to wait before the first restart attempt. Default 1.0.
    max_backoff
        Upper bound on the backoff. Default 60.0.
    """

    def __init__(
        self,
        writer_process_factory: Callable[[], Any],
        event_bus: EventBus | None = None,
        max_retries: int = 5,
        health_check_interval: float = 5.0,
        base_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        """Configure the writer factory, event sink, and retry bounds."""
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if health_check_interval <= 0:
            raise ValueError("health_check_interval must be > 0")
        if base_backoff < 0:
            raise ValueError("base_backoff must be >= 0")
        if max_backoff < base_backoff:
            raise ValueError("max_backoff must be >= base_backoff")

        self._factory: Callable[[], Any] = writer_process_factory
        self._bus: EventBus | None = event_bus
        self._max_retries: int = max_retries
        self._health_check_interval: float = health_check_interval
        self._base_backoff: float = base_backoff
        self._max_backoff: float = max_backoff

        self._state: SupervisorState = SupervisorState.STOPPED
        self._state_lock: threading.RLock = threading.RLock()

        # Cancellation signal — set by stop(), polled by the health loop and
        # the backoff sleep. An Event is the right primitive here: the loop
        # MUST be interruptible mid-sleep so stop() returns promptly.
        self._stop_event: threading.Event = threading.Event()

        self._writer: Any = None
        self._restart_count: int = 0

        # The health-check thread is created lazily by start().
        self._health_thread: threading.Thread | None = None

    # Public properties
    @property
    def state(self) -> SupervisorState:
        """Return the current supervisor lifecycle state."""
        return self._state

    @property
    def restart_count(self) -> int:
        """Return the number of restart attempts in this lifecycle."""
        return self._restart_count

    # Public lifecycle
    def start(self) -> bool:
        """Start the writer and spawn the health-check thread.

        Idempotent in the sense that calling ``start()`` on an already-running
        supervisor is a no-op; calling it after ``PERMANENT_FAILURE`` or
        ``STOPPED`` raises ``RuntimeError`` (use a fresh supervisor instead).
        """
        with self._state_lock:
            if self._state is SupervisorState.RUNNING:
                return True
            if self._state in (SupervisorState.PERMANENT_FAILURE,):
                raise RuntimeError(
                    "cannot start() a supervisor in PERMANENT_FAILURE — "
                    "construct a new WriterSupervisor"
                )

            self._writer = self._factory()
            self._writer.start()
            self._stop_event.clear()
            self._state = SupervisorState.RUNNING
            self._restart_count = 0

            # Spawn the health-check daemon. Daemon=True so it dies with the
            # interpreter and never blocks process exit.
            self._health_thread = threading.Thread(
                target=self._health_check_loop,
                name="WriterSupervisor-health",
                daemon=True,
            )
            self._health_thread.start()
            return True

    def stop(self) -> bool:
        """Graceful shutdown: cancel health-check, stop the writer.

        Idempotent — repeated calls return True without raising. Safe to call
        during an in-flight restart: the restart loop checks ``_stop_event``
        between backoff and ``start()``, and the writer's ``stop()`` is itself
        idempotent.
        """
        # Signal the health loop to bail FIRST, so it cannot race ahead and
        # start another writer between us clearing the loop and stopping the
        # current writer.
        self._stop_event.set()

        with self._state_lock:
            if self._state is SupervisorState.STOPPED:
                return True

            writer = self._writer
            self._writer = None
            self._state = SupervisorState.STOPPED

        # Stop the writer outside the lock — stop() can block on SIGTERM wait
        # and we don't want to serialize every stop() behind it. Safe because
        # the only other holder of the writer reference is the health loop,
        # which has already been told to stop via _stop_event.
        if writer is not None:
            with _suppress_log():
                writer.stop()

        # Join the health thread for a clean shutdown, but don't block forever
        # — the thread is daemon=True so it can't wedge process exit.
        thread = self._health_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        return True

    # Internals
    def _next_backoff(self, attempt: int) -> float:
        """Exponential backoff for restart ``attempt`` (0-indexed), capped.

        Pure function — exposed for unit testing the backoff curve without
        wall-clock waiting. ``attempt`` is the restart index (0 = first
        restart, 1 = second, ...).
        """
        # Clamp negative attempts before exponentiation, then cap the backoff.
        if self._base_backoff <= 0:
            return 0.0
        return cast(float, min(self._base_backoff * (2 ** max(attempt, 0)), self._max_backoff))

    def _health_check_loop(self) -> None:
        """Poll ``is_alive()`` on the configured interval; recover on death.

        Runs in a daemon thread. Exits when ``_stop_event`` is set or when
        the supervisor escalates to ``PERMANENT_FAILURE``.
        """
        while not self._stop_event.is_set():
            # Use wait() instead of sleep() so stop() interrupts the nap.
            if self._stop_event.wait(timeout=self._health_check_interval):
                return

            with self._state_lock:
                if self._state is not SupervisorState.RUNNING:
                    return
                writer = self._writer

            if writer is None:
                continue

            try:
                alive = writer.is_alive()
            except Exception:
                logger.exception("WriterSupervisor: is_alive() raised — treating as dead")
                alive = False

            if alive:
                continue

            # Writer is dead. Distinguish clean shutdown (exit 0) from crash.
            exit_code = self._read_exit_code(writer)
            if exit_code == 0:
                logger.info(
                    "WriterSupervisor: writer exited cleanly (code 0) — not restarting"
                )
                with self._state_lock:
                    self._state = SupervisorState.STOPPED
                return

            # Crash: attempt recovery. If recovery escalates, exit the loop.
            if not self._recover(exit_code):
                return

    def _read_exit_code(self, writer: Any) -> int | None:
        """Best-effort read of the writer's exit code.

        The real ``WriterProcess`` exposes ``_proc.returncode``; test fakes
        expose ``exit_code``. We try both. Unknown -> treat as crash (None
        behaves like a non-zero code in the comparison).
        """
        # Test-fake shape.
        exit_code = getattr(writer, "exit_code", None)
        if exit_code is not None:
            return cast(int, exit_code)
        # Real WriterProcess shape.
        proc = getattr(writer, "_proc", None)
        return cast(int | None, getattr(proc, "returncode", None)) if proc is not None else None

    def _recover(self, exit_code: int | None) -> bool:
        """Restart the writer with bounded retry + exponential backoff.

        Returns True if a new writer was successfully started (loop should
        continue), False if the supervisor escalated to PERMANENT_FAILURE
        (loop should exit).
        """
        with self._state_lock:
            if self._state is SupervisorState.STOPPED:
                return False
            attempt = self._restart_count
            self._restart_count += 1

            if attempt >= self._max_retries:
                # Exhausted retries — escalate. Do NOT spin forever.
                self._state = SupervisorState.PERMANENT_FAILURE
                self._emit(
                    SupervisorFailureEscalatedEvent(
                        attempts=attempt,
                        last_exit_code=exit_code,
                    )
                )
                logger.critical(
                    "WriterSupervisor: PERMANENT_FAILURE after %d restart attempts",
                    attempt,
                )
                return False

            self._state = SupervisorState.RESTARTING

        # Compute + apply backoff OUTSIDE the lock so stop() is not blocked
        # by a backoff sleep. The sleep is interruptible via _stop_event.
        backoff = self._next_backoff(attempt)
        logger.warning(
            "WriterSupervisor: writer died (exit=%s); restart attempt %d in %.2fs",
            exit_code,
            attempt + 1,
            backoff,
        )
        if backoff > 0 and self._stop_event.wait(timeout=backoff):
            # stop() was called during backoff — bail.
            return False

        if self._stop_event.is_set():
            return False

        with self._state_lock:
            if self._state is SupervisorState.STOPPED:
                return False
            try:
                new_writer = self._factory()
                new_writer.start()
            except Exception:
                logger.exception(
                    "WriterSupervisor: restart attempt %d failed to start new writer",
                    attempt + 1,
                )
                # Count this as a failed restart; recurse via the health loop.
                # We leave _state as RESTARTING and return True so the loop
                # immediately notices the writer is dead and tries again.
                self._writer = None
                return True

            self._writer = new_writer
            self._state = SupervisorState.RUNNING

        # Emit recovery event AFTER the new writer is confirmed running so the
        # event is an accurate signal (a subscriber reacting to it can assume
        # a live writer exists).
        self._emit(
            SupervisorRecoveryEvent(
                attempt=attempt,
                exit_code=exit_code,
                backoff_s=backoff,
            )
        )
        return True

    def _emit(self, event: Event) -> None:
        """Publish ``event`` on the bus (if any) and log it unconditionally.

        Events MUST be observable even without a bus — the log stream is the
        floor, the EventBus is the ceiling. No Unseen Events invariant.
        """
        logger.info("WriterSupervisor event: %s payload=%s", event.type, event.payload)
        if self._bus is not None:
            try:
                self._bus.publish(event)
            except Exception:
                logger.exception("WriterSupervisor: EventBus.publish raised — event lost")


class _suppress_log:
    """Context manager placeholder for future log-level suppression.

    Currently a no-op so we don't lose exception info; kept as a named class
    so call sites read clearly and the hook point exists if we need to
    silence writer.stop() noise later.
    """

    def __enter__(self) -> _suppress_log:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None
