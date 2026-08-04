"""Deep tests for bulkhead isolation — SemaphoreBulkhead, ThreadPoolBulkhead,
QueueBulkhead.  Covers isolation, saturation rejection, graceful degradation,
queue overflow policies, and edge cases.
"""

from __future__ import annotations

import contextlib
import queue as _qmod
import threading
import time
from concurrent.futures import Future

import pytest

from general_ludd.resilience.bulkhead import (
    BulkheadRejectedError,
    OverflowPolicy,
    QueueBulkhead,
    SemaphoreBulkhead,
    ThreadPoolBulkhead,
)

# ============================================================================
# SemaphoreBulkhead
# ============================================================================


class TestSemaphoreConstruction:
    def test_valid_max_concurrency(self) -> None:
        b = SemaphoreBulkhead(4)
        assert b.max_concurrency == 4

    def test_zero_concurrency_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            SemaphoreBulkhead(0)

    def test_negative_concurrency_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            SemaphoreBulkhead(-3)


class TestSemaphoreAcquireRelease:
    def test_acquire_and_release(self) -> None:
        b = SemaphoreBulkhead(2)
        assert b.available == 2
        assert b.acquire()
        assert b.available == 1
        assert b.acquire()
        assert b.available == 0
        b.release()
        assert b.available == 1

    def test_try_acquire_saturated(self) -> None:
        b = SemaphoreBulkhead(1)
        assert b.try_acquire() is True
        assert b.available == 0
        assert b.try_acquire() is False

    def test_context_manager_acquires_and_releases(self) -> None:
        b = SemaphoreBulkhead(1)
        with b:
            assert b.available == 0
        assert b.available == 1


class TestSemaphoreExecute:
    def test_execute_acquires_and_releases(self) -> None:
        b = SemaphoreBulkhead(1)
        result = b.execute(lambda x: x + 1, 41)
        assert result == 42
        assert b.available == 1

    def test_execute_rejects_when_full(self) -> None:
        b = SemaphoreBulkhead(1)
        b.acquire()
        with pytest.raises(BulkheadRejectedError, match="saturated"):
            b.execute(lambda: None, blocking=False)
        assert b.rejected_count == 1

    def test_execute_non_blocking_no_slot(self) -> None:
        b = SemaphoreBulkhead(1)
        b.acquire()
        with pytest.raises(BulkheadRejectedError):
            b.execute(lambda: None, blocking=False)

    def test_execute_increments_rejected_counter(self) -> None:
        b = SemaphoreBulkhead(1)
        b.acquire()
        for _ in range(3):
            with contextlib.suppress(BulkheadRejectedError):
                b.execute(lambda: None, blocking=False)
        assert b.rejected_count == 3


class TestSemaphoreIsolation:
    """Verify that a saturated bulkhead does not block unrelated callers."""

    def test_isolation_independent_instances(self) -> None:
        a = SemaphoreBulkhead(1)
        b = SemaphoreBulkhead(2)
        a.acquire()
        assert a.available == 0
        assert b.available == 2
        assert b.acquire()
        assert b.available == 1


class TestSemaphoreThreadSafety:
    def test_concurrent_execute_under_limit(self) -> None:
        bulkhead = SemaphoreBulkhead(3)
        results: list[int] = []
        errors: list[Exception] = []
        latch = threading.Barrier(3)

        def worker(n: int) -> None:
            latch.wait()
            try:
                v = bulkhead.execute(lambda x: x * 2, n, blocking=True)
                results.append(v)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert sorted(results) == [0, 2, 4]


# ============================================================================
# ThreadPoolBulkhead
# ============================================================================


class TestThreadPoolConstruction:
    def test_default_construction(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=2, max_queue_size=4)
        assert tp.max_workers == 2
        assert tp.max_queue_size == 4
        assert tp.queue_size == 0
        assert not tp.is_saturated

    def test_zero_queue_size_allowed(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=1, max_queue_size=0)
        assert tp.max_queue_size == 0


