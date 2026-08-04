"""Deep concurrency limiter tests — semaphore, adaptive, queue-depth, async.

15+ tests covering state transitions, capacity enforcement, timeout behaviour,
AIMD hysteresis, streak-based adaptation, gate admission/rejection, stale-pruning,
drain, stats integrity, and async context-manager.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from general_ludd.networking.concurrency_limiter import (
    AdaptiveConcurrencyLimiter,
    AsyncConcurrencyLimiter,
    ConcurrencyLimiter,
    LimiterState,
    QueueDepthGate,
)

# ── ConcurrencyLimiter (bounded semaphore) ────────────────────────────────


class TestConcurrencyLimiter:
    def test_acquire_release_sequence(self) -> None:
        lim = ConcurrencyLimiter(3)
        assert lim.acquire()
        assert lim.acquire()
        assert lim.acquire()
        assert lim.stats.acquired == 3
        assert lim.active == 3
        lim.release()
        assert lim.active == 2
        lim.release()
        lim.release()
        assert lim.active == 0
        assert lim.stats.released == 3

    def test_capacity_enforced(self) -> None:
        lim = ConcurrencyLimiter(2)
        assert lim.acquire()
        assert lim.acquire()
        assert lim.stats.acquired == 2

        results: list[bool] = []

        def late_acquire() -> None:
            results.append(lim.acquire(timeout=0.2))

        t = threading.Thread(target=late_acquire)
        t.start()
        t.join()
        assert not results[0]

    def test_closed_state_rejects(self) -> None:
        lim = ConcurrencyLimiter(2)
        lim.set_state(LimiterState.CLOSED)
        assert not lim.acquire(timeout=0.05)
        assert lim.stats.rejected == 1

    def test_context_manager(self) -> None:
        lim = ConcurrencyLimiter(2)
        with lim:
            assert lim.active == 1
        assert lim.active == 0

    def test_concurrent_access_maintains_acquire_count(self) -> None:
        lim = ConcurrencyLimiter(50)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                if lim.acquire(timeout=1.0):
                    time.sleep(0.01)
                    lim.release()
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=20) as ex:
            futures = [ex.submit(worker) for _ in range(100)]
            for f in as_completed(futures):
                f.result()

        assert not errors
        assert lim.active == 0
        assert lim.stats.acquired == lim.stats.released
        assert lim.stats.acquired >= 80

    def test_stats_snapshot_is_immutable(self) -> None:
        lim = ConcurrencyLimiter(3)
        lim.acquire()
        s1 = lim.stats
        lim.release()
        s2 = lim.stats
        assert s1.acquired == 1
        assert s2.released == 1

    def test_max_concurrent_exposed(self) -> None:
        lim = ConcurrencyLimiter(7)
        assert lim.max_concurrent == 7

    def test_rejects_invalid_max_concurrent(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            ConcurrencyLimiter(0)
        with pytest.raises(ValueError, match="must be >= 1"):
            ConcurrencyLimiter(-1)


# ── AdaptiveConcurrencyLimiter (AIMD) ─────────────────────────────────────


class TestAdaptiveConcurrencyLimiter:
    def test_starts_at_max(self) -> None:
        lim = AdaptiveConcurrencyLimiter(min_concurrent=2, max_concurrent=8)
        assert lim.current_limit == 8

    def test_additive_increase(self) -> None:
        lim = AdaptiveConcurrencyLimiter(
            min_concurrent=1,
            max_concurrent=10,
            success_threshold=3,
            cooldown_seconds=0.0,
        )
        lim._current_limit = 3
        lim._last_adjust = 0.0
        lim.report_success()
        lim.report_success()
        lim.report_success()
        assert lim.current_limit == 4

    def test_multiplicative_decrease(self) -> None:
        lim = AdaptiveConcurrencyLimiter(
            min_concurrent=1,
            max_concurrent=64,
            failure_threshold=2,
            cooldown_seconds=0.0,
        )
        lim._current_limit = 32
        lim._last_adjust = 0.0
        lim.report_failure()
        lim.report_failure()
        assert lim.current_limit == 16

    def test_floor_respected(self) -> None:
        lim = AdaptiveConcurrencyLimiter(
            min_concurrent=3,
            max_concurrent=10,
            failure_threshold=1,
            cooldown_seconds=0.0,
        )
        lim._current_limit = 4
        lim._last_adjust = 0.0
        lim.report_failure()
        assert lim.current_limit == 3
        lim.report_failure()
        assert lim.current_limit == 3

    def test_ceiling_respected(self) -> None:
        lim = AdaptiveConcurrencyLimiter(
            min_concurrent=1,
            max_concurrent=5,
            success_threshold=1,
            cooldown_seconds=0.0,
        )
        lim._current_limit = 5
        lim._last_adjust = 0.0
        lim.report_success()
        assert lim.current_limit == 5

    def test_high_latency_triggers_decrease(self) -> None:
        lim = AdaptiveConcurrencyLimiter(
            min_concurrent=1,
            max_concurrent=20,
            latency_threshold_ms=200,
        )
        lim._current_limit = 10
        lim._last_adjust = 0.0
        lim._cooldown = 0.0
        lim.report_success(latency_ms=600.0)
        assert lim.current_limit < 10

    def test_cooldown_blocks_oscillation(self) -> None:
        lim = AdaptiveConcurrencyLimiter(
            min_concurrent=1,
            max_concurrent=20,
            success_threshold=1,
            cooldown_seconds=99.0,
        )
        lim._current_limit = 5
        lim._last_adjust = time.monotonic()
        lim.report_success()
        assert lim.current_limit == 5

    def test_rejects_invalid_bounds(self) -> None:
        with pytest.raises(ValueError, match=r"max_concurrent.*must be >= min_concurrent"):
            AdaptiveConcurrencyLimiter(min_concurrent=5, max_concurrent=3)
        with pytest.raises(ValueError, match="must be >= 1"):
            AdaptiveConcurrencyLimiter(min_concurrent=0, max_concurrent=5)


# ── QueueDepthGate ─────────────────────────────────────────────────────────


class TestQueueDepthGate:
    def test_basic_admit_release(self) -> None:
        gate = QueueDepthGate(3)
        assert gate.admit()
        assert gate.admit()
        assert gate.admit()
        assert gate.pending_count == 3
        gate.release()
        assert gate.pending_count == 2

    def test_admit_rejects_at_capacity(self) -> None:
        gate = QueueDepthGate(2)
        gate.admit()
        gate.admit()
        assert not gate.admit()
        assert gate.stats.rejected == 1

    def test_state_open_throttled_closed(self) -> None:
        gate = QueueDepthGate(5)
        assert gate.state == LimiterState.OPEN
        for _ in range(4):
            gate.admit()
        assert gate.state == LimiterState.THROTTLED
        gate.admit()
        assert gate.state == LimiterState.CLOSED

    def test_drain_clears_all(self) -> None:
        gate = QueueDepthGate(10)
        for _ in range(6):
            gate.admit()
        assert gate.drain() == 6
        assert gate.pending_count == 0
        assert gate.state == LimiterState.OPEN

    def test_stale_prune(self) -> None:
        gate = QueueDepthGate(10)
        gate._pending.append(time.monotonic() - 999.0)
        gate._pending.append(time.monotonic() + 999.0)
        assert gate.pending_count == 1

    def test_external_depth_source(self) -> None:
        def source() -> int:
            return 5

        gate = QueueDepthGate(5, depth_source=source)
        assert not gate.admit()
        assert gate.stats.rejected == 1

    def test_rejects_invalid_max_depth(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            QueueDepthGate(0)


# ── AsyncConcurrencyLimiter ────────────────────────────────────────────────


class TestAsyncConcurrencyLimiter:
    def test_async_context_manager(self) -> None:
        lim = AsyncConcurrencyLimiter(2)

        async def runner() -> None:
            async with lim:
                assert lim.active == 1
            assert lim.active == 0

        asyncio.run(runner())

    def test_async_capacity_enforced(self) -> None:
        lim = AsyncConcurrencyLimiter(1)
        acquired: list[bool] = []

        async def worker() -> None:
            async with lim:
                acquired.append(True)
                await asyncio.sleep(0.05)

        async def late() -> None:
            await asyncio.sleep(0.01)
            assert lim.active == 1

        async def main() -> None:
            await asyncio.gather(worker(), late())

        asyncio.run(main())
        assert len(acquired) == 1
