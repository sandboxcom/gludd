"""Concurrency limiter: semaphore, adaptive, and queue-depth gate.

All three strategies share a common interface: ``acquire()`` / ``release()``
and an async-capable context-manager pattern.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final


class LimiterState(Enum):
    OPEN = auto()
    THROTTLED = auto()
    CLOSED = auto()


@dataclass
class LimiterStats:
    acquired: int = 0
    released: int = 0
    rejected: int = 0
    wait_time_total: float = 0.0
    last_acquired_at: float | None = None

    @property
    def active(self) -> int:
        return self.acquired - self.released

    @property
    def avg_wait_ms(self) -> float:
        if self.acquired == 0:
            return 0.0
        return (self.wait_time_total / self.acquired) * 1000


class ConcurrencyLimiter:
    """Bounded semaphore with stats, state tracking, and optional timeout."""

    def __init__(self, max_concurrent: int, *, timeout: float | None = None) -> None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        self._sem: Final = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._stats = LimiterStats()
        self._state = LimiterState.OPEN
        self._max_concurrent = max_concurrent
        self._timeout = timeout

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def stats(self) -> LimiterStats:
        with self._lock:
            return LimiterStats(
                acquired=self._stats.acquired,
                released=self._stats.released,
                rejected=self._stats.rejected,
                wait_time_total=self._stats.wait_time_total,
                last_acquired_at=self._stats.last_acquired_at,
            )

    @property
    def active(self) -> int:
        return self._stats.active

    @property
    def state(self) -> LimiterState:
        with self._lock:
            return self._state

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    # ------------------------------------------------------------------
    # acquire / release
    # ------------------------------------------------------------------

    def acquire(self, *, timeout: float | None = None) -> bool:
        effective = timeout if timeout is not None else self._timeout
        if self._state == LimiterState.CLOSED:
            with self._lock:
                self._stats.rejected += 1
            return False

        t0 = time.monotonic()
        result = self._sem.acquire(blocking=True, timeout=effective)
        elapsed = time.monotonic() - t0

        with self._lock:
            if result:
                self._stats.acquired += 1
                self._stats.wait_time_total += elapsed
                self._stats.last_acquired_at = time.monotonic()
            else:
                self._stats.rejected += 1
        return result

    def release(self) -> None:
        with self._lock:
            self._stats.released += 1
        self._sem.release()

    def set_state(self, state: LimiterState) -> None:
        with self._lock:
            self._state = state

    def __enter__(self) -> ConcurrencyLimiter:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


class AdaptiveConcurrencyLimiter(ConcurrencyLimiter):
    """Concurrency limiter that adapts its limit based on latency feedback.

    Invariants
    ----------
    * **Floor / ceiling** — the limit never drops below ``min_concurrent``
      or rises above ``max_concurrent``.
    * **Additive-increase / multiplicative-decrease (AIMD)** — on success
      the limit increases by ``+1`` (additive); on failure / high latency
      it is halved (multiplicative).
    * **Hysteresis window** — adjustments only happen after
      ``cooldown_seconds`` since the last change to prevent oscillation.
    """

    def __init__(
        self,
        min_concurrent: int = 1,
        max_concurrent: int = 64,
        *,
        timeout: float | None = None,
        cooldown_seconds: float = 1.0,
        latency_threshold_ms: float = 500.0,
        success_threshold: int = 5,
        failure_threshold: int = 2,
    ) -> None:
        if min_concurrent < 1:
            raise ValueError(f"min_concurrent must be >= 1, got {min_concurrent}")
        if max_concurrent < min_concurrent:
            raise ValueError(f"max_concurrent ({max_concurrent}) must be >= min_concurrent ({min_concurrent})")
        super().__init__(max_concurrent, timeout=timeout)
        self._min = min_concurrent
        self._max = max_concurrent
        self._cooldown = cooldown_seconds
        self._latency_threshold = latency_threshold_ms
        self._success_threshold = success_threshold
        self._failure_threshold = failure_threshold
        self._success_streak: int = 0
        self._failure_streak: int = 0
        self._last_adjust: float = 0.0
        self._current_limit: int = max_concurrent

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def current_limit(self) -> int:
        with self._lock:
            return self._current_limit

    @property
    def min_concurrent(self) -> int:
        return self._min

    @property
    def latency_threshold_ms(self) -> float:
        return self._latency_threshold

    # ------------------------------------------------------------------
    # feedback loop
    # ------------------------------------------------------------------

    def report_success(self, *, latency_ms: float = 0.0) -> None:
        with self._lock:
            self._failure_streak = 0
            self._success_streak += 1
            if latency_ms > self._latency_threshold:
                self._failure_streak = 1
                self._success_streak = 0
                self._try_decrease()

        with self._lock:
            if self._success_streak >= self._success_threshold:
                self._try_increase()

    def report_failure(self) -> None:
        with self._lock:
            self._success_streak = 0
            self._failure_streak += 1
            if self._failure_streak >= self._failure_threshold:
                self._try_decrease()

    def _cooldown_elapsed(self) -> bool:
        return (time.monotonic() - self._last_adjust) >= self._cooldown

    def _try_increase(self) -> None:
        if not self._cooldown_elapsed():
            return
        if self._current_limit >= self._max:
            return
        self._current_limit = min(self._current_limit + 1, self._max)
        self._success_streak = 0
        self._last_adjust = time.monotonic()

    def _try_decrease(self) -> None:
        if not self._cooldown_elapsed():
            return
        new_limit = max(self._current_limit // 2, self._min)
        if new_limit >= self._current_limit:
            return
        self._current_limit = new_limit
        self._failure_streak = 0
        self._last_adjust = time.monotonic()


class QueueDepthGate:
    """Non-blocking admission gate keyed on pending-request depth.

    This is NOT a mutex / semaphore — it is a fast no-wait gate that
    lets callers decide *before* acquiring a concurrency slot whether
    the system is saturated, so they can shed load or fall back.
    """

    def __init__(
        self,
        max_depth: int,
        *,
        depth_source: Callable[[], int] | None = None,
    ) -> None:
        if max_depth < 1:
            raise ValueError(f"max_depth must be >= 1, got {max_depth}")
        self._max_depth = max_depth
        self._lock = threading.Lock()
        self._pending: deque[float] = deque()
        self._depth_source = depth_source
        self._stats = LimiterStats()

    @property
    def max_depth(self) -> int:
        return self._max_depth

    @property
    def stats(self) -> LimiterStats:
        with self._lock:
            return LimiterStats(
                acquired=self._stats.acquired,
                released=self._stats.released,
                rejected=self._stats.rejected,
                wait_time_total=self._stats.wait_time_total,
                last_acquired_at=self._stats.last_acquired_at,
            )

    @property
    def pending_count(self) -> int:
        with self._lock:
            self._prune_stale()
            return len(self._pending)

    @property
    def state(self) -> LimiterState:
        if self.pending_count >= self._max_depth:
            return LimiterState.CLOSED
        if self.pending_count >= self._max_depth * 0.8:
            return LimiterState.THROTTLED
        return LimiterState.OPEN

    def admit(self, *, ttl: float = 30.0) -> bool:
        now = time.monotonic()
        with self._lock:
            self._prune_stale(now=now)
            depth = len(self._pending) if self._depth_source is None else self._depth_source()
            if depth >= self._max_depth:
                self._stats.rejected += 1
                return False
            self._pending.append(now + ttl)
            self._stats.acquired += 1
            self._stats.last_acquired_at = now
            self._stats.wait_time_total += 0.0
            return True

    def release(self) -> None:
        with self._lock:
            self._stats.released += 1
            if self._pending:
                self._pending.popleft()

    def drain(self) -> int:
        with self._lock:
            count = len(self._pending)
            self._pending.clear()
            return count

    def _prune_stale(self, *, now: float | None = None) -> int:
        if now is None:
            now = time.monotonic()
        pruned = 0
        while self._pending and self._pending[0] < now:
            self._pending.popleft()
            pruned += 1
        return pruned


class AsyncConcurrencyLimiter:
    """asyncio-flavored semaphore with the same stats surface."""

    def __init__(self, max_concurrent: int) -> None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        self._sem = asyncio.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._stats = LimiterStats()
        self._max_concurrent = max_concurrent

    @property
    def stats(self) -> LimiterStats:
        with self._lock:
            return LimiterStats(
                acquired=self._stats.acquired,
                released=self._stats.released,
                rejected=self._stats.rejected,
                wait_time_total=self._stats.wait_time_total,
                last_acquired_at=self._stats.last_acquired_at,
            )

    @property
    def active(self) -> int:
        return self._stats.active

    async def acquire(self) -> None:
        t0 = time.monotonic()
        await self._sem.acquire()
        with self._lock:
            self._stats.acquired += 1
            self._stats.wait_time_total += time.monotonic() - t0
            self._stats.last_acquired_at = time.monotonic()

    def release(self) -> None:
        with self._lock:
            self._stats.released += 1
        self._sem.release()

    async def __aenter__(self) -> AsyncConcurrencyLimiter:
        await self.acquire()
        return self

    async def __aexit__(self, *args: object) -> None:
        self.release()