class TestThreadPoolSubmit:
    def test_submit_returns_future_with_result(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=2, max_queue_size=2)
        f = tp.submit(lambda a, b: a + b, 10, 32)
        assert isinstance(f, Future)
        assert f.result(timeout=5) == 42

    def test_submit_rejects_when_queue_full_nonblocking(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=1, max_queue_size=1)
        tp.submit(time.sleep, 0.1, blocking=False)
        tp.submit(time.sleep, 0.1, blocking=False)
        with pytest.raises(BulkheadRejectedError, match="admission queue full"):
            tp.submit(time.sleep, 0.1, blocking=False)
        assert tp.rejected_count == 1

    def test_submit_increments_rejected_count(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=1, max_queue_size=1)
        tp.submit(time.sleep, 0.1, blocking=False)
        tp.submit(time.sleep, 0.1, blocking=False)
        for _ in range(2):
            with contextlib.suppress(BulkheadRejectedError):
                tp.submit(time.sleep, 0, blocking=False)
        assert tp.rejected_count == 2

    def test_submit_shutdown_rejects(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=1, max_queue_size=4)
        tp.shutdown()
        with pytest.raises(BulkheadRejectedError, match="shut down"):
            tp.submit(str, 1)


class TestThreadPoolIsolation:
    """Pool saturation must not affect other pools."""

    def test_independent_pools(self) -> None:
        tp1 = ThreadPoolBulkhead(max_workers=1, max_queue_size=1)
        tp2 = ThreadPoolBulkhead(max_workers=1, max_queue_size=2)
        tp1.submit(time.sleep, 0.2, blocking=False)
        tp1.submit(time.sleep, 0.2, blocking=False)
        with pytest.raises(BulkheadRejectedError):
            tp1.submit(time.sleep, 0, blocking=False)
        f = tp2.submit(lambda: 99)
        assert f.result(timeout=5) == 99


class TestThreadPoolGracefulDegradation:
    def test_futures_still_complete_under_load(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=3, max_queue_size=6)
        futs = []
        for i in range(6):
            futs.append(tp.submit(lambda x: x, i, blocking=False))
        for i, f in enumerate(futs):
            assert f.result(timeout=5) == i


# ============================================================================
# QueueBulkhead
# ============================================================================


class TestQueueBulkheadConstruction:
    def test_valid_construction(self) -> None:
        q = QueueBulkhead[str](max_size=5, overflow=OverflowPolicy.REJECT)
        assert q.max_size == 5
        assert q.overflow == OverflowPolicy.REJECT
        assert q.size == 0

    def test_zero_size_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            QueueBulkhead[int](max_size=0, overflow=OverflowPolicy.REJECT)


class TestQueueBulkheadReject:
    def test_put_below_capacity(self) -> None:
        q = QueueBulkhead[int](max_size=2, overflow=OverflowPolicy.REJECT)
        assert q.put(1) is True
        assert q.put(2) is True
        assert q.size == 2

    def test_put_at_capacity_rejects(self) -> None:
        q = QueueBulkhead[int](max_size=2, overflow=OverflowPolicy.REJECT)
        q.put(1)
        q.put(2)
        with pytest.raises(BulkheadRejectedError, match="Queue bulkhead full"):
            q.put(3)
        assert q.rejected_count == 1

    def test_rejected_count_accumulates(self) -> None:
        q = QueueBulkhead[int](max_size=1, overflow=OverflowPolicy.REJECT)
        q.put(1)
        for _ in range(5):
            with contextlib.suppress(BulkheadRejectedError):
                q.put(99)
        assert q.rejected_count == 5


class TestQueueBulkheadDropOldest:
    def test_drop_oldest_evicts_first_item(self) -> None:
        q = QueueBulkhead[int](max_size=2, overflow=OverflowPolicy.DROP_OLDEST)
        q.put(1)
        q.put(2)
        accepted = q.put(3)
        assert accepted is False
        assert q.dropped_count == 1
        assert q.get() == 2
        assert q.get() == 3

    def test_drop_oldest_single_item(self) -> None:
        q = QueueBulkhead[int](max_size=1, overflow=OverflowPolicy.DROP_OLDEST)
        q.put(10)
        q.put(20)
        assert q.get() == 20


