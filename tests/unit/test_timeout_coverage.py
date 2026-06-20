"""Additional coverage tests for src/general_ludd/models/timeout_detector.py.

Targets branches and paths not already covered by test_timeout_detector.py:
- TimeoutClassifier._build_type_map lazy init (called twice)
- TimeoutClassifier.classify: all exception types including 529, UNKNOWN
- ModelHealthTracker: default params, history capping, probe in-flight discard
- ModelHealthTracker.is_healthy: admit_probe=False status-poll path
- ModelHealthTracker.get_health: returns correct keys
- TimeoutRetryPolicy.decide: INVALID_REQUEST non-retryable, overload beyond limit,
  failover_after boundary, regular beyond max_retries
- TimeoutRetryPolicy._compute_backoff: RATE_LIMITED with/without retry_after,
  CONNECTION_TIMEOUT double multiplier, overload cap, non-overload cap
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import pytest


# ---------------------------------------------------------------------------
# TimeoutClassifier._build_type_map (lazy init)
# ---------------------------------------------------------------------------

class TestBuildTypeMap:
    def test_build_type_map_lazy_init_twice(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier
        # First call populates; second call returns cached
        map1 = TimeoutClassifier._build_type_map()
        map2 = TimeoutClassifier._build_type_map()
        assert map1 is map2
        assert len(map1) > 0

    def test_build_type_map_contains_expected_types(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        type_map = TimeoutClassifier._build_type_map()
        assert httpx.ConnectTimeout in type_map
        assert type_map[httpx.ConnectTimeout] == TimeoutKind.CONNECTION_TIMEOUT
        assert httpx.ReadTimeout in type_map
        assert type_map[httpx.ReadTimeout] == TimeoutKind.READ_TIMEOUT


# ---------------------------------------------------------------------------
# TimeoutClassifier.classify — additional branches
# ---------------------------------------------------------------------------

class TestTimeoutClassifierAdditional:
    def test_classify_529_is_provider_error(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.HTTPStatusError(
            "overloaded",
            request=MagicMock(),
            response=MagicMock(status_code=529),
        )
        assert TimeoutClassifier.classify(exc) == TimeoutKind.PROVIDER_ERROR

    def test_classify_500_is_provider_error(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.HTTPStatusError(
            "internal server error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        assert TimeoutClassifier.classify(exc) == TimeoutKind.PROVIDER_ERROR

    def test_classify_502_is_provider_error(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.HTTPStatusError(
            "bad gateway",
            request=MagicMock(),
            response=MagicMock(status_code=502),
        )
        assert TimeoutClassifier.classify(exc) == TimeoutKind.PROVIDER_ERROR

    def test_classify_400_with_context_length_body(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.HTTPStatusError(
            "bad request",
            request=MagicMock(),
            response=MagicMock(status_code=400),
        )
        kind = TimeoutClassifier.classify(exc, response_body="too many tokens in request")
        assert kind == TimeoutKind.CONTEXT_LENGTH

    def test_classify_400_context_length_exceeded_pattern(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.HTTPStatusError(
            "context_length_exceeded",
            request=MagicMock(),
            response=MagicMock(status_code=400),
        )
        # exc string contains context_length_exceeded
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.CONTEXT_LENGTH

    def test_classify_400_no_pattern_is_invalid_request(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.HTTPStatusError(
            "bad param",
            request=MagicMock(),
            response=MagicMock(status_code=400),
        )
        kind = TimeoutClassifier.classify(exc, response_body="invalid value for temperature")
        assert kind == TimeoutKind.INVALID_REQUEST

    def test_classify_unknown_status_code_is_unknown(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.HTTPStatusError(
            "teapot",
            request=MagicMock(),
            response=MagicMock(status_code=418),
        )
        assert TimeoutClassifier.classify(exc) == TimeoutKind.UNKNOWN

    def test_classify_unknown_exception_type(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = ValueError("something unexpected")
        assert TimeoutClassifier.classify(exc) == TimeoutKind.UNKNOWN

    def test_classify_timeout_error_builtin(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = TimeoutError("generic timeout")
        assert TimeoutClassifier.classify(exc) == TimeoutKind.READ_TIMEOUT

    def test_classify_pool_timeout(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.PoolTimeout("pool exhausted")
        assert TimeoutClassifier.classify(exc) == TimeoutKind.CONNECTION_TIMEOUT

    def test_classify_connect_error(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.ConnectError("connection refused")
        assert TimeoutClassifier.classify(exc) == TimeoutKind.CONNECTION_TIMEOUT

    def test_classify_write_timeout(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind
        exc = httpx.WriteTimeout("write stalled")
        assert TimeoutClassifier.classify(exc) == TimeoutKind.READ_TIMEOUT


# ---------------------------------------------------------------------------
# ModelHealthTracker — additional paths
# ---------------------------------------------------------------------------

class TestModelHealthTrackerAdditional:
    def test_default_params(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker
        tracker = ModelHealthTracker()
        assert tracker._failure_threshold == 3
        assert tracker._cooldown_seconds == 60.0
        assert tracker._max_event_history == 100

    def test_custom_params(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker
        tracker = ModelHealthTracker(failure_threshold=5, cooldown_seconds=30.0, max_event_history=50)
        assert tracker._failure_threshold == 5
        assert tracker._cooldown_seconds == 30.0
        assert tracker._max_event_history == 50

    def test_history_capped_at_max_event_history(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker(max_event_history=5, failure_threshold=100)
        for i in range(10):
            tracker.record_event(TimeoutEvent(
                model_id="m", kind=TimeoutKind.READ_TIMEOUT,
                timestamp=time.monotonic(), duration_s=0.0,
            ))
        assert len(tracker._history["m"]) == 5

    def test_record_event_increments_consecutive_and_total(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker()
        ev = TimeoutEvent(model_id="m", kind=TimeoutKind.PROVIDER_ERROR, timestamp=time.monotonic(), duration_s=0.0)
        tracker.record_event(ev)
        tracker.record_event(ev)
        assert tracker._consecutive.get("m") == 2
        assert tracker._total.get("m") == 2

    def test_record_event_stores_last_failure(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker()
        ev = TimeoutEvent(model_id="m", kind=TimeoutKind.AUTH_ERROR, timestamp=time.monotonic(), duration_s=0.0)
        tracker.record_event(ev)
        assert tracker._last_failure.get("m") is ev

    def test_record_event_discards_probe_in_flight(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        ev = TimeoutEvent(model_id="m", kind=TimeoutKind.READ_TIMEOUT, timestamp=time.monotonic(), duration_s=0.0)
        tracker.record_event(ev)
        tracker.record_event(ev)
        time.sleep(0.02)
        # Admit probe
        assert tracker.is_healthy("m") is True
        # Now record another event — should discard the probe in flight
        tracker.record_event(ev)
        time.sleep(0.02)
        # After new cooldown elapses, a fresh probe should be admitted
        assert tracker.is_healthy("m") is True

    def test_record_success_resets_consecutive_to_zero(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker()
        ev = TimeoutEvent(model_id="m", kind=TimeoutKind.READ_TIMEOUT, timestamp=time.monotonic(), duration_s=0.0)
        tracker.record_event(ev)
        tracker.record_event(ev)
        tracker.record_success("m")
        assert tracker._consecutive.get("m") == 0

    def test_record_success_discards_probe_in_flight(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        ev = TimeoutEvent(model_id="m", kind=TimeoutKind.READ_TIMEOUT, timestamp=time.monotonic(), duration_s=0.0)
        tracker.record_event(ev)
        tracker.record_event(ev)
        time.sleep(0.02)
        tracker.is_healthy("m")  # admit probe
        tracker.record_success("m")
        # After success, breaker fully cleared
        assert tracker.is_healthy("m", admit_probe=False) is True

    def test_is_healthy_admit_probe_false_status_poll(self) -> None:
        """admit_probe=False never consumes the probe slot."""
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        ev = TimeoutEvent(model_id="m", kind=TimeoutKind.READ_TIMEOUT, timestamp=time.monotonic(), duration_s=0.0)
        tracker.record_event(ev)
        tracker.record_event(ev)
        time.sleep(0.02)
        # Status poll: admit_probe=False should return True (cooldown elapsed) without consuming slot
        assert tracker.is_healthy("m", admit_probe=False) is True
        # Real probe should still be available
        assert tracker.is_healthy("m") is True
        # Second real probe consumed → False
        assert tracker.is_healthy("m") is False

    def test_is_healthy_below_threshold_is_true(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker(failure_threshold=5)
        for _ in range(3):
            tracker.record_event(TimeoutEvent(
                model_id="m", kind=TimeoutKind.READ_TIMEOUT,
                timestamp=time.monotonic(), duration_s=0.0,
            ))
        assert tracker.is_healthy("m") is True

    def test_get_health_returns_all_keys(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker()
        ev = TimeoutEvent(model_id="m", kind=TimeoutKind.RATE_LIMITED, timestamp=time.monotonic(), duration_s=0.5)
        tracker.record_event(ev)
        h = tracker.get_health("m")
        assert "model_id" in h
        assert "healthy" in h
        assert "consecutive_failures" in h
        assert "total_failures" in h
        assert "last_failure_kind" in h
        assert "last_failure_at" in h
        assert h["last_failure_kind"] == "rate_limited"

    def test_get_health_no_events_returns_none_last_failure(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker
        tracker = ModelHealthTracker()
        h = tracker.get_health("never-seen")
        assert h["last_failure_kind"] is None
        assert h["last_failure_at"] is None

    def test_is_healthy_cooldown_not_elapsed_returns_false(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker, TimeoutEvent, TimeoutKind
        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=3600.0)
        ev = TimeoutEvent(model_id="m", kind=TimeoutKind.PROVIDER_ERROR, timestamp=time.monotonic(), duration_s=0.0)
        tracker.record_event(ev)
        tracker.record_event(ev)
        # Cooldown is 3600s so it hasn't elapsed
        assert tracker.is_healthy("m") is False


# ---------------------------------------------------------------------------
# TimeoutRetryPolicy.decide — additional branches
# ---------------------------------------------------------------------------

class TestTimeoutRetryPolicyAdditional:
    def test_invalid_request_is_not_retryable(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.INVALID_REQUEST, attempt=1)
        assert decision.should_retry is False

    def test_overload_within_limit_retries(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(overload_max_retries=10)
        decision = policy.decide(TimeoutKind.PROVIDER_ERROR, attempt=5)
        assert decision.should_retry is True

    def test_overload_beyond_limit_fails_over(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(overload_max_retries=3)
        decision = policy.decide(TimeoutKind.PROVIDER_ERROR, attempt=4)
        assert decision.should_retry is False
        assert decision.should_failover is True

    def test_rate_limited_beyond_overload_fails_over(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(overload_max_retries=2)
        decision = policy.decide(TimeoutKind.RATE_LIMITED, attempt=3)
        assert decision.should_retry is False
        assert decision.should_failover is True

    def test_regular_at_failover_after_boundary(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(max_retries=5, failover_after_retries=3)
        # attempt == failover_after → should_failover=True, should_retry=False
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=3)
        assert decision.should_retry is False
        assert decision.should_failover is True

    def test_regular_beyond_max_retries_fails_over(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(max_retries=2)
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=3)
        assert decision.should_retry is False
        assert decision.should_failover is True

    def test_regular_within_max_retries_retries(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(max_retries=5, failover_after_retries=10)
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=2)
        assert decision.should_retry is True

    def test_context_length_not_retryable(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.CONTEXT_LENGTH, attempt=1)
        assert decision.should_retry is False
        assert "not retryable" in decision.reason


# ---------------------------------------------------------------------------
# TimeoutRetryPolicy._compute_backoff
# ---------------------------------------------------------------------------

class TestComputeBackoff:
    def test_rate_limited_with_retry_after_returns_max_of_retry_after_and_1(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy()
        result = policy._compute_backoff(TimeoutKind.RATE_LIMITED, attempt=1, retry_after=15.0)
        assert result == 15.0

    def test_rate_limited_with_small_retry_after_returns_at_least_1(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy()
        result = policy._compute_backoff(TimeoutKind.RATE_LIMITED, attempt=1, retry_after=0.01)
        assert result >= 1.0

    def test_rate_limited_no_retry_after_base_at_least_1(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(base_backoff_seconds=0.001)
        result = policy._compute_backoff(TimeoutKind.RATE_LIMITED, attempt=1, retry_after=None)
        assert result >= 1.0

    def test_connection_timeout_doubles_base(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(base_backoff_seconds=1.0)
        ct_result = policy._compute_backoff(TimeoutKind.CONNECTION_TIMEOUT, attempt=1, retry_after=None)
        rt_result = policy._compute_backoff(TimeoutKind.READ_TIMEOUT, attempt=1, retry_after=None)
        assert ct_result == pytest.approx(rt_result * 2.0, rel=1e-6)

    def test_overload_uses_overload_max_backoff_cap(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(
            base_backoff_seconds=100.0,
            max_backoff_seconds=60.0,
            overload_max_backoff_seconds=120.0,
        )
        result = policy._compute_backoff(
            TimeoutKind.PROVIDER_ERROR, attempt=10, retry_after=None, overload=True
        )
        assert result <= 120.0

    def test_non_overload_uses_max_backoff_cap(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(
            base_backoff_seconds=100.0,
            max_backoff_seconds=30.0,
            overload_max_backoff_seconds=120.0,
        )
        result = policy._compute_backoff(
            TimeoutKind.READ_TIMEOUT, attempt=10, retry_after=None, overload=False
        )
        assert result <= 30.0

    def test_jitter_grows_with_attempt(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutKind, TimeoutRetryPolicy
        policy = TimeoutRetryPolicy(base_backoff_seconds=1.0, max_backoff_seconds=10000.0)
        b1 = policy._compute_backoff(TimeoutKind.READ_TIMEOUT, attempt=1, retry_after=None)
        b2 = policy._compute_backoff(TimeoutKind.READ_TIMEOUT, attempt=2, retry_after=None)
        b3 = policy._compute_backoff(TimeoutKind.READ_TIMEOUT, attempt=3, retry_after=None)
        assert b2 > b1
        assert b3 > b2
