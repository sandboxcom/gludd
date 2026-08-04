"""Circuit breaker with closed/open/half-open states, sliding window, and half-open probe.

Thread-safe state machine:
  CLOSED → OPEN on reaching failure_threshold within window_seconds
  OPEN → HALF_OPEN after recovery_timeout seconds
  HALF_OPEN → CLOSED on a successful probe (single-flight admission)
  HALF_OPEN → OPEN on a failed probe (immediate re-arm)
"""

from __future__ import annotations

import enum
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


class State(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class _WindowEntry:
    timestamp: float
    kind: str


@dataclass
class BreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    window_seconds: float = 60.0
    half_open_max_probes: int = 1
    non_trip_kinds: frozenset[str] = field(default_factory=lambda: frozenset())


class CircuitBreakerStats:
    __slots__ = (
        "consecutive_failures",
        "current_state",
        "half_opened_at",
        "last_failure_time",
        "last_success_time",
        "opened_at",
        "state_transitions",
        "total_failures",
        "total_successes",
        "window_failure_count",
    )

    def __init__(self) -> None:
        self.total_failures: int = 0
        self.total_successes: int = 0
        self.state_transitions: int = 0
        self.last_failure_time: float | None = None
        self.last_success_time: float | None = None
        self.current_state: State = State.CLOSED
        self.window_failure_count: int = 0
        self.consecutive_failures: int = 0
        self.opened_at: float | None = None
        self.half_opened_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "state_transitions": self.state_transitions,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
            "current_state": self.current_state.value,
            "window_failure_count": self.window_failure_count,
            "consecutive_failures": self.consecutive_failures,
            "opened_at": self.opened_at,
            "half_opened_at": self.half_opened_at,
        }


class CircuitBreaker:
    def __init__(self, name: str, config: BreakerConfig | None = None) -> None:
        self._name = name
        self._config = config or BreakerConfig()
        self._state = State.CLOSED
        self._stats = CircuitBreakerStats()
        self._window: deque[_WindowEntry] = deque()
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_opened_at: float | None = None
        self._probe_admitted = False
        self._lock = threading.RLock()

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @property
    def stats(self) -> CircuitBreakerStats:
        with self._lock:
            self._prune_window()
            s = CircuitBreakerStats()
            s.total_failures = self._stats.total_failures
            s.total_successes = self._stats.total_successes
            s.state_transitions = self._stats.state_transitions
            s.last_failure_time = self._stats.last_failure_time
            s.last_success_time = self._stats.last_success_time
            s.current_state = self._state
            s.window_failure_count = len(self._window)
            s.consecutive_failures = self._consecutive_failures
            s.opened_at = self._opened_at
            s.half_opened_at = self._half_opened_at
            return s

    def _prune_window(self) -> None:
        cutoff = time.monotonic() - self._config.window_seconds
        while self._window and self._window[0].timestamp < cutoff:
            self._window.popleft()

    def _failures_in_window(self) -> int:
        self._prune_window()
        return len(self._window)

    def _transition_to(self, new_state: State) -> None:
        self._state = new_state
        self._stats.state_transitions += 1
        self._stats.current_state = new_state
        if new_state == State.OPEN:
            self._opened_at = time.monotonic()
            self._stats.opened_at = self._opened_at
            self._probe_admitted = False
        elif new_state == State.HALF_OPEN:
            self._half_opened_at = time.monotonic()
            self._stats.half_opened_at = self._half_opened_at
        elif new_state == State.CLOSED:
            self._opened_at = None
            self._half_opened_at = None
            self._stats.opened_at = None
            self._stats.half_opened_at = None

    def record_failure(self, kind: str = "unknown") -> bool:
        with self._lock:
            self._stats.total_failures += 1
            self._stats.last_failure_time = time.monotonic()

            if kind in self._config.non_trip_kinds:
                return self._state == State.OPEN

            now = time.monotonic()
            self._window.append(_WindowEntry(timestamp=now, kind=kind))
            self._prune_window()
            self._consecutive_failures += 1
            self._stats.consecutive_failures = self._consecutive_failures
            self._stats.window_failure_count = len(self._window)

            if self._state == State.HALF_OPEN:
                self._transition_to(State.OPEN)
                return True

            if self._state == State.CLOSED and self._failures_in_window() >= self._config.failure_threshold:
                self._transition_to(State.OPEN)
                return True

            return self._state == State.OPEN

    def record_success(self) -> None:
        with self._lock:
            self._stats.total_successes += 1
            self._stats.last_success_time = time.monotonic()
            self._consecutive_failures = 0
            self._stats.consecutive_failures = 0

            if self._state == State.HALF_OPEN:
                self._probe_admitted = False
                self._transition_to(State.CLOSED)

    def allow_request(self) -> bool:
        with self._lock:
            self._prune_window()
            self._stats.window_failure_count = len(self._window)

            if self._state == State.CLOSED:
                return True

            if self._state == State.OPEN:
                elapsed = time.monotonic() - (self._opened_at or 0.0)
                if elapsed >= self._config.recovery_timeout:
                    self._transition_to(State.HALF_OPEN)
                else:
                    self._stats.current_state = State.OPEN
                    return False

            if self._state == State.HALF_OPEN:
                if not self._probe_admitted:
                    self._probe_admitted = True
                    return True
                return False

            return False

    def reset(self) -> None:
        with self._lock:
            self._state = State.CLOSED
            self._stats = CircuitBreakerStats()
            self._window.clear()
            self._consecutive_failures = 0
            self._opened_at = None
            self._half_opened_at = None
            self._probe_admitted = False


class MultiBreaker:
    def __init__(self, config: BreakerConfig | None = None) -> None:
        self._config = config or BreakerConfig()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name,
                    BreakerConfig(
                        failure_threshold=self._config.failure_threshold,
                        recovery_timeout=self._config.recovery_timeout,
                        window_seconds=self._config.window_seconds,
                        half_open_max_probes=self._config.half_open_max_probes,
                        non_trip_kinds=self._config.non_trip_kinds,
                    ),
                )
            return self._breakers[name]

    def all_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: cb.stats.to_dict() for name, cb in self._breakers.items()}

    def reset_all(self) -> None:
        with self._lock:
            for cb in self._breakers.values():
                cb.reset()
