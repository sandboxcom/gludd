"""Bulkhead isolation — thread pool, semaphore, and queue-based isolation.

A bulkhead limits concurrent access to a resource so that saturation in one
pool does not cascade to another.  Three strategies are provided:

* :class:`SemaphoreBulkhead` - acquire a slot before entering; reject when full.
* :class:`ThreadPoolBulkhead` - bounded executor with queue; reject when queue
  is at capacity.
* :class:`QueueBulkhead` - bounded work queue with configurable overflow policy
  (reject, drop-oldest, drop-newest, caller-runs).

All three surface :class:`BulkheadRejectedError` when the requested work cannot
be accepted.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BulkheadRejectedError(Exception):
    """Raised when a bulkhead cannot accept a new execution."""


# ---------------------------------------------------------------------------
# Overflow policy
# ---------------------------------------------------------------------------


class OverflowPolicy(Enum):
    REJECT = "reject"
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    CALLER_RUNS = "caller_runs"


# ---------------------------------------------------------------------------
# SemaphoreBulkhead
# ---------------------------------------------------------------------------


class SemaphoreBulkhead:
    """Limit concurrent access with a semaphore.

    Parameters
    ----------
    max_concurrency: int
        Maximum simultaneous executions allowed.
    """

    def __init__(self, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._max_concurrency = max_concurrency
        self._rejected_count: int = 0  # protected by GIL (atomic int)

    # --- properties -------------------------------------------------------

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def active(self) -> int:
        return self._max_concurrency - self._semaphore._value

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    @property
    def available(self) -> int:
        return self._semaphore._value

    # --- execution --------------------------------------------------------

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        """Acquire a slot.  Returns ``True`` on success, ``False`` otherwise."""
        return self._semaphore.acquire(blocking=blocking, timeout=timeout)

    def release(self) -> None:
        """Release a previously acquired slot."""
        self._semaphore.release()

    def try_acquire(self) -> bool:
        """Non-blocking acquire.  Returns ``True`` if a slot was available."""
        return self._semaphore.acquire(blocking=False)

    def execute(
        self,
        fn: Callable[..., T],
        *args: Any,
        blocking: bool = True,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T:
        """Execute *fn* inside the bulkhead, acquiring and releasing a slot.

        If *blocking* is ``False`` and no slot is available,
        :class:`BulkheadRejectedError` is raised.
        """
        ok = self._semaphore.acquire(blocking=blocking, timeout=timeout)
        if not ok:
            self._rejected_count += 1
            raise BulkheadRejectedError(f"Bulkhead saturated: {self._max_concurrency} slots in use")
        try:
            return fn(*args, **kwargs)
        finally:
            self._semaphore.release()

    # --- context manager --------------------------------------------------

    def __enter__(self) -> SemaphoreBulkhead:
        self._semaphore.acquire()
        return self

    def __exit__(self, *_: Any) -> None:
        self._semaphore.release()


# ---------------------------------------------------------------------------
# ThreadPoolBulkhead
# ---------------------------------------------------------------------------


class ThreadPoolBulkhead:
    """Thread-pool isolation with a bounded work queue.

    Internally wraps a :class:`~concurrent.futures.ThreadPoolExecutor` and
    uses a :class:`~queue.Queue`-based admission gate so that callers are
    rejected before the executor's own unbounded internal queue fills up.

    Parameters
    ----------
    max_workers: int
        Thread pool size.
    max_queue_size: int
        Maximum items waiting in the admission queue.
    """

    def __init__(self, max_workers: int, max_queue_size: int) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if max_queue_size < 0:
            raise ValueError("max_queue_size must be >= 0")
        self._max_workers = max_workers
        self._max_queue_size = max_queue_size
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._queue: queue.Queue[_BulkheadWorkItem[Any]] = queue.Queue(maxsize=max_queue_size)
        self._rejected_count: int = 0
        self._running = True

    # --- properties -------------------------------------------------------

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def max_queue_size(self) -> int:
        return self._max_queue_size

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def is_saturated(self) -> bool:
        return self._queue.qsize() >= self._max_queue_size

    # --- execution --------------------------------------------------------

    def submit(
        self,
        fn: Callable[..., T],
        *args: Any,
        blocking: bool = True,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Future[T]:
        """Submit work to the bulkhead.

        Raises
        ------
        BulkheadRejectedError
            If the admission queue is full and *blocking* is ``False`` (or
            the timeout expires).
        """
        if not self._running:
            raise BulkheadRejectedError("ThreadPoolBulkhead is shut down")

        future: Future[T] = Future()
        work_item = _BulkheadWorkItem(future, fn, args, kwargs)

        def _enqueue() -> None:
            self._queue.put(work_item, block=True)

        try:
            if blocking and timeout is not None:

                def _enqueue_with_timeout() -> None:
                    self._queue.put(work_item, block=True, timeout=timeout)

                _enqueue_with_timeout()
            elif blocking and timeout is None:
                self._queue.put_nowait(work_item)
            else:
                self._queue.put_nowait(work_item)
        except queue.Full:
            self._rejected_count += 1
            raise BulkheadRejectedError(f"Bulkhead admission queue full ({self._max_queue_size})") from None

        self._executor.submit(self._drain_one)
        return future

    def _drain_one(self) -> None:
        try:
            work: _BulkheadWorkItem[Any] = self._queue.get_nowait()
        except queue.Empty:
            return
        try:
            result = work.fn(*work.args, **work.kwargs)
            work.future.set_result(result)
        except Exception as exc:
            work.future.set_exception(exc)
        finally:
            self._queue.task_done()

    def shutdown(self, wait: bool = True) -> None:
        self._running = False
        self._executor.shutdown(wait=wait)


@dataclass
class _BulkheadWorkItem(Generic[T]):
    future: Future[T]
    fn: Callable[..., T]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


# ---------------------------------------------------------------------------
# QueueBulkhead
# ---------------------------------------------------------------------------


class QueueBulkhead(Generic[T]):
    """Bounded queue isolation with configurable overflow policy.

    Parameters
    ----------
    max_size: int
        Maximum items allowed in the queue.
    overflow: OverflowPolicy
        Action when a ``put()`` would exceed *max_size*.
    """

    def __init__(self, max_size: int, overflow: OverflowPolicy) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._overflow = overflow
        self._queue: queue.Queue[T] = queue.Queue(maxsize=0)
        self._items: int = 0
        self._lock = threading.Lock()
        self._rejected_count: int = 0
        self._dropped_count: int = 0

    # --- properties -------------------------------------------------------

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def overflow(self) -> OverflowPolicy:
        return self._overflow

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def size(self) -> int:
        with self._lock:
            return self._items

    # --- queue ops --------------------------------------------------------

    def put(self, item: T) -> bool:
        """Attempt to enqueue *item*.

        Returns
        -------
        bool
            ``True`` if the item was enqueued (or executed in CALLER_RUNS).
            ``False`` if the item was dropped.

        Raises
        ------
        BulkheadRejectedError
            If *overflow* is ``REJECT`` and the queue is full.
        """
        with self._lock:
            if self._items < self._max_size:
                self._queue.put(item)
                self._items += 1
                return True

            if self._overflow == OverflowPolicy.REJECT:
                self._rejected_count += 1
                raise BulkheadRejectedError(f"Queue bulkhead full ({self._max_size} / {self._items})")
            elif self._overflow == OverflowPolicy.DROP_OLDEST:
                self._dropped_count += 1
                self._queue.get()
                self._queue.put(item)
                return False
            elif self._overflow == OverflowPolicy.DROP_NEWEST:
                self._dropped_count += 1
                return False
            elif self._overflow == OverflowPolicy.CALLER_RUNS:
                return False
            return False

    def get(self, block: bool = True, timeout: float | None = None) -> T:
        item = self._queue.get(block=block, timeout=timeout)
        with self._lock:
            self._items -= 1
        return item

    def get_nowait(self) -> T:
        item = self._queue.get_nowait()
        with self._lock:
            self._items -= 1
        return item

    def drain(self) -> list[T]:
        items: list[T] = []
        while True:
            try:
                items.append(self.get_nowait())
            except queue.Empty:
                break
        return items

    def clear(self) -> None:
        self.drain()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "BulkheadRejectedError",
    "OverflowPolicy",
    "QueueBulkhead",
    "SemaphoreBulkhead",
    "ThreadPoolBulkhead",
]
