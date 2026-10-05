"""Retry queue with exponential backoff, max retries, and dead letter queue.

An in-process, thread-safe queue for workloads that may fail transiently.
Each item carries an attempt counter; on :meth:`RetryQueue.nack` it is
re-enqueued with an exponentially growing backoff delay.  Items that exceed
``max_retries`` are routed to a **dead letter queue** (DLQ) for operator
inspection and manual replay.

Usage sketch::

    rq = RetryQueue(max_retries=3, base_delay=0.05)
    rq.enqueue({"task": "send_email", "to": "a@b.com"})

    while (item := rq.dequeue(timeout=30)) is not None:
        if item.is_poison:
            break   # shutdown sentinel
        try:
            do_work(item.payload)
        except TransientError as exc:
            rq.nack(item.item_id, str(exc))
        except FatalError:
            rq.ack(item.item_id)   # will never succeed — drop
        else:
            rq.ack(item.item_id)

    for dead in rq.get_dlq_items():
        operator.review(dead.payload, dead.errors)
"""

from __future__ import annotations

import heapq
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# data types
# ---------------------------------------------------------------------------


@dataclass
class RetryItem:
    """An item travelling through the retry pipeline.

    Attributes:
        item_id:  Stable unique identifier (UUID4).
        payload:  Arbitrary user data enqueued with the item.
        priority: Higher values are dequeued first (max-heap semantics).
        attempt:  How many times the item has been **dequeued** (0-indexed).
        errors:   Error messages collected across nacks.
        is_poison: ``True`` for the poison-pill shutdown sentinel.
        poisoned:  Alias for *is_poison*.
        enqueued_at:  Monotonic timestamp of last enqueue/requeue.
        ready_at:     Monotonic timestamp when the item becomes eligible for dequeue.
    """

    item_id: str
    payload: Any
    priority: int
    attempt: int = 0
    errors: list[str] = field(default_factory=list)
    is_poison: bool = False
    enqueued_at: float = 0.0
    ready_at: float = 0.0

    @property
    def poisoned(self) -> bool:
        """Return whether this item is the poison-pill sentinel."""
        return self.is_poison


@dataclass
class _DLQEntry:
    """Persistent record of an item that exhausted its retries."""

    item_id: str
    payload: Any
    errors: list[str]
    attempts: int
    moved_at: float


# ---------------------------------------------------------------------------
# internal heap helpers
# ---------------------------------------------------------------------------


class _MaxHeap:
    """Max-heap wrapper pushing the *largest* integer key to the top."""

    @staticmethod
    def push(heap: list[tuple[int, float, str]], priority: int, ts: float, item_id: str) -> None:
        heapq.heappush(heap, (-priority, ts, item_id))

    @staticmethod
    def pop(heap: list[tuple[int, float, str]]) -> tuple[int, float, str]:
        neg_pri, ts, item_id = heapq.heappop(heap)
        return -neg_pri, ts, item_id


# ---------------------------------------------------------------------------
# RetryQueue
# ---------------------------------------------------------------------------


