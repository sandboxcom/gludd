"""Deep semaphore/concurrency limiter tests.

Covers all semaphore patterns used in the codebase:
- ``threading.BoundedSemaphore`` (failover recording cap)
- ``threading.Semaphore`` (gateway fallback + stream provider)
- ``asyncio.BoundedSemaphore`` (dispatcher per-agent cap)
- ``asyncio.Semaphore`` (event loop dispatch + to_thread)

Dimensions: acquire/release lifecycle, max count, blocking vs non-blocking,
cancelled wait / timeout, async support, bounded guard, concurrent throughput.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Protocol, cast

import pytest

# ── test-style runner for async cases ──────────────────────────────────


def _async_run(coro: Any) -> Any:
    return asyncio.run(coro)


class _BoundedSemaphoreState(Protocol):
    _initial_value: int


# ═══════════════════════════════════════════════════════════════════════
# 1. threading.BoundedSemaphore — acquire / release / max count
# ═══════════════════════════════════════════════════════════════════════


class TestThreadingBoundedSemaphoreLifecycle:
    """Model: failover.py record_failover() bounded-semaphore pattern."""

    def test_acquire_release_idempotent(self) -> None:
        sem = threading.BoundedSemaphore(3)
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is True
        sem.release()
        assert sem.acquire(blocking=False) is True
        sem.release()
        sem.release()

    def test_max_count_respected_nonblocking(self) -> None:
        sem = threading.BoundedSemaphore(2)
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is False

    def test_max_count_respected_blocking(self) -> None:
        sem = threading.BoundedSemaphore(1)
        assert sem.acquire(blocking=True) is True
        latched: list[bool] = [False]

        def _blocker() -> None:
            sem.acquire(blocking=True)
            latched[0] = True

        t = threading.Thread(target=_blocker)
        t.start()
        time.sleep(0.1)
        assert latched[0] is False, "blocking acquire should wait"
        sem.release()
        t.join(timeout=2)
        assert latched[0] is True

    def test_release_without_acquire_raises_valueerror(self) -> None:
        sem = threading.BoundedSemaphore(1)
        with pytest.raises(ValueError, match=r"Semaphore.*release"):
            sem.release()

    def test_initial_value_preserved(self) -> None:
        sem = threading.BoundedSemaphore(5)
        state = cast(_BoundedSemaphoreState, sem)
        assert state._initial_value == 5
        assert sem.acquire(blocking=False) is True
        assert state._initial_value == 5
        sem.release()
        assert state._initial_value == 5

    def test_nonblocking_acquire_all_then_exhausted(self) -> None:
        sem = threading.BoundedSemaphore(4)
        for _ in range(4):
            assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is False
        sem.release()
        assert sem.acquire(blocking=False) is True

    def test_timeout_acquire_returns_false_on_saturation(self) -> None:
        sem = threading.BoundedSemaphore(1)
        assert sem.acquire(blocking=True) is True
        acquired = sem.acquire(timeout=0.05)
        assert acquired is False


# ═══════════════════════════════════════════════════════════════════════
# 2. threading.Semaphore — unbounded release, fallback / stream pattern
# ═══════════════════════════════════════════════════════════════════════


class TestThreadingSemaphoreUnbounded:
    """Model: gateway.py _fallback_semaphore / _stream_provider_semaphore."""

    def test_acquire_release_basic(self) -> None:
        sem = threading.Semaphore(2)
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is False
        sem.release()
        sem.release()

    def test_release_without_acquire_does_not_raise(self) -> None:
        sem = threading.Semaphore(1)
        sem.release()
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is True

    def test_timeout_acquire_saturates_then_returns_false(self) -> None:
        sem = threading.Semaphore(1)
        assert sem.acquire(blocking=True) is True
        assert sem.acquire(timeout=0.02) is False

    def test_concurrent_producers_respect_limit(self) -> None:
        sem = threading.Semaphore(2)
        peaks: list[int] = [0]
        inside: list[int] = [0]
        lock = threading.Lock()
        errors: list[Exception] = []

        def _worker(delay: float) -> None:
            if not sem.acquire(timeout=1.0):
                errors.append(RuntimeError("timeout"))
                return
            try:
                with lock:
                    inside[0] += 1
                    peaks[0] = max(peaks[0], inside[0])
                time.sleep(delay)
            finally:
                with lock:
                    inside[0] -= 1
                sem.release()

        threads = [threading.Thread(target=_worker, args=(0.05,)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors, f"worker errors: {errors}"
        assert peaks[0] <= 2, f"peak concurrency {peaks[0]} > 2"


# ═══════════════════════════════════════════════════════════════════════
# 3. asyncio.BoundedSemaphore — async acquire / release / max
# ═══════════════════════════════════════════════════════════════════════


class TestAsyncBoundedSemaphoreLifecycle:
    """Model: dispatcher.py _get_semaphore BoundedSemaphore pattern."""

    def test_acquire_release_async(self) -> None:
        async def _go() -> None:
            sem = asyncio.BoundedSemaphore(2)
            await sem.acquire()
            await sem.acquire()
            sem.release()
            await sem.acquire()
            sem.release()
            sem.release()

        _async_run(_go())

    def test_max_count_nonblocking_like(self) -> None:
        async def _go() -> None:
            sem = asyncio.BoundedSemaphore(2)
            acquired = [sem.locked()]
            await sem.acquire()
            acquired.append(sem.locked())
            await sem.acquire()
            acquired.append(sem.locked())
            assert acquired == [False, False, True]

        _async_run(_go())

    def test_release_without_acquire_raises(self) -> None:
        async def _go() -> None:
            sem = asyncio.BoundedSemaphore(1)
            with pytest.raises(ValueError):
                sem.release()

        _async_run(_go())

    def test_async_context_manager_releases_on_exit(self) -> None:
        async def _go() -> None:
            sem = asyncio.BoundedSemaphore(1)
            async with sem:
                assert sem.locked()
            assert not sem.locked()
            async with sem:
                assert sem.locked()

        _async_run(_go())

    def test_async_context_manager_releases_on_exception(self) -> None:
        async def _go() -> None:
            sem = asyncio.BoundedSemaphore(1)
            with pytest.raises(ValueError):
                async with sem:
                    raise ValueError("inside")

            assert not sem.locked(), "should be released after exception"

        _async_run(_go())


# ═══════════════════════════════════════════════════════════════════════
# 4. asyncio.Semaphore — unbounded release, concurrent through-put
# ═══════════════════════════════════════════════════════════════════════


class TestAsyncSemaphoreUnbounded:
    """Model: event_loop.py _to_thread_semaphore / _dispatch_semaphore."""

    def test_acquire_release_async_unbounded(self) -> None:
        async def _go() -> None:
            sem = asyncio.Semaphore(3)
            await sem.acquire()
            await sem.acquire()
            await sem.acquire()
            assert sem.locked()
            sem.release()
            sem.release()
            sem.release()

        _async_run(_go())

    def test_semaphore_limits_concurrent_coroutines(self) -> None:
        peak: list[int] = [0]
        inflight: list[int] = [0]

        async def _worker(sem: asyncio.Semaphore, delay: float) -> None:
            async with sem:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
                await asyncio.sleep(delay)
                inflight[0] -= 1

        async def _go() -> None:
            sem = asyncio.Semaphore(3)
            tasks = [asyncio.ensure_future(_worker(sem, 0.02)) for _ in range(20)]
            await asyncio.gather(*tasks)

        _async_run(_go())
        assert peak[0] <= 3, f"peak {peak[0]} exceeded semaphore limit 3"
        assert peak[0] >= 2, "expected multiple concurrent coroutines"

    def test_release_more_than_acquired_is_allowed(self) -> None:
        async def _go() -> None:
            sem = asyncio.Semaphore(1)
            # Initial permit + two releases = exactly three acquisitions.
            sem.release()
            sem.release()
            await sem.acquire()
            await sem.acquire()
            await sem.acquire()
            assert sem.locked()

        _async_run(_go())


# ═══════════════════════════════════════════════════════════════════════
# 5. Cancelled wait — asyncio Semaphore acquire cancellation
# ═══════════════════════════════════════════════════════════════════════


class TestAsyncSemaphoreCancelledWait:
    def test_cancelled_acquire_frees_slot(self) -> None:
        hit: list[bool] = [False]

        async def _worker(sem: asyncio.Semaphore) -> None:
            async with sem:
                hit[0] = True

        async def _go() -> None:
            sem = asyncio.Semaphore(1)
            await sem.acquire()
            t = asyncio.ensure_future(_worker(sem))
            await asyncio.sleep(0.02)
            assert not hit[0], "worker should be blocked on semaphore"
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            sem.release()
            await _worker(sem)
            assert hit[0]

        _async_run(_go())

    def test_cancelled_while_waiting_does_not_deadlock(self) -> None:
        completed: list[int] = [0]

        async def _blocked(sem: asyncio.Semaphore) -> None:
            async with sem:
                completed[0] += 1

        async def _go() -> None:
            sem = asyncio.Semaphore(1)
            await sem.acquire()
            tasks = [asyncio.ensure_future(_blocked(sem)) for _ in range(5)]
            await asyncio.sleep(0.03)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            sem.release()
            await _blocked(sem)
            assert completed[0] == 1, f"expected 1 completion, got {completed[0]}"

        _async_run(_go())

    def test_wait_for_timeout_on_acquire(self) -> None:
        async def _go() -> None:
            sem = asyncio.Semaphore(1)
            await sem.acquire()

            async def _timed_acquire() -> bool:
                async with sem:
                    return True

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_timed_acquire(), timeout=0.05)
            sem.release()
            assert await _timed_acquire() is True

        _async_run(_go())


# ═══════════════════════════════════════════════════════════════════════
# 6. Stress — rapid acquire/release, many threads, no deadlock
# ═══════════════════════════════════════════════════════════════════════


class TestSemaphoreStress:
    def test_rapid_acquire_release_no_deadlock(self) -> None:
        sem = threading.BoundedSemaphore(10)
        counter: list[int] = [0]
        lock = threading.Lock()

        def _flipper() -> None:
            for _ in range(100):
                assert sem.acquire(timeout=0.5)
                with lock:
                    counter[0] += 1
                sem.release()
                with lock:
                    counter[0] -= 1

        threads = [threading.Thread(target=_flipper) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert counter[0] == 0, f"dangling counter: {counter[0]}"

    def test_async_rapid_acquire_release_no_deadlock(self) -> None:
        counter: list[int] = [0]

        async def _flipper(sem: asyncio.BoundedSemaphore) -> None:
            for _ in range(50):
                async with sem:
                    counter[0] += 1
                    await asyncio.sleep(0)
                    counter[0] -= 1

        async def _go() -> None:
            sem = asyncio.BoundedSemaphore(5)
            tasks = [asyncio.ensure_future(_flipper(sem)) for _ in range(20)]
            await asyncio.gather(*tasks)

        _async_run(_go())
        assert counter[0] == 0, f"dangling counter: {counter[0]}"
