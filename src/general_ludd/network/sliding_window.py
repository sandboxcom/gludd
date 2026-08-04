"""Rate-limiting primitives: FixedWindow, SlidingLog, SmoothedRate."""

from __future__ import annotations

import bisect
import time
from collections.abc import Callable
from dataclasses import dataclass, field


def _default_clock() -> int:
    return time.monotonic_ns()


@dataclass
class FixedWindow:
    window_sec: float
    max_events: int
    _counter: int = 0
    _window_start: int = 0
    _clock: Callable[[], int] = field(default=_default_clock, repr=False)

    def __post_init__(self) -> None:
        self._window_start = self._clock()

    def count(self) -> int:
        self._advance()
        return self._counter

    def allow(self) -> bool:
        self._advance()
        if self._counter < self.max_events:
            self._counter += 1
            return True
        return False

    def _advance(self) -> None:
        now = self._clock()
        window_ns = int(self.window_sec * 1_000_000_000)
        if now - self._window_start >= window_ns:
            self._window_start = now
            self._counter = 0


@dataclass
class SlidingLog:
    window_sec: float
    max_events: int
    _log: list[int] = field(default_factory=list)
    _clock: Callable[[], int] = field(default=_default_clock, repr=False)

    def count(self) -> int:
        self._evict()
        return len(self._log)

    def allow(self) -> bool:
        self._evict()
        if len(self._log) < self.max_events:
            now = self._clock()
            bisect.insort(self._log, now)
            return True
        return False

    def _evict(self) -> None:
        self._log.sort()
        now = self._clock()
        cutoff = now - int(self.window_sec * 1_000_000_000)
        idx = bisect.bisect_left(self._log, cutoff)
        if idx > 0:
            self._log = self._log[idx:]


@dataclass
class SmoothedRate:
    alpha: float
    _rate: float = 0.0
    _last_ts: int = 0
    _initialized: bool = False
    _clock: Callable[[], int] = field(default=_default_clock, repr=False)

    def rate(self) -> float:
        return self._rate

    def observe(self, count: int) -> None:
        now = self._clock()
        if self._initialized:
            elapsed_ns = now - self._last_ts
            elapsed_sec = max(elapsed_ns, 1) / 1_000_000_000
            instant_rate = count / elapsed_sec
            self._rate = self.alpha * instant_rate + (1 - self.alpha) * self._rate
        else:
            self._rate = self.alpha * count + (1 - self.alpha) * self._rate
            self._initialized = True
        self._last_ts = now
