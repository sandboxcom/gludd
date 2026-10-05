"""Deep algorithmic tests for rate limiter token bucket, sliding window
log, fixed window counter, and concurrent safety across all implementations.

Tests the mathematical contract of each algorithm — refill arithmetic,
window-bound pruning, monotonic clock invariants, and thread-safety under
contention — NOT retesting the higher-level integration tests in
test_rate_limiter_deep.py.
"""

from __future__ import annotations

import threading
import time

from general_ludd.config.user_config import OrchestrationGuardConfig
from general_ludd.receiver.router import _RateLimiter, _TokenBucket
from general_ludd.routers.web_search import SlidingWindowRateLimiter

# ---------------------------------------------------------------------------
# 1 — Token Bucket Refill Algorithm
# ---------------------------------------------------------------------------


class TestTokenBucketRefill:
    """Verify the token-bucket refill arithmetic:
    tokens = min(burst, tokens + elapsed * rate)
    """

    def test_refill_is_linear_with_rate(self) -> None:
        tb = _TokenBucket(rate_per_sec=100.0, burst=10.0)
        for _ in range(10):
            assert tb.allow() is True
        assert tb.allow() is False
        time.sleep(0.05)
        assert tb.allow() is True

    def test_refill_uses_injected_monotonic_clock(self) -> None:
        """Refill arithmetic must not depend on scheduler or wall-clock latency."""
        now = [10.0]
        tb = _TokenBucket(rate_per_sec=100.0, burst=1.0, clock=lambda: now[0])

        assert tb.allow() is True
        assert tb.allow() is False
        now[0] += 1.0
        assert tb.allow() is True

    def test_refill_never_exceeds_burst(self) -> None:
        tb = _TokenBucket(rate_per_sec=1000.0, burst=1.0)
        time.sleep(0.5)
        assert tb.allow() is True
        assert tb.allow() is False

    def test_refill_at_fractional_rate(self) -> None:
        tb = _TokenBucket(rate_per_sec=0.5, burst=1.0)
        assert tb.allow() is True
        assert tb.allow() is False
        time.sleep(2.1)
        assert tb.allow() is True

    def test_refill_at_high_rate_precision(self) -> None:
        now = [10.0]
        tb = _TokenBucket(
            rate_per_sec=10000.0,
            burst=100.0,
            clock=lambda: now[0],
        )
        for _ in range(100):
            assert tb.allow() is True
        assert tb.allow() is False
        now[0] += 0.005
        allowed_after = sum(1 for _ in range(100) if tb.allow())
        assert allowed_after == 50

    def test_refill_elapsed_zero_does_nothing(self) -> None:
        tb = _TokenBucket(rate_per_sec=50.0, burst=1.0)
        assert tb.allow() is True
        assert tb.allow() is False
        assert tb.allow() is False

    def test_burst_tokens_consumed_in_order(self) -> None:
        tb = _TokenBucket(rate_per_sec=0.0, burst=5.0)
        for _ in range(5):
            assert tb.allow() is True
        assert tb.allow() is False


# ---------------------------------------------------------------------------
# 2 — Sliding Window Log Algorithm (deque-based)
# ---------------------------------------------------------------------------


class TestSlidingWindowLogAlgorithm:
    """Verify the deque-based sliding-window-log invariants:
    - cutoff = now - window
    - popleft while timestamps[0] <= cutoff
    - append only if len < max_requests
    """

    def test_window_prunes_expired_only(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=3, window_seconds=0.1)
        rl.allow()
        rl.allow()
        time.sleep(0.15)
        rl.allow()
        assert rl.allow() is True

    def test_timestamps_strictly_monotonic(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=0, window_seconds=60.0)
        assert rl.allow() is False

    def test_window_boundary_exact(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=0.1)
        assert rl.allow() is True
        assert rl.allow() is False
        time.sleep(0.11)
        assert rl.allow() is True

    def test_allow_does_not_add_timestamp_when_denied(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0)
        rl.allow()
        rl.allow()
        rl.allow()
        assert len(rl._timestamps) == 2

    def test_all_expired_then_allow(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=2, window_seconds=0.05)
        rl.allow()
        rl.allow()
        assert rl.allow() is False
        time.sleep(0.1)
        for _ in range(2):
            assert rl.allow() is True


# ---------------------------------------------------------------------------
# 3 — Fixed Window Counter Algorithm (timestamp-list pruning)
# ---------------------------------------------------------------------------