class RetryQueue:
    """Thread-safe retry queue with exponential backoff and dead letter routing.

    Parameters:
        max_retries:  Items that fail this many times move to the DLQ.
        base_delay:   Seconds.  Delay after first nack = ``base_delay``.
        max_delay:    Ceiling on computed backoff (default *60 s*).
        clock:        Callable returning seconds (monotonic).  Injectable for tests.
    """

    def __init__(
        self,
        max_retries: int,
        base_delay: float,
        max_delay: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize a queue with bounded retries and injectable timing."""
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        if max_delay < 0:
            raise ValueError("max_delay must be >= 0")

        self.max_retries: int = max_retries
        self.base_delay: float = base_delay
        self.max_delay: float = max_delay
        self._clock: Callable[[], float] = clock

        self._lock: threading.Lock = threading.Lock()

        # pending items keyed by item_id (FIFO insertion within same priority)
        self._items: OrderedDict[str, RetryItem] = OrderedDict()
        # priority heap: (-priority, enqueued_at, item_id) for stable ordering
        self._heap: list[tuple[int, float, str]] = []
        # currently dequeued / "in-flight"
        self._active: dict[str, RetryItem] = {}
        # dead letter queue
        self._dlq: list[_DLQEntry] = []

        self._condition: threading.Condition = threading.Condition(self._lock)

    # ------------------------------------------------------------------ public properties

    @property
    def size(self) -> int:
        """Number of items waiting to be dequeued."""
        with self._lock:
            return len(self._items)

    @property
    def active_count(self) -> int:
        """Number of items currently dequeued (not yet acked / nacked)."""
        with self._lock:
            return len(self._active)

    @property
    def dlq_size(self) -> int:
        """Number of items in the dead letter queue."""
        with self._lock:
            return len(self._dlq)

    # ------------------------------------------------------------------ backoff

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff: ``base_delay * 2^attempt``, capped at *max_delay*."""
        raw = self.base_delay * (2**attempt)
        return float(min(raw, self.max_delay))

    # ------------------------------------------------------------------ enqueue / deque

    def enqueue(self, payload: Any, priority: int = 0) -> str:
        """Push a payload onto the queue.

        Returns the assigned *item_id*.
        """
        item_id = uuid.uuid4().hex
        now = self._clock()
        with self._condition:
            item = RetryItem(
                item_id=item_id,
                payload=payload,
                priority=priority,
                attempt=0,
                enqueued_at=now,
                ready_at=now,
            )
            self._items[item_id] = item
            _MaxHeap.push(self._heap, priority, now, item_id)
            self._condition.notify()
        return item_id

    def poison(self) -> str:
        """Push a poison-pill sentinel.  Dequeue returns it as a signal to stop.

        Returns the sentinel's *item_id*.
        """
        item_id = uuid.uuid4().hex
        now = self._clock()
        with self._condition:
            item = RetryItem(
                item_id=item_id,
                payload=None,
                priority=999_999_999,  # always first
                is_poison=True,
                enqueued_at=now,
                ready_at=now,
            )
            self._items[item_id] = item
            _MaxHeap.push(self._heap, item.priority, now, item_id)
            self._condition.notify()
        return item_id

    def dequeue(self, timeout: float | None = None) -> RetryItem | None:
        """Remove and return the highest-priority, ready item.

        Blocks up to *timeout* seconds.  Returns ``None`` on timeout.
        """
        deadline: float | None = None
        if timeout is not None:
            deadline = self._clock() + timeout

        with self._condition:
            while True:
                item = self._pop_ready()
                if item is not None:
                    self._active[item.item_id] = item
                    return item

                if deadline is not None:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return None
                    self._condition.wait(timeout=remaining)
                else:
                    self._condition.wait()

    def peek(self, timeout: float | None = None) -> RetryItem | None:
        """Return the next ready item **without removing it** (does not activate)."""
        deadline: float | None = None
        if timeout is not None:
            deadline = self._clock() + timeout

        with self._condition:
            while True:
                item = self._pop_ready(activate=False)
                if item is not None:
                    return item

                if deadline is not None:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return None
                    self._condition.wait(timeout=remaining)
                else:
                    self._condition.wait()

    # ------------------------------------------------------------------ ack / nack / requeue

    def ack(self, item_id: str) -> None:
        """Acknowledge successful processing — remove the item completely."""
        with self._lock:
            if item_id not in self._active:
                raise KeyError(f"unknown item {item_id!r}")
            del self._active[item_id]

    def nack(self, item_id: str, error: str) -> None:
        """Negative-acknowledge — record the error and requeue with backoff.

        If the item has reached *max_retries*, it moves to the DLQ instead.
        """
        with self._condition:
            if item_id not in self._active:
                raise KeyError(f"unknown item {item_id!r}")

            item = self._active.pop(item_id)
            item.errors.append(error)

            if item.attempt >= self.max_retries:
                self._dlq.append(
                    _DLQEntry(
                        item_id=item.item_id,
                        payload=item.payload,
                        errors=list(item.errors),
                        attempts=item.attempt,
                        moved_at=self._clock(),
                    )
                )
                return

            delay = self._backoff_delay(item.attempt)
            now = self._clock()
            item.attempt += 1
            item.ready_at = now + delay
            item.enqueued_at = now
            self._items[item.item_id] = item
            _MaxHeap.push(self._heap, item.priority, now, item.item_id)
            self._condition.notify()

    def requeue(self, item_id: str, delay: float = 0.0) -> None:
        """Requeue an in-flight item with a custom delay (seconds).

        Does **not** increment the attempt counter — use :meth:`nack` for that.
        """
        with self._condition:
            if item_id not in self._active:
                raise KeyError(f"unknown item {item_id!r}")

            item = self._active.pop(item_id)
            now = self._clock()
            item.ready_at = now + delay
            item.enqueued_at = now
            self._items[item.item_id] = item
            _MaxHeap.push(self._heap, item.priority, now, item.item_id)
            self._condition.notify()

    # ------------------------------------------------------------------ dead letter queue

    def get_dlq_items(self) -> list[_DLQEntry]:
        """Return a snapshot of all dead-lettered items."""
        with self._lock:
            return list(self._dlq)

    # ------------------------------------------------------------------ iteration

    def __iter__(self) -> Iterator[RetryItem]:
        """Iterate over ready items (dequeue until timeout)."""
        while True:
            item = self.dequeue(timeout=0.0)
            if item is None:
                return
            yield item

    # ------------------------------------------------------------------ internals

    def _pop_ready(self, activate: bool = True) -> RetryItem | None:
        """Pop the highest-priority item whose *ready_at* has passed.

        Must be called while holding ``self._lock``.
        """
        now = self._clock()
        deferred: list[RetryItem] = []
        while self._heap:
            _pri, _ts, item_id = _MaxHeap.pop(self._heap)
            item = self._items.get(item_id)
            if item is None:
                continue  # stale entry; skip
            if item.ready_at > now:
                # A delayed high-priority item must not starve a lower-priority
                # item that is already ready. Preserve it while scanning.
                deferred.append(item)
                continue
            for waiting in deferred:
                _MaxHeap.push(
                    self._heap,
                    waiting.priority,
                    waiting.enqueued_at,
                    waiting.item_id,
                )
            if activate:
                del self._items[item_id]
            else:
                _MaxHeap.push(
                    self._heap,
                    item.priority,
                    item.enqueued_at,
                    item.item_id,
                )
            return item
        for waiting in deferred:
            _MaxHeap.push(
                self._heap,
                waiting.priority,
                waiting.enqueued_at,
                waiting.item_id,
            )
        return None
