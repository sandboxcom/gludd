"""Live model failover chain.

When a primary model fails (429/5xx/timeout), retries with fallback
profiles using tenacity backoff. Records failover events for metrics.

Transitive-cascade behavior
----------------------------
A failover chain is an ordered list of profiles: ``[primary, f1, f2, ...]``.
When the active profile raises a retryable error:

1. The error is recorded as a failover event (``from=primary, to=f1``).
2. The request is retried on the next fallback profile (f1).
3. If f1 ALSO fails, the cascade continues **transitively**: the error on f1 is
   recorded (``from=f1, to=f2``), and the request moves to f2.
4. Each fallback profile may itself have configured ``fallback_profiles``,
   which are appended to the walk queue (cascade depth capped by
   ``max_fallback_depth`` in ``gateway.py``).
5. The walk repeats until either a profile succeeds or the chain is exhausted.
   An exhausted chain raises the last error encountered.
6. The chain is cycle-safe: a fallback that points back to an earlier profile
   (or to itself) is skipped via a visited set, preventing infinite loops.

The ``_walk_fallbacks`` method in ``gateway.py`` implements the actual
transitive walk; ``ModelFailoverChain`` is the passive data structure that
records the events and exposes the retry predicate (``should_retry``).

Thread safety
--------------
``record_failover`` is guarded by two mechanisms:
* **Bounded semaphore** — caps concurrent failover recording at
  ``max_concurrent_failovers`` (default 50).  Acquire waits up to
  ``_semaphore_timeout`` seconds; if saturated, the event is dropped
  (logged at WARNING) rather than blocking indefinitely.
* **Mutex lock** — ``_events_lock`` serialises writes to the event list
  AND reads via ``get_failover_events``, preventing torn reads.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_FAILOVERS = 50
_DEFAULT_SEMAPHORE_TIMEOUT = 5.0


class ModelFailoverChain:
    """Ordered model failover chain with bounded-concurrency event recording.

    Each recorded event captures the source profile, destination profile,
    error message, exception type, attempt ordinal, and wall-clock timestamp
    for full per-attempt observability.
    """

    def __init__(
        self,
        primary_profile: str,
        fallback_profiles: list[str] | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
        max_concurrent_failovers: int | None = None,
    ) -> None:
        self._primary = primary_profile
        self._fallbacks = list(fallback_profiles) if fallback_profiles else []
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._events_lock = threading.Lock()
        self._failover_events: list[dict[str, object]] = []
        self._attempt_counter: int = 0
        self._semaphore = threading.BoundedSemaphore(
            max_concurrent_failovers
            if max_concurrent_failovers is not None
            else _DEFAULT_MAX_CONCURRENT_FAILOVERS
        )
        self._semaphore_timeout = _DEFAULT_SEMAPHORE_TIMEOUT

    def get_chain(self) -> list[str]:
        return [self._primary, *self._fallbacks]

    def record_failover(
        self,
        from_profile: str,
        to_profile: str,
        error: str,
        *,
        exception_type: str | None = None,
    ) -> bool:
        """Record a failover transition from one profile to another.

        Thread-safe: acquires the bounded semaphore first (to cap concurrent
        recording), then the events mutex (to prevent torn writes).

        Args:
            from_profile: Profile that failed.
            to_profile:   Profile being failed over to.
            error:        Sanitised error message.
            exception_type: Qualified exception class name (e.g.
                            ``httpx.TimeoutException``).  If *None* the
                            recorded value is ``"unknown"``.

        Returns:
            ``True`` if the event was appended; ``False`` if it was dropped
            because the semaphore could not be acquired within the timeout
            (saturated under load).
        """
        acquired = self._semaphore.acquire(timeout=self._semaphore_timeout)
        if not acquired:
            logger.warning(
                "Failover event dropped (semaphore saturated, %d/%d): %s -> %s",
                self._semaphore._value,  # type: ignore[attr-defined]
                self._semaphore._initial_value,  # type: ignore[attr-defined]
                from_profile,
                to_profile,
            )
            return False
        try:
            self._attempt_counter += 1
            with self._events_lock:
                self._failover_events.append({
                    "from": from_profile,
                    "to": to_profile,
                    "error": error,
                    "attempt": self._attempt_counter,
                    "exception_type": exception_type or "unknown",
                    "timestamp": time.time(),
                })
            logger.warning(
                "Model failover: %s -> %s (%s) [attempt %d, type=%s]",
                from_profile,
                to_profile,
                error,
                self._attempt_counter,
                exception_type or "unknown",
            )
            return True
        finally:
            self._semaphore.release()

    def get_failover_events(self) -> list[dict[str, object]]:
        """Return a shallow copy of all recorded failover events.

        Acquires ``_events_lock`` so the returned snapshot is consistent
        (no torn read from a concurrent ``record_failover``).
        """
        with self._events_lock:
            return list(self._failover_events)

    def should_retry(self, error: Exception) -> bool:
        status = getattr(error, "status_code", getattr(error, "status", 0))
        if isinstance(status, int) and status in (429, 500, 502, 503, 504):
            return True
        error_str = str(error).lower()
        return any(
            keyword in error_str
            for keyword in ("timeout", "rate limit", "unavailable", "capacity")
        )