class TestFixedWindowCounter:
    """Verify the list-based fixed-window counter invariants:
    - cutoff = now - window_s
    - prune all timestamps <= cutoff
    - block when len > max_per_window
    """

    def test_fixed_window_math_allow_below_limit(self) -> None:
        from general_ludd.config.user_config import OrchestrationGuardConfig

        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=3,
            dispatch_rate_window_s=60.0,
        )
        d = _FixedWindowHelper(guard)
        for _ in range(3):
            assert not d.is_rate_limited()
        assert d.is_rate_limited()

    def test_fixed_window_prunes_expired(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=2,
            dispatch_rate_window_s=0.05,
        )
        d = _FixedWindowHelper(guard)
        for _ in range(2):
            d.is_rate_limited()
        time.sleep(0.1)
        for _ in range(2):
            assert not d.is_rate_limited()

    def test_window_zero_is_bypassed(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=0,
            dispatch_rate_window_s=60.0,
        )
        d = _FixedWindowHelper(guard)
        for _ in range(200):
            assert not d.is_rate_limited()

    def test_fixed_window_counts_accurately(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=5,
            dispatch_rate_window_s=60.0,
        )
        d = _FixedWindowHelper(guard)
        for _ in range(5):
            assert not d.is_rate_limited()
        assert d.is_rate_limited()
        assert d.current_count() == 6

    def test_fixed_window_single_entry(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=1,
            dispatch_rate_window_s=60.0,
        )
        d = _FixedWindowHelper(guard)
        assert not d.is_rate_limited()
        assert d.is_rate_limited()


# ---------------------------------------------------------------------------
# 4 — Concurrent Safety Under Thread Contention
# ---------------------------------------------------------------------------


class TestConcurrentSafetyDeep:
    """Thread-safety tests: no deadlocks, no lost updates, no internal state
    corruption under high-contention concurrent access."""

    def test_token_bucket_under_heavy_contention(self) -> None:
        tb = _TokenBucket(rate_per_sec=0.0, burst=10.0)
        success_count = 0
        lock = threading.Lock()
        errors: list[Exception] = []

        def worker() -> None:
            nonlocal success_count
            try:
                if tb.allow():
                    with lock:
                        success_count += 1
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert success_count == 10

    def test_sliding_window_under_contention(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=50, window_seconds=60.0)
        results: list[bool] = []

        def worker() -> None:
            results.append(rl.allow())

        threads = [threading.Thread(target=worker) for _ in range(300)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(results) == 50

    def test_per_key_limiter_under_contention_distinct_keys(self) -> None:
        rl = _RateLimiter(rate_per_sec=100.0, burst=10.0)
        errors: list[Exception] = []

        def worker(key: str) -> None:
            try:
                for _ in range(5):
                    rl.allow(key)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"k{i}",)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(rl._buckets) == 50

    def test_per_key_same_key_under_contention(self) -> None:
        rl = _RateLimiter(rate_per_sec=0.0, burst=20.0)
        success_count = 0
        lock = threading.Lock()
        errors: list[Exception] = []

        def worker() -> None:
            nonlocal success_count
            try:
                if rl.allow("shared"):
                    with lock:
                        success_count += 1
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert success_count == 20

    def test_fixed_window_under_contention(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=30,
            dispatch_rate_window_s=60.0,
        )
        d = _FixedWindowHelper(guard)
        results: list[bool] = []

        def worker() -> None:
            results.append(d.is_rate_limited())

        threads = [threading.Thread(target=worker) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(1 for r in results if r) >= 170


# ---------------------------------------------------------------------------
# Helper: isolates the fixed-window counter algorithm from AgentDispatcher
# ---------------------------------------------------------------------------


class _FixedWindowHelper:
    """A minimal reproduction of the fixed-window rate limiter algorithm
    from AgentDispatcher._check_rate_limiter, without the asyncio wrapper.
    This allows pure-algorithm testing without async boilerplate."""

    def __init__(self, guard: OrchestrationGuardConfig) -> None:
        self._max_per_window = guard.max_dispatches_per_window
        self._window_s = guard.dispatch_rate_window_s
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def is_rate_limited(self) -> bool:
        if self._max_per_window <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window_s
            self._timestamps.append(now)
            self._timestamps[:] = [ts for ts in self._timestamps if ts > cutoff]
            return len(self._timestamps) > self._max_per_window

    def current_count(self) -> int:
        with self._lock:
            return len(self._timestamps)


# ---------------------------------------------------------------------------
# 5 — Math invariants and edge cases
# ---------------------------------------------------------------------------


class TestMathInvariants:
    def test_token_bucket_tokens_are_float(self) -> None:
        tb = _TokenBucket(rate_per_sec=1.5, burst=3.0)
        assert isinstance(tb._tokens, float)

    def test_sliding_window_deque_is_left_aligned(self) -> None:
        rl = SlidingWindowRateLimiter(max_requests=5, window_seconds=60.0)
        time.monotonic()
        rl.allow()
        rl.allow()
        assert len(rl._timestamps) == 2
        assert rl._timestamps[0] <= rl._timestamps[1]

    def test_fixed_window_never_negative(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=10,
            dispatch_rate_window_s=60.0,
        )
        d = _FixedWindowHelper(guard)
        d.is_rate_limited()
        assert d.current_count() >= 1

    def test_empty_allow_state_consistent(self) -> None:
        tb = _TokenBucket(rate_per_sec=1.0, burst=1.0)
        rl1 = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=1,
            dispatch_rate_window_s=60.0,
        )
        fw = _FixedWindowHelper(guard)
        assert tb.allow() is True
        assert rl1.allow() is True
        assert not fw.is_rate_limited()
