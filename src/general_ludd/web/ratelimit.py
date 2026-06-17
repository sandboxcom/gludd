"""Per-host token-bucket rate limiting (monotonic clock, stdlib only).

A :class:`TokenBucket` refills at ``rate`` tokens/second up to a small burst;
:class:`HostRateLimiter` keeps one bucket per host so cross-host fetches run
concurrently while same-host fetches are spaced. ``acquire`` blocks (sleeps) just
long enough for a token, bounded by the bucket rate — never an unbounded wait.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    """A monotonic-clock token bucket: ``rate`` tokens/sec, capacity ``burst``."""

    def __init__(
        self,
        rate: float,
        burst: int = 1,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._rate = max(rate, 1e-6)
        self._capacity = float(max(burst, 1))
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now

    def try_acquire(self) -> bool:
        """Take one token without waiting; ``True`` if a token was available."""
        with self._lock:
            self._refill_locked()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def acquire(self) -> float:
        """Block until a token is available; return the seconds waited."""
        with self._lock:
            self._refill_locked()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            deficit = 1.0 - self._tokens
            wait = deficit / self._rate
        if wait > 0:
            self._sleep(wait)
        with self._lock:
            self._refill_locked()
            self._tokens = max(0.0, self._tokens - 1.0)
        return wait


class HostRateLimiter:
    """A per-host registry of token buckets — spaces same-host requests."""

    def __init__(
        self,
        rate: float,
        burst: int = 1,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._sleep = sleep
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _bucket(self, host: str) -> TokenBucket:
        with self._lock:
            b = self._buckets.get(host)
            if b is None:
                b = TokenBucket(
                    self._rate, self._burst, clock=self._clock, sleep=self._sleep
                )
                self._buckets[host] = b
            return b

    def acquire(self, host: str) -> float:
        """Block until the host's bucket yields a token; return seconds waited."""
        return self._bucket(host).acquire()

    def set_min_interval(self, host: str, seconds: float) -> None:
        """Honor a robots ``Crawl-delay`` by capping the host's effective rate."""
        if seconds <= 0:
            return
        rate = 1.0 / seconds
        with self._lock:
            self._buckets[host] = TokenBucket(
                rate, 1, clock=self._clock, sleep=self._sleep
            )