class TestQueueBulkheadDropNewest:
    def test_drop_newest_discards_incoming(self) -> None:
        q = QueueBulkhead[int](max_size=2, overflow=OverflowPolicy.DROP_NEWEST)
        q.put(1)
        q.put(2)
        accepted = q.put(3)
        assert accepted is False
        assert q.dropped_count == 1
        assert q.get() == 1
        assert q.get() == 2
        with pytest.raises(_qmod.Empty):
            q.get_nowait()


class TestQueueBulkheadCallerRuns:
    def test_caller_runs_returns_false_no_enqueue(self) -> None:
        q = QueueBulkhead[int](max_size=1, overflow=OverflowPolicy.CALLER_RUNS)
        q.put(42)
        accepted = q.put(99)
        assert accepted is False
        assert q.get() == 42
        with pytest.raises(_qmod.Empty):
            q.get_nowait()


class TestQueueBulkheadOps:
    def test_get_block_waits_for_item(self) -> None:
        q = QueueBulkhead[int](max_size=4, overflow=OverflowPolicy.REJECT)
        result: list[int] = []

        def producer() -> None:
            time.sleep(0.05)
            q.put(7)

        def consumer() -> None:
            result.append(q.get(block=True, timeout=5))

        tp = threading.Thread(target=producer)
        tc = threading.Thread(target=consumer)
        tc.start()
        tp.start()
        tc.join(timeout=3)
        tp.join(timeout=3)
        assert result == [7]

    def test_drain_empties_queue(self) -> None:
        q = QueueBulkhead[int](max_size=10, overflow=OverflowPolicy.REJECT)
        for i in range(5):
            q.put(i)
        items = q.drain()
        assert items == [0, 1, 2, 3, 4]
        assert q.size == 0

    def test_clear_resets_size(self) -> None:
        q = QueueBulkhead[int](max_size=10, overflow=OverflowPolicy.REJECT)
        q.put(1)
        q.put(2)
        q.clear()
        assert q.size == 0


class TestQueueBulkheadIsolation:
    def test_independent_queues_dont_interfere(self) -> None:
        qa = QueueBulkhead[int](max_size=1, overflow=OverflowPolicy.REJECT)
        qb = QueueBulkhead[int](max_size=2, overflow=OverflowPolicy.REJECT)
        qa.put(1)
        with pytest.raises(BulkheadRejectedError):
            qa.put(2)
        qb.put(10)
        qb.put(20)
        assert qb.size == 2


# ============================================================================
# Edge cases
# ============================================================================


class TestBulkheadEdgeCases:
    def test_semaphore_release_too_many_raises(self) -> None:
        b = SemaphoreBulkhead(2)
        b.acquire()
        b.release()
        assert b.available == 2
        with pytest.raises(ValueError, match="released too many times"):
            b.release()

    def test_threadpool_submit_after_drain_resumes(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=1, max_queue_size=2)
        f1 = tp.submit(lambda: 1)
        f2 = tp.submit(lambda: 2)
        assert f1.result(timeout=5) == 1
        assert f2.result(timeout=5) == 2
        f3 = tp.submit(lambda: 3)
        assert f3.result(timeout=5) == 3

    def test_threadpool_exception_propagates_to_future(self) -> None:
        tp = ThreadPoolBulkhead(max_workers=1, max_queue_size=2)
        f = tp.submit(lambda: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            f.result(timeout=5)

    def test_queue_overflow_all_policies_defined(self) -> None:
        for policy in OverflowPolicy:
            q = QueueBulkhead[int](max_size=1, overflow=policy)
            q.put(1)
            if policy == OverflowPolicy.REJECT:
                with pytest.raises(BulkheadRejectedError):
                    q.put(2)
            elif policy == OverflowPolicy.DROP_OLDEST:
                assert q.put(2) is False
                assert q.get() == 2
            elif policy == OverflowPolicy.DROP_NEWEST or policy == OverflowPolicy.CALLER_RUNS:
                assert q.put(2) is False
                assert q.get() == 1
