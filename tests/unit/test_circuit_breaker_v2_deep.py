"""Deep circuit breaker tests: full state machine, sliding window, concurrent probes.

Covers CircuitBreaker, BreakerConfig, CircuitBreakerStats, and MultiBreaker from
src/general_ludd/resilience/circuit_breaker.py.

Tests:
  1.  init_closed          — breaker starts in CLOSED state
  2.  allow_when_closed      — requests pass through CLOSED
  3.  opens_at_threshold     — exactly failure_threshold opens breaker
  4.  opens_beyond_threshold — past threshold, stays OPEN
  5.  deny_when_open_before_recovery  — OPEN blocks until recovery_timeout
  6.  transitions_to_half_open        — after recovery_timeout, allows one probe
  7.  half_open_probe_success_recloses — success in HALF_OPEN → CLOSED
  8.  half_open_probe_failure_reopens — failure in HALF_OPEN → immediate OPEN
  9.  sliding_window_expires          — window expiry drops old failures
  10. sliding_window_keeps_recent     — recent failures still trip
  11. non_trip_kinds_ignored          — non_trip_kinds never open breaker
  12. non_trip_kinds_count_in_stats   — non-trip failures increment total failures
  13. stats_row_values                — stats reflect accurate counts
  14. concurrent_half_open_only_one_probe — 10 threads, exactly 1 admitted
  15. reset_clears_everything         — reset returns to CLOSED with zero stats
  16. multi_breaker_independence       — each breaker tracks independently
  17. consecutive_failures_tracked     — consecutive counter works
  18. success_during_closed_noop       — CLOSED stays CLOSED after success
  19. no_stampede_after_probe_failure  — re-ARM → cooldown → fresh probe
  20. edge_zero_threshold              — threshold=0 opens on first failure
  21. stats_snapshot_readonly          — stats is a snapshot, not live proxy
"""

from __future__ import annotations

import threading
import time

from general_ludd.resilience.circuit_breaker import (
    BreakerConfig,
    CircuitBreaker,
    MultiBreaker,
    State,
)


class TestCircuitBreakerClosedState:
    def test_initially_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == State.CLOSED
        assert cb.allow_request() is True

    def test_allow_when_closed(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=3, window_seconds=60))
        for _ in range(2):
            cb.record_failure("connection_timeout")
        assert cb.state == State.CLOSED
        assert cb.allow_request() is True

    def test_success_during_closed_noop(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=2, window_seconds=60))
        cb.record_failure("timeout")
        cb.record_success()
        assert cb.state == State.CLOSED
        assert cb.stats.consecutive_failures == 0


class TestCircuitBreakerOpenState:
    def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=3, window_seconds=60))
        for _ in range(3):
            cb.record_failure("read_timeout")
        assert cb.state == State.OPEN
        assert cb.allow_request() is False

    def test_opens_beyond_threshold(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=2, window_seconds=60))
        for _ in range(5):
            cb.record_failure("provider_error")
        assert cb.state == State.OPEN
        assert cb.allow_request() is False

    def test_deny_when_open_before_recovery(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=1, recovery_timeout=60.0, window_seconds=120))
        cb.record_failure("timeout")
        assert cb.state == State.OPEN
        assert cb.allow_request() is False


class TestCircuitBreakerHalfOpenState:
    def test_transitions_to_half_open_after_recovery(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=1, recovery_timeout=0.01, window_seconds=60))
        cb.record_failure("timeout")
        assert cb.state == State.OPEN
        time.sleep(0.03)
        assert cb.allow_request() is True
        assert cb.state == State.HALF_OPEN
        assert cb.allow_request() is False

    def test_half_open_probe_success_recloses(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=1, recovery_timeout=0.01, window_seconds=60))
        cb.record_failure("timeout")
        time.sleep(0.03)
        assert cb.allow_request() is True
        cb.record_success()
        assert cb.state == State.CLOSED
        assert cb.allow_request() is True

    def test_half_open_probe_failure_reopens(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=1, recovery_timeout=0.01, window_seconds=60))
        cb.record_failure("timeout")
        time.sleep(0.03)
        assert cb.allow_request() is True
        cb.record_failure("read_timeout")
        assert cb.state == State.OPEN
        assert cb.allow_request() is False

    def test_no_stampede_after_probe_failure(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=1, recovery_timeout=0.01, window_seconds=60))
        cb.record_failure("timeout")
        time.sleep(0.03)
        assert cb.allow_request() is True
        cb.record_failure("timeout")
        assert cb.state == State.OPEN
        time.sleep(0.03)
        assert cb.allow_request() is True
        assert cb.state == State.HALF_OPEN


