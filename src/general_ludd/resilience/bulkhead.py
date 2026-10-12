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
    """Choose how a bounded queue handles excess submitted work."""

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
        """Initialize the validated semaphore permit budget."""
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._max_concurrency = max_concurrency
        self._rejected_count: int = 0  # protected by GIL (atomic int)

    # --- properties -------------------------------------------------------

    @property
    def max_concurrency(self) -> int:
        """Return the configured semaphore permit limit."""
        return self._max_concurrency

    @property
    def active(self) -> int:
        """Return the instantaneous count of occupied semaphore permits."""
        return self._max_concurrency - self._semaphore._value

    @property
    def rejected_count(self) -> int:
        """Return the cumulative count of rejected admission attempts."""
        return self._rejected_count

    @property
    def available(self) -> int:
        """Return the instantaneous count of available semaphore permits."""
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
        """Acquire this context's permit and return the bulkhead."""
        self._semaphore.acquire()
        return self

    def __exit__(self, *_: Any) -> None:
        """Release the permit owned by this context."""
        self._semaphore.release()


# ---------------------------------------------------------------------------
# ThreadPoolBulkhead
# ---------------------------------------------------------------------------


class ThreadPoolBulkhead:
    """Thread-pool isolation with a bounded work queue.

    Internally wraps a :class:`~concurrent.futures.ThreadPoolExecutor` and
    reserves at most ``max_workers + max_queue_size`` outstanding executions.
    Worker reservations do not depend on when executor threads start running.
    Cancelled pending work retains its reservation until the executor consumes
    it, keeping the executor's internal queue bounded as well.

    Parameters
    ----------
    max_workers: int
        Thread pool size.
    max_queue_size: int
        Maximum outstanding reservations beyond the worker budget; zero allows
        worker reservations only.
    """

    def __init__(self, max_workers: int, max_queue_size: int) -> None:
        """Initialize validated worker and backlog reservation budgets."""
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if max_queue_size < 0:
            raise ValueError("max_queue_size must be >= 0")
        self._max_workers = max_workers
        self._max_queue_size = max_queue_size
        self._capacity = max_workers + max_queue_size
        self._admission = threading.Condition()
        self._in_flight = 0
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"gludd-bulkhead-{id(self):x}")
        self._rejected_count: int = 0
        self._running = True

    # --- properties -------------------------------------------------------

    @property
    def max_workers(self) -> int:
        """Return the configured executor worker budget."""
        return self._max_workers

    @property
    def max_queue_size(self) -> int:
        """Return the configured backlog reservation budget."""
        return self._max_queue_size

    @property
    def rejected_count(self) -> int:
        """Return the cumulative count of rejected admission attempts."""
        with self._admission:
            return self._rejected_count

    @property
    def queue_size(self) -> int:
        """Return outstanding reservations beyond the worker budget."""
        with self._admission:
            return max(0, self._in_flight - self._max_workers)

    @property
    def is_saturated(self) -> bool:
        """Return whether every worker and backlog reservation is occupied."""
        with self._admission:
            return self._in_flight >= self._capacity

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

        Submissions without a timeout attempt immediate admission. A timeout
        with *blocking* enabled waits for an outstanding execution slot.

        Raises:
            BulkheadRejectedError: If admission is full or the wait expires.
            ValueError: If a blocking timeout is negative.
        """
        future: Future[T] = Future()
        work_item = _BulkheadWorkItem(future, fn, args, kwargs)
        with self._admission:
            if not self._running:
                raise BulkheadRejectedError("ThreadPoolBulkhead is shut down")
            if blocking and timeout is not None and timeout < 0:
                raise ValueError("'timeout' must be a non-negative number")
            if self._in_flight >= self._capacity:
                if not (blocking and timeout is not None) or not self._admission.wait_for(
                    lambda: not self._running or self._in_flight < self._capacity,
                    timeout=timeout,
                ):
                    self._rejected_count += 1
                    raise BulkheadRejectedError(f"Bulkhead admission queue full ({self._max_queue_size})")
                if not self._running:
                    raise BulkheadRejectedError("ThreadPoolBulkhead is shut down")
            self._in_flight += 1
            try:
                self._executor.submit(self._execute_work, work_item)
            except BaseException:
                self._in_flight -= 1
                self._admission.notify_all()
                raise
        return future

    def _release_reservation(self) -> None:
        with self._admission:
            self._in_flight -= 1
            self._admission.notify_all()

    def _execute_work(self, work: _BulkheadWorkItem[T]) -> None:
        if not work.future.set_running_or_notify_cancel():
            self._release_reservation()
            return
        try:
            result = work.fn(*work.args, **work.kwargs)
        except BaseException as exc:
            self._release_reservation()
            work.future.set_exception(exc)
        else:
            self._release_reservation()
            work.future.set_result(result)

    def shutdown(self, wait: bool = True) -> None:
        """Stop admission and drain accepted work, joining workers when requested."""
        with self._admission:
            self._running = False
            self._admission.notify_all()
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
        """Initialize a bounded logical queue and its overflow policy."""
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
        """Return the configured logical queue capacity."""
        return self._max_size

    @property
    def overflow(self) -> OverflowPolicy:
        """Return the configured overflow policy."""
        return self._overflow

    @property
    def rejected_count(self) -> int:
        """Return the cumulative count of rejected admission attempts."""
        return self._rejected_count

    @property
    def dropped_count(self) -> int:
        """Return the cumulative number of discarded items."""
        return self._dropped_count

    @property
    def size(self) -> int:
        """Return the current number of queued items."""
        with self._lock:
            return self._items

    # --- queue ops --------------------------------------------------------

    def put(self, item: T) -> bool:
        """Attempt to enqueue *item*.

        Returns:
            True when queued below capacity. False when an overflow policy
            replaces or discards work, or leaves execution to the caller.

        Raises:
            BulkheadRejectedError: If the REJECT policy meets a full queue.
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
        """Retrieve one item using the requested blocking and timeout behavior."""
        item = self._queue.get(block=block, timeout=timeout)
        with self._lock:
            self._items -= 1
        return item

    def get_nowait(self) -> T:
        """Retrieve one queued item without waiting."""
        item = self._queue.get_nowait()
        with self._lock:
            self._items -= 1
        return item

    def drain(self) -> list[T]:
        """Remove and return all currently queued items in order."""
        items: list[T] = []
        while True:
            try:
                items.append(self.get_nowait())
            except queue.Empty:
                break
        return items

    def clear(self) -> None:
        """Discard all currently queued items."""
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
