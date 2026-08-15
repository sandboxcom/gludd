"""Monotonic queue / deque — min-queue, max-queue, sliding window, priority.

Pure-Python, stdlib only.  A monotonic queue maintains elements in
monotonically increasing or decreasing order, giving O(1) amortised
access to the extremum at the front while supporting O(1) amortised
push and pop.

Classes
-------
MonotonicQueue[T]   - generic monotonic queue (min / max, configurable)
MinQueue[T]         - monotonic min-queue (front = minimum)
MaxQueue[T]         - monotonic max-queue (front = maximum)
SlidingWindow[T]    - sliding-window min / max aggregate
PriorityMonotonic[T] - monotonic priority queue (min or max)
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from typing import Generic, Protocol, TypeVar


class Comparable(Protocol):
    """Structural protocol for values usable by monotonic queues."""

    def __lt__(self, other: object, /) -> bool:
        """Return whether ``self`` sorts before ``other``."""
        ...

    def __gt__(self, other: object, /) -> bool:
        """Return whether ``self`` sorts after ``other``."""
        ...

    def __le__(self, other: object, /) -> bool:
        """Return whether ``self`` sorts at or before ``other``."""
        ...


T = TypeVar("T", bound=Comparable)


# ---------------------------------------------------------------------------
# Core monotonic queue
# ---------------------------------------------------------------------------


class MonotonicQueue(Generic[T]):
    """Monotonic queue - maintains a monotonic order (min or max).

    ``order`` is a binary predicate ``descending(a, b)`` that returns True
    when *a* is "better" than *b* from the queue's perspective.

    For a **min-queue** (front is the minimum), pass ``lambda a, b: a < b``
    (the default).  For a **max-queue**, pass ``lambda a, b: a > b``.
    """

    __slots__ = ("_dq", "_order")

    def __init__(self, order: Callable[[T, T], bool] | None = None) -> None:
        """Create a queue using *order* as the dominance predicate."""
        self._dq: deque[tuple[int, T]] = deque()
        self._order: Callable[[T, T], bool] = order if order is not None else lambda a, b: a < b

    # -- mutating -----------------------------------------------------------

    def push(self, item: T, key: int | None = None) -> None:
        """Push *item* onto the queue, expelling dominated elements.

        When *key* is provided it is used as the ordinal position; when
        omitted a monotonic counter internal to the queue is used.
        """
        k = key if key is not None else (self._dq[-1][0] + 1 if self._dq else 0)
        while self._dq and self._order(item, self._dq[-1][1]):
            self._dq.pop()
        self._dq.append((k, item))

    def pop(self) -> T | None:
        """Pop the front (extremum) element."""
        if not self._dq:
            return None
        return self._dq.popleft()[1]

    def pop_until(self, key: int) -> None:
        """Evict any elements whose key ≤ *key* (for sliding-window expiry)."""
        while self._dq and self._dq[0][0] <= key:
            self._dq.popleft()

    # -- query --------------------------------------------------------------

    def front(self) -> T | None:
        """Return the extremum without removing it."""
        return self._dq[0][1] if self._dq else None

    def back(self) -> T | None:
        """Return the most-recently-pushed non-dominated item."""
        return self._dq[-1][1] if self._dq else None

    def front_with_key(self) -> tuple[int, T] | None:
        """Return ``(key, value)`` of the front element."""
        return self._dq[0] if self._dq else None

    def __len__(self) -> int:
        """Return the number of live elements."""
        return len(self._dq)

    def __bool__(self) -> bool:
        """Return whether the queue is non-empty."""
        return bool(self._dq)

    def __iter__(self) -> Iterator[tuple[int, T]]:
        """Iterate over ``(key, value)`` pairs, front to back."""
        return iter(self._dq)


class MinQueue(MonotonicQueue[T]):
    """Monotonic min-queue — front is always the minimum element."""

    def __init__(self) -> None:
        """Create a min-queue."""
        super().__init__(order=lambda a, b: a < b)


class MaxQueue(MonotonicQueue[T]):
    """Monotonic max-queue — front is always the maximum element."""

    def __init__(self) -> None:
        """Create a max-queue."""
        super().__init__(order=lambda a, b: a > b)


# ---------------------------------------------------------------------------
# Sliding window aggregates
# ---------------------------------------------------------------------------


def sliding_window_maximum(nums: list[int], k: int) -> list[int]:
    """Return the maximum of every contiguous sub-array of length *k*.

    Uses a monotonic max-deque.  O(n) time and space.
    """
    if k <= 0 or not nums:
        return []
    dq: deque[int] = deque()
    result: list[int] = []
    for i, val in enumerate(nums):
        while dq and dq[0] <= i - k:
            dq.popleft()
        while dq and nums[dq[-1]] <= val:
            dq.pop()
        dq.append(i)
        if i >= k - 1:
            result.append(nums[dq[0]])
    return result


def sliding_window_minimum(nums: list[int], k: int) -> list[int]:
    """Return the minimum of every contiguous sub-array of length *k*.

    Uses a monotonic min-deque.  O(n) time and space.
    """
    if k <= 0 or not nums:
        return []
    dq: deque[int] = deque()
    result: list[int] = []
    for i, val in enumerate(nums):
        while dq and dq[0] <= i - k:
            dq.popleft()
        while dq and nums[dq[-1]] >= val:
            dq.pop()
        dq.append(i)
        if i >= k - 1:
            result.append(nums[dq[0]])
    return result


def sliding_window_aggregate(
    nums: list[int],
    k: int,
    aggregate: Callable[[Iterable[int]], int] = max,
) -> list[int]:
    """Generic sliding-window aggregate — e.g. sum, product.

    Falls back to a brute-force window scan.  For simple min/max prefer
    ``sliding_window_maximum`` / ``sliding_window_minimum`` (they are O(n)
    rather than O(n·k)).
    """
    if k <= 0 or not nums:
        return []
    result: list[int] = []
    for i in range(len(nums) - k + 1):
        result.append(aggregate(nums[i : i + k]))
    return result


# ---------------------------------------------------------------------------
# Priority monotonic queue
# ---------------------------------------------------------------------------


class PriorityMonotonic(Generic[T]):
    """Monotonic queue that also respects item priority / tie-breaking.

    Each pushed item is annotated with a *priority*; among equal-valued
    items the one with higher priority wins the front position.  Useful
    for prioritised scheduling over a sliding window.
    """

    __slots__ = ("_counter", "_dq", "_order")

    def __init__(self, order: Callable[[T, T], bool] | None = None) -> None:
        """Create a priority queue using *order* as the dominance predicate."""
        self._dq: deque[tuple[int, int, T]] = deque()
        self._order: Callable[[T, T], bool] = order if order is not None else lambda a, b: a < b
        self._counter = 0

    def push(self, item: T, priority: int = 0) -> None:
        """Push *item* with the given *priority* (higher = more important).

        Items with higher priority dominate items with lower priority but
        equal value.  The insertion index is tracked for sliding-window
        expiry.
        """
        idx = self._counter
        self._counter += 1
        while self._dq and (
            self._order(item, self._dq[-1][2]) or (item == self._dq[-1][2] and priority > self._dq[-1][1])
        ):
            self._dq.pop()
        self._dq.append((idx, priority, item))

    def front(self) -> T | None:
        """Return the extremum (accounting for priority) without removing it."""
        return self._dq[0][2] if self._dq else None

    def front_priority(self) -> tuple[int, T] | None:
        """Return ``(priority, value)`` of the front."""
        if not self._dq:
            return None
        return (self._dq[0][1], self._dq[0][2])

    def pop(self) -> T | None:
        """Pop and return the extremum."""
        if not self._dq:
            return None
        return self._dq.popleft()[2]

    def pop_until(self, idx: int) -> None:
        """Evict elements whose insertion index ≤ *idx*."""
        while self._dq and self._dq[0][0] <= idx:
            self._dq.popleft()

    def __len__(self) -> int:
        """Return the number of live elements."""
        return len(self._dq)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def windowed_stream(
    stream: Iterable[T],
    k: int,
    queue: MonotonicQueue[T] | None = None,
) -> list[T]:
    """Yield the extremum of every length-*k* window over *stream*.

    Returns a list (eager).  When *queue* is None, a ``MaxQueue`` is used.
    """
    q: MonotonicQueue[T] = queue if queue is not None else MaxQueue()
    result: list[T] = []
    buf: list[T] = []
    for item in stream:
        buf.append(item)
        q.push(item, key=len(buf) - 1)
        if len(buf) >= k:
            q.pop_until(len(buf) - k - 1)
            front = q.front()
            if front is not None:
                result.append(front)
    return result
