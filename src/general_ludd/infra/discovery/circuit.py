"""Minimal per-provider circuit breaker (closed/open/half-open).

Kept self-contained (stdlib only) so the discovery package never adds a dep.
A breaker trips OPEN after ``failure_threshold`` consecutive failures; while
OPEN it rejects calls fast (``allow()`` -> False) until ``cooldown_s`` elapses,
then admits a single HALF-OPEN probe. A probe success closes the breaker; a
probe failure re-opens it for another cooldown.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class ProviderCircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock: Callable[[], float] = clock or time.monotonic
        self._consecutive = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """True if a call may proceed (closed, or a half-open probe slot)."""
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self._cooldown_s:
                return False
            # Cooldown elapsed: admit exactly one half-open probe.
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive += 1
            self._probe_in_flight = False
            if self._consecutive >= self._failure_threshold:
                self._opened_at = self._clock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            return self._clock() - self._opened_at < self._cooldown_s
