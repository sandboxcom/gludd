"""Two-heap median, sliding window median, multi-stream median aggregator.

Pure-Python, stdlib only.  ``TwoHeapMedian`` maintains a running median via
a max-heap (lower half) and a min-heap (upper half).  ``SlidingWindowMedian``
extends this with lazy deletion for fixed-size window sliding.  ``MultiStreamMedian``
processes multiple independent streams with the same window size.
"""

from __future__ import annotations

import heapq
from collections import Counter, deque
from collections.abc import Iterable, Iterator


class TwoHeapMedian:
    """Running median via two heaps — add-only, no deletion.

    ``low`` is a max-heap (negated values in a min-heap) for the smaller half.
    ``high`` is a min-heap for the larger half.  Invariant: 0 <= len(low) - len(high) <= 1.
    """

    def __init__(self) -> None:
        """Initialize empty lower and upper heaps."""
        self._low: list[float] = []
        self._high: list[float] = []
        self._size: int = 0

    def add(self, value: float) -> None:
        """Add ``value`` to the running median."""
        if not self._low or value <= -self._low[0]:
            heapq.heappush(self._low, -value)
        else:
            heapq.heappush(self._high, value)

        if len(self._low) > len(self._high) + 1:
            heapq.heappush(self._high, -heapq.heappop(self._low))
        elif len(self._high) > len(self._low):
            heapq.heappush(self._low, -heapq.heappop(self._high))

        self._size += 1

    def find_median(self) -> float | None:
        """Return the current median, or ``None`` when empty."""
        if self._size == 0:
            return None
        if self._size % 2 == 1:
            return float(-self._low[0])
        return (-self._low[0] + self._high[0]) / 2.0

    @property
    def size(self) -> int:
        """Return the number of observed values."""
        return self._size

    def clear(self) -> None:
        """Remove every observed value."""
        self._low = []
        self._high = []
        self._size = 0


class SlidingWindowMedian:
    """Fixed-size sliding window median over a streaming input.

    Maintains two heaps with lazy deletion.  ``balance`` tracks
    ``effective_low_sz - effective_high_sz`` (always 0 or 1 after rebalance).
    """

    def __init__(self, window_size: int) -> None:
        """Initialize a median window containing at most ``window_size`` values."""
        self._k: int = window_size
        self._low: list[float] = []
        self._high: list[float] = []
        self._queue: deque[float] = deque()
        self._marked: Counter[float] = Counter()
        self._balance: int = 0
        self._eff_sz: int = 0

    # ------------------------------------------------------------------ helpers

    def _prune_low(self) -> None:
        while self._low and self._marked.get(-self._low[0], 0) > 0:
            v = -heapq.heappop(self._low)
            self._marked[v] -= 1
            if self._marked[v] == 0:
                del self._marked[v]

    def _prune_high(self) -> None:
        while self._high and self._marked.get(self._high[0], 0) > 0:
            v = heapq.heappop(self._high)
            self._marked[v] -= 1
            if self._marked[v] == 0:
                del self._marked[v]

    def _rebalance(self) -> None:
        self._prune_low()
        self._prune_high()
        while self._balance < 0:
            v = heapq.heappop(self._high)
            heapq.heappush(self._low, -v)
            self._balance += 2
            self._prune_high()
        while self._balance > 1:
            v = -heapq.heappop(self._low)
            heapq.heappush(self._high, v)
            self._balance -= 2
            self._prune_low()

    # ------------------------------------------------------------ public API

    @property
    def size(self) -> int:
        """Return the number of values in the current window."""
        return self._eff_sz

    def reset(self) -> None:
        """Clear the window and all lazy-deletion state."""
        self._low = []
        self._high = []
        self._queue = deque()
        self._marked = Counter()
        self._balance = 0
        self._eff_sz = 0

    def process(self, stream: Iterable[float]) -> Iterator[float | None]:
        """Yield each full-window median, using ``None`` during warm-up."""
        k = self._k
        for value in stream:
            self._queue.append(value)
            self._eff_sz += 1

            if not self._low or value <= -self._low[0]:
                heapq.heappush(self._low, -value)
                self._balance += 1
            else:
                heapq.heappush(self._high, value)
                self._balance -= 1

            if len(self._queue) > k:
                old = self._queue.popleft()
                self._eff_sz -= 1
                if old <= -self._low[0]:
                    self._balance -= 1
                else:
                    self._balance += 1
                self._marked[old] += 1
                self._prune_low()
                self._prune_high()

            self._rebalance()

            if len(self._queue) < k:
                yield None
            else:
                self._prune_low()
                self._prune_high()
                if self._balance == 0:
                    yield (-self._low[0] + self._high[0]) / 2.0
                else:
                    yield float(-self._low[0])


class MultiStreamMedian:
    """Process multiple named streams through independent sliding windows.

    ``process(**streams)`` returns a ``dict[str, list[float | None]]``
    with one entry per named stream.
    """

    def __init__(self, window_size: int) -> None:
        """Initialize independent windows of the requested size."""
        self._k: int = window_size
        self._streams: dict[str, SlidingWindowMedian] = {}

    def _ensure_stream(self, name: str) -> SlidingWindowMedian:
        if name not in self._streams:
            self._streams[name] = SlidingWindowMedian(self._k)
        return self._streams[name]

    def reset(self) -> None:
        """Clear every named stream window."""
        for sw in self._streams.values():
            sw.reset()

    def process(self, **streams: Iterable[float]) -> dict[str, list[float | None]]:
        """Return sliding medians for every named input stream."""
        result: dict[str, list[float | None]] = {}
        for name, stream in streams.items():
            sw = self._ensure_stream(name)
            result[name] = list(sw.process(stream))
        return result