class TestSlidingWindow:
    def test_window_expiry_drops_old_failures(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=3, window_seconds=0.02, recovery_timeout=0.1))
        for _ in range(2):
            cb.record_failure("slow")
        assert cb.state == State.CLOSED
        assert cb.stats.window_failure_count == 2
        time.sleep(0.04)
        assert cb.stats.window_failure_count == 0

    def test_window_keeps_recent_failures(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=3, window_seconds=10.0, recovery_timeout=0.1))
        for _ in range(3):
            cb.record_failure("fast")
        assert cb.state == State.OPEN
        assert cb.stats.window_failure_count >= 3


class TestNonTripKinds:
    def test_non_trip_kinds_never_open(self):
        cfg = BreakerConfig(
            failure_threshold=1, window_seconds=60, non_trip_kinds=frozenset({"auth_error", "context_length"})
        )
        cb = CircuitBreaker("test", cfg)
        final_ret = False
        for _ in range(10):
            final_ret = cb.record_failure("auth_error")
        assert cb.state == State.CLOSED
        assert final_ret is False

    def test_non_trip_kinds_count_in_stats(self):
        cfg = BreakerConfig(failure_threshold=5, window_seconds=60, non_trip_kinds=frozenset({"auth_error"}))
        cb = CircuitBreaker("test", cfg)
        cb.record_failure("auth_error")
        cb.record_failure("auth_error")
        assert cb.stats.total_failures == 2
        assert cb.stats.window_failure_count == 0


class TestStats:
    def test_stats_reflect_accurate_counts(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=3, window_seconds=60))
        cb.record_failure("e1")
        cb.record_failure("e2")
        cb.record_success()
        cb.record_failure("e3")
        cb.record_failure("e4")  # trips
        s = cb.stats
        assert s.total_failures == 4
        assert s.total_successes == 1
        assert s.current_state == State.OPEN
        assert s.consecutive_failures == 2
        assert s.state_transitions == 1

    def test_stats_snapshot_readonly(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=2, window_seconds=60))
        cb.record_failure("e1")
        s1 = cb.stats
        assert s1.total_failures == 1
        cb.record_failure("e2")
        assert s1.total_failures == 1


class TestConcurrentProbes:
    def test_concurrent_half_open_only_one_probe_admitted(self):
        cfg = BreakerConfig(failure_threshold=1, recovery_timeout=0.01, window_seconds=60)
        cb = CircuitBreaker("test", cfg)
        cb.record_failure("timeout")
        time.sleep(0.03)

        admitted = 0
        lock = threading.Lock()

        def caller():
            nonlocal admitted
            if cb.allow_request():
                with lock:
                    admitted += 1

        threads = [threading.Thread(target=caller) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert admitted == 1


class TestReset:
    def test_reset_clears_everything(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=2, window_seconds=60))
        for _ in range(3):
            cb.record_failure("err")
        assert cb.state == State.OPEN
        cb.reset()
        assert cb.state == State.CLOSED
        s = cb.stats
        assert s.total_failures == 0
        assert s.total_successes == 0
        assert s.window_failure_count == 0
        assert s.consecutive_failures == 0
        assert s.opened_at is None


class TestMultiBreaker:
    def test_independent_circuits(self):
        mb = MultiBreaker(BreakerConfig(failure_threshold=2, window_seconds=60))
        b1 = mb.get("svc-a")
        b2 = mb.get("svc-b")
        for _ in range(2):
            b1.record_failure("err")
        assert b1.state == State.OPEN
        assert b2.state == State.CLOSED

    def test_all_stats_returns_every_breaker(self):
        mb = MultiBreaker()
        mb.get("x")
        mb.get("y")
        stats = mb.all_stats()
        assert set(stats.keys()) == {"x", "y"}

    def test_reset_all(self):
        mb = MultiBreaker(BreakerConfig(failure_threshold=1, window_seconds=60))
        b1 = mb.get("a")
        b1.record_failure("err")
        assert b1.state == State.OPEN
        mb.reset_all()
        assert b1.state == State.CLOSED


class TestEdgeCases:
    def test_zero_threshold_opens_on_first_failure(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=0, window_seconds=60))
        cb.record_failure("err")
        assert cb.state == State.OPEN

    def test_consecutive_failures_tracked(self):
        cb = CircuitBreaker("test", BreakerConfig(failure_threshold=5, window_seconds=60))
        cb.record_failure("a")
        cb.record_failure("b")
        assert cb.stats.consecutive_failures == 2
        cb.record_success()
        assert cb.stats.consecutive_failures == 0
