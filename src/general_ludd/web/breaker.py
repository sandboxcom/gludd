"""Per-host circuit breaker for the web toolkit.

Keyed by ``host:port`` so a dead host trips fast instead of retrying forever.
Thread-safe (RLock), modelled on the semantics of
:class:`general_ludd.models.timeout_detector.ModelHealthTracker`
(failure_threshold + cooldown + a single half-open probe per cooldown window).

An instance is created PER :class:`~general_ludd.web.ssrf_client.SsrfSafeClient`
(per crawler / per caller), NOT a module singleton, so one abusive target cannot
trip a host for unrelated callers — the deliberate trade-off being no cross-call
protection unless an operator shares a client/breaker on purpose.
"""

from __future__ import annotations

import threading
import time


class HostCircuitBreaker:
    """A small thread-safe per-host breaker (closed / open / half-open)."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown = max(0.0, cooldown_seconds)
        self._consecutive: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._probe_in_flight: set[str] = set()
        self._lock = threading.RLock()

    @staticmethod
    def _key(host: str, port: int | None = None) -> str:
        return f"{host.strip().lower()}:{port if port is not None else ''}"

    def allow(self, host: str, port: int | None = None) -> bool:
        """Return True iff a request to ``host:port`` may proceed right now.

        Admits at most ONE half-open probe per cooldown window once the breaker
        is open; the breaker stays open until a :meth:`record_success` clears it
        or a :meth:`record_failure` re-arms it.
        """
        key = self._key(host, port)
        with self._lock:
            if self._consecutive.get(key, 0) < self._failure_threshold:
                return True
            opened = self._opened_at.get(key)
            if opened is None:
                return False
            if (time.monotonic() - opened) < self._cooldown:
                return False
            # Cooldown elapsed: admit exactly one half-open probe.
            if key in self._probe_in_flight:
                return False
            self._probe_in_flight.add(key)
            return True

    def is_open(self, host: str, port: int | None = None) -> bool:
        """Status poll: True iff the breaker would short-circuit (no probe slot consumed)."""
        key = self._key(host, port)
        with self._lock:
            if self._consecutive.get(key, 0) < self._failure_threshold:
                return False
            opened = self._opened_at.get(key)
            if opened is None:
                return True
            return (time.monotonic() - opened) < self._cooldown

    def record_success(self, host: str, port: int | None = None) -> None:
        key = self._key(host, port)
        with self._lock:
            self._consecutive[key] = 0
            self._opened_at.pop(key, None)
            self._probe_in_flight.discard(key)

    def record_failure(self, host: str, port: int | None = None) -> None:
        key = self._key(host, port)
        with self._lock:
            count = self._consecutive.get(key, 0) + 1
            self._consecutive[key] = count
            # A new failure re-arms the breaker and (re)starts the cooldown clock;
            # a failed half-open probe must not keep the gate claimed.
            self._probe_in_flight.discard(key)
            if count >= self._failure_threshold:
                self._opened_at[key] = time.monotonic()
