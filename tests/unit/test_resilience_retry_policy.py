"""Unit tests for TimeoutRetryPolicy, ModelHealthTracker, and TimeoutClassifier."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutClassifier,
    TimeoutEvent,
    TimeoutKind,
    TimeoutRetryPolicy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(model_id: str, kind: TimeoutKind, timestamp: float = 0.0) -> TimeoutEvent:
    return TimeoutEvent(model_id=model_id, kind=kind, timestamp=timestamp, duration_s=0.1)


# ---------------------------------------------------------------------------
# TimeoutRetryPolicy — non-retryable kinds
# ---------------------------------------------------------------------------

class TestNonRetryableKinds:
    @pytest.mark.parametrize("kind", [
        TimeoutKind.AUTH_ERROR,
        TimeoutKind.CONTEXT_LENGTH,
        TimeoutKind.INVALID_REQUEST,
    ])
    def test_non_retryable_no_retry_no_failover(self, kind: TimeoutKind) -> None:
        policy = TimeoutRetryPolicy()
        decision = policy.decide(kind, attempt=1)
        assert decision.should_retry is False
        assert decision.should_failover is False

    @pytest.mark.parametrize("kind", [
        TimeoutKind.AUTH_ERROR,
        TimeoutKind.CONTEXT_LENGTH,
        TimeoutKind.INVALID_REQUEST,
    ])
    def test_non_retryable_reason_contains_kind(self, kind: TimeoutKind) -> None:
        policy = TimeoutRetryPolicy()
        decision = policy.decide(kind, attempt=1)
        assert kind.value in decision.reason


# ---------------------------------------------------------------------------
# TimeoutRetryPolicy — retryable (non-overload) kinds
# ---------------------------------------------------------------------------

class TestRetryableKinds:
    def test_read_timeout_attempt1_retries(self) -> None:
        policy = TimeoutRetryPolicy(max_retries=3, failover_after_retries=3)
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=1)
        assert decision.should_retry is True
        assert decision.should_failover is False

    def test_read_timeout_attempt2_retries(self) -> None:
        policy = TimeoutRetryPolicy(max_retries=3, failover_after_retries=3)
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=2)
        assert decision.should_retry is True

    def test_exhausted_max_retries_fails_over(self) -> None:
        # attempt=4 > max_retries=3 → failover
        policy = TimeoutRetryPolicy(max_retries=3)
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=4)
        assert decision.should_retry is False
        assert decision.should_failover is True

    def test_failover_at_failover_after_threshold(self) -> None:
        # attempt=3 >= failover_after=3 → failover (before max_retries check)
        policy = TimeoutRetryPolicy(max_retries=5, failover_after_retries=3)
        decision = policy.decide(TimeoutKind.CONNECTION_TIMEOUT, attempt=3)
        assert decision.should_failover is True
        assert decision.should_retry is False

    def test_unknown_kind_retries_below_max(self) -> None:
        policy = TimeoutRetryPolicy(max_retries=3, failover_after_retries=3)
        decision = policy.decide(TimeoutKind.UNKNOWN, attempt=1)
        assert decision.should_retry is True


# ---------------------------------------------------------------------------
# TimeoutRetryPolicy — overload kinds
# ---------------------------------------------------------------------------

class TestOverloadKinds:
    def test_provider_error_attempt1_retries(self) -> None:
        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.PROVIDER_ERROR, attempt=1)
        assert decision.should_retry is True
        assert decision.should_failover is False

    def test_rate_limited_attempt1_retries(self) -> None:
        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.RATE_LIMITED, attempt=1)
        assert decision.should_retry is True

    def test_overload_exhausts_at_overload_max(self) -> None:
        # attempt=11 > overload_max_retries=10 → failover
        policy = TimeoutRetryPolicy(overload_max_retries=10)
        decision = policy.decide(TimeoutKind.PROVIDER_ERROR, attempt=11)
        assert decision.should_retry is False
        assert decision.should_failover is True

    def test_overload_does_not_failover_below_overload_max(self) -> None:
        policy = TimeoutRetryPolicy(overload_max_retries=10)
        decision = policy.decide(TimeoutKind.PROVIDER_ERROR, attempt=10)
        assert decision.should_retry is True

    def test_overload_does_not_use_normal_failover_after(self) -> None:
        # failover_after=2 should NOT apply to overload kinds
        policy = TimeoutRetryPolicy(failover_after_retries=2, overload_max_retries=10)
        decision = policy.decide(TimeoutKind.RATE_LIMITED, attempt=5)
        assert decision.should_retry is True
        assert decision.should_failover is False


# ---------------------------------------------------------------------------
# TimeoutRetryPolicy — backoff computation
# ---------------------------------------------------------------------------

class TestBackoffComputation:
    def test_backoff_grows_with_attempt(self) -> None:
        policy = TimeoutRetryPolicy(base_backoff_seconds=1.0, max_backoff_seconds=300.0)
        w1 = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=1).wait_seconds
        w2 = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=2).wait_seconds
        assert w2 > w1, f"expected w2 ({w2}) > w1 ({w1})"

    def test_connection_timeout_backoff_double_vs_read_timeout(self) -> None:
        policy = TimeoutRetryPolicy(
            base_backoff_seconds=1.0,
            max_backoff_seconds=300.0,
            failover_after_retries=10,
        )
        wc = policy.decide(TimeoutKind.CONNECTION_TIMEOUT, attempt=1).wait_seconds
        wr = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=1).wait_seconds
        # CONNECTION_TIMEOUT applies a 2x multiplier
        assert abs(wc - 2.0 * wr) < 1e-9, f"wc={wc}, wr={wr}"

    def test_rate_limited_with_retry_after_uses_retry_after(self) -> None:
        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.RATE_LIMITED, attempt=1, retry_after_seconds=30.0)
        assert decision.wait_seconds >= 30.0

    def test_rate_limited_without_retry_after_backoff_gte_1(self) -> None:
        policy = TimeoutRetryPolicy(overload_max_retries=10)
        decision = policy.decide(TimeoutKind.RATE_LIMITED, attempt=1)
        assert decision.wait_seconds >= 1.0

    def test_backoff_capped_at_max_backoff(self) -> None:
        policy = TimeoutRetryPolicy(
            base_backoff_seconds=10.0,
            max_backoff_seconds=5.0,
            failover_after_retries=20,
            max_retries=20,
        )
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=5)
        assert decision.wait_seconds <= 5.0

    def test_overload_backoff_capped_at_overload_max(self) -> None:
        policy = TimeoutRetryPolicy(
            base_backoff_seconds=10.0,
            overload_max_backoff_seconds=7.0,
            overload_max_retries=20,
        )
        decision = policy.decide(TimeoutKind.PROVIDER_ERROR, attempt=5)
        assert decision.wait_seconds <= 7.0


# ---------------------------------------------------------------------------
# ModelHealthTracker — circuit open/half-open/closed
# ---------------------------------------------------------------------------

class TestModelHealthTracker:
    def test_healthy_with_no_failures(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3)
        assert tracker.is_healthy("m1") is True

    def test_healthy_below_threshold(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3)
        for _ in range(2):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT, timestamp=0.0))
        assert tracker.is_healthy("m1") is True

    def test_open_at_threshold(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        for _ in range(3):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT, timestamp=t_base))
        # Still within cooldown → open
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 10.0):
            assert tracker.is_healthy("m1") is False

    def test_half_open_first_caller_admitted_after_cooldown(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        for _ in range(3):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT, timestamp=t_base))
        # Cooldown elapsed → first probe admitted
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 61.0):
            assert tracker.is_healthy("m1") is True

    def test_half_open_second_caller_rejected(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        for _ in range(3):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT, timestamp=t_base))
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 61.0):
            assert tracker.is_healthy("m1") is True   # consumes probe slot
            assert tracker.is_healthy("m1") is False  # second caller blocked

    def test_record_success_resets_to_healthy(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        for _ in range(3):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT, timestamp=t_base))
        tracker.record_success("m1")
        assert tracker.is_healthy("m1") is True

    def test_record_success_clears_probe_in_flight(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        for _ in range(3):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT, timestamp=t_base))
        # Admit first probe, then succeed — probe slot cleared
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 61.0):
            tracker.is_healthy("m1")  # consume probe slot
        tracker.record_success("m1")
        # After success, is_healthy should be True (consecutive reset to 0)
        assert tracker.is_healthy("m1") is True

    def test_auth_error_failures_do_not_open_circuit(self) -> None:
        # AUTH_ERROR is non-retryable: the tracker records the event but
        # is_healthy bypasses the circuit-breaker for AUTH_ERROR last failure.
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        for _ in range(5):
            tracker.record_event(_event("m1", TimeoutKind.AUTH_ERROR, timestamp=t_base))
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 1.0):
            assert tracker.is_healthy("m1") is True

    def test_context_length_failures_do_not_open_circuit(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        for _ in range(5):
            tracker.record_event(_event("m1", TimeoutKind.CONTEXT_LENGTH, timestamp=t_base))
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 1.0):
            assert tracker.is_healthy("m1") is True

    def test_get_health_returns_required_keys(self) -> None:
        tracker = ModelHealthTracker()
        health = tracker.get_health("m1")
        assert "model_id" in health
        assert "healthy" in health
        assert "consecutive_failures" in health
        assert "total_failures" in health
        assert "last_failure_kind" in health
        assert "last_failure_at" in health

    def test_get_health_does_not_consume_probe(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        for _ in range(3):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT, timestamp=t_base))
        # Call get_health many times — should never exhaust the probe slot
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 61.0):
            for _ in range(5):
                tracker.get_health("m1")
            # Probe slot still available for real is_healthy
            assert tracker.is_healthy("m1") is True

    def test_get_health_model_id_matches(self) -> None:
        tracker = ModelHealthTracker()
        health = tracker.get_health("my-model")
        assert health["model_id"] == "my-model"

    def test_total_failures_count(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=10)
        for _ in range(4):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT))
        assert tracker.get_health("m1")["total_failures"] == 4

    def test_consecutive_resets_on_success(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=10)
        for _ in range(4):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT))
        tracker.record_success("m1")
        assert tracker.get_health("m1")["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# TimeoutClassifier — stdlib-only branch
# ---------------------------------------------------------------------------

class TestTimeoutClassifier:
    def test_timeout_error_classified_as_read_timeout(self) -> None:
        exc = TimeoutError("timed out")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.READ_TIMEOUT
