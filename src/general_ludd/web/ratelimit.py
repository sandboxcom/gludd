"""Per-host token-bucket rate limiter (monotonic clock).

Used by the Crawler to never hammer a single host: ``take`` refills lazily and
blocks (bounded) until a token is available.  No background threads.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    """A monotonic-clock token bucket.

    capacity = burst size; refill_rate = tokens added per second.  ``take`` blocks
    up to ``max_wait`` seconds for a token; returns the seconds it actually
    waited (or -1.0 if it gave up without a token).
    """

    def __init__(self, *, rate: float = 1.0, capacity: float | None = None) -> None:
        self._rate = max(1e-6, float(rate))
        self._capacity = float(capacity) if capacity is not None else max(1.0, self._rate)
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now

    def take(self, *, max_wait: float = 30.0, sleep: bool = True) -> float:
        """Consume one token, waiting (bounded) if necessary.

        Returns the seconds waited, or -1.0 if no token became available within
        ``max_wait``.  ``sleep=False`` makes it non-blocking (returns -1.0 when a
        token is not immediately available) — used in tests to avoid real sleeps.
        """
        deadline = time.monotonic() + max(0.0, max_wait)
        waited = 0.0
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                needed = (1.0 - self._tokens) / self._rate
            if not sleep:
                return -1.0
            now = time.monotonic()
            if now >= deadline:
                return -1.0
            nap = min(needed, deadline - now)
            if nap > 0:
                time.sleep(nap)
                waited += nap
