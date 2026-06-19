"""Unit tests for A-05: overload retry cap.

Verifies:
- PROVIDER_ERROR and RATE_LIMITED retry through attempt 10 and exhaust at 11
  (should_failover=True, should_retry=False).
- CONNECTION_TIMEOUT / READ_TIMEOUT still follow the fast-failover path
  (failover_after_retries=3 / max_retries=3).
- Overload backoff never exceeds 120.0 s.
- NON_RETRYABLE kinds (AUTH_ERROR, CONTEXT_LENGTH, INVALID_REQUEST) never retry.
- Default policy: _max_retries==3, _overload_max_retries==10.
"""

from __future__ import annotations

import pytest

from general_ludd.models.timeout_detector import (
    _NON_RETRYABLE_KINDS,
    _OVERLOAD_KINDS,
    TimeoutKind,
    TimeoutRetryPolicy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _policy(**kwargs: object) -> TimeoutRetryPolicy:
    """Return a policy with optional overrides."""
    return TimeoutRetryPolicy(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Default defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_max_retries(self) -> None:
        p = TimeoutRetryPolicy()
        assert p._max_retries == 3

    def test_default_overload_max_retries(self) -> None:
        p = TimeoutRetryPolicy()
        assert p._overload_max_retries == 10

    def test_default_overload_max_backoff(self) -> None:
        p = TimeoutRetryPolicy()
        assert p._overload_max_backoff == 120.0

    def test_default_max_backoff(self) -> None:
        p = TimeoutRetryPolicy()
        assert p._max_backoff == 60.0


# ---------------------------------------------------------------------------
# _OVERLOAD_KINDS membership
# ---------------------------------------------------------------------------

class TestOverloadKindsSet:
    def test_provider_error_is_overload(self) -> None:
        assert TimeoutKind.PROVIDER_ERROR in _OVERLOAD_KINDS

    def test_rate_limited_is_overload(self) -> None:
        assert TimeoutKind.RATE_LIMITED in _OVERLOAD_KINDS

    def test_connection_timeout_not_overload(self) -> None:
        assert TimeoutKind.CONNECTION_TIMEOUT not in _OVERLOAD_KINDS

    def test_read_timeout_not_overload(self) -> None:
        assert TimeoutKind.READ_TIMEOUT not in _OVERLOAD_KINDS


# ---------------------------------------------------------------------------
# Overload retry-through (PROVIDER_ERROR)
# ---------------------------------------------------------------------------

class TestProviderErrorRetry:
    def test_retries_through_attempt_10(self) -> None:
        p = TimeoutRetryPolicy()
        for attempt in range(1, 11):
            decision = p.decide(TimeoutKind.PROVIDER_ERROR, attempt)
            assert decision.should_retry is True, f"attempt {attempt} should retry"
            assert decision.should_failover is False

    def test_exhausts_at_attempt_11(self) -> None:
        p = TimeoutRetryPolicy()
        decision = p.decide(TimeoutKind.PROVIDER_ERROR, 11)
        assert decision.should_retry is False
        assert decision.should_failover is True
        assert "overload max retries exhausted" in decision.reason

    def test_exhausts_beyond_11(self) -> None:
        p = TimeoutRetryPolicy()
        for attempt in range(11, 15):
            decision = p.decide(TimeoutKind.PROVIDER_ERROR, attempt)
            assert decision.should_retry is False
            assert decision.should_failover is True


# ---------------------------------------------------------------------------
# Overload retry-through (RATE_LIMITED)
# ---------------------------------------------------------------------------

class TestRateLimitedRetry:
    def test_retries_through_attempt_10(self) -> None:
        p = TimeoutRetryPolicy()
        for attempt in range(1, 11):
            decision = p.decide(TimeoutKind.RATE_LIMITED, attempt)
            assert decision.should_retry is True, f"attempt {attempt} should retry"

    def test_exhausts_at_attempt_11(self) -> None:
        p = TimeoutRetryPolicy()
        decision = p.decide(TimeoutKind.RATE_LIMITED, 11)
        assert decision.should_retry is False
        assert decision.should_failover is True
        assert "overload max retries exhausted" in decision.reason


# ---------------------------------------------------------------------------
# Fast-failover path: CONNECTION_TIMEOUT and READ_TIMEOUT
# ---------------------------------------------------------------------------

class TestFastFailoverKinds:
    @pytest.mark.parametrize("kind", [TimeoutKind.CONNECTION_TIMEOUT, TimeoutKind.READ_TIMEOUT])
    def test_retries_within_max_retries(self, kind: TimeoutKind) -> None:
        p = TimeoutRetryPolicy(max_retries=3, failover_after_retries=3)
        # Attempts 1 and 2 should retry (below failover_after)
        for attempt in range(1, 3):
            decision = p.decide(kind, attempt)
            assert decision.should_retry is True, f"attempt {attempt} kind {kind} should retry"

    @pytest.mark.parametrize("kind", [TimeoutKind.CONNECTION_TIMEOUT, TimeoutKind.READ_TIMEOUT])
    def test_failovers_at_failover_after(self, kind: TimeoutKind) -> None:
        p = TimeoutRetryPolicy(max_retries=3, failover_after_retries=3)
        # At attempt == failover_after_retries, should failover
        decision = p.decide(kind, 3)
        assert decision.should_failover is True
        assert decision.should_retry is False

    @pytest.mark.parametrize("kind", [TimeoutKind.CONNECTION_TIMEOUT, TimeoutKind.READ_TIMEOUT])
    def test_exhausted_beyond_max_retries(self, kind: TimeoutKind) -> None:
        p = TimeoutRetryPolicy(max_retries=3)
        decision = p.decide(kind, 4)
        assert decision.should_retry is False
        assert decision.should_failover is True


# ---------------------------------------------------------------------------
# Overload backoff cap
# ---------------------------------------------------------------------------

class TestOverloadBackoffCap:
    @pytest.mark.parametrize("kind", [TimeoutKind.PROVIDER_ERROR, TimeoutKind.RATE_LIMITED])
    def test_backoff_never_exceeds_120(self, kind: TimeoutKind) -> None:
        p = TimeoutRetryPolicy()
        for attempt in range(1, 12):
            decision = p.decide(kind, attempt)
            assert decision.wait_seconds <= 120.0, (
                f"attempt {attempt} kind {kind}: wait={decision.wait_seconds} > 120.0"
            )

    def test_non_overload_backoff_still_caps_at_60(self) -> None:
        p = TimeoutRetryPolicy()
        # CONNECTION_TIMEOUT at high attempt numbers must not exceed 60s
        for attempt in range(1, 4):
            backoff = p._compute_backoff(TimeoutKind.CONNECTION_TIMEOUT, attempt, None, overload=False)
            assert backoff <= 60.0, f"attempt {attempt}: backoff={backoff} > 60.0"

    def test_compute_backoff_overload_true_uses_overload_cap(self) -> None:
        p = TimeoutRetryPolicy(max_backoff_seconds=60.0, overload_max_backoff_seconds=120.0)
        # Force a large attempt so base would exceed 60 but should be capped at 120
        backoff_overload = p._compute_backoff(TimeoutKind.PROVIDER_ERROR, 20, None, overload=True)
        backoff_normal = p._compute_backoff(TimeoutKind.PROVIDER_ERROR, 20, None, overload=False)
        assert backoff_overload <= 120.0
        assert backoff_normal <= 60.0
        # overload cap is larger, so overload backoff should be >= normal backoff
        assert backoff_overload >= backoff_normal


# ---------------------------------------------------------------------------
# Non-retryable kinds
# ---------------------------------------------------------------------------

class TestNonRetryableKinds:
    @pytest.mark.parametrize("kind", list(_NON_RETRYABLE_KINDS))
    def test_never_retry(self, kind: TimeoutKind) -> None:
        p = TimeoutRetryPolicy()
        for attempt in range(1, 5):
            decision = p.decide(kind, attempt)
            assert decision.should_retry is False, f"kind={kind} attempt={attempt} should not retry"
            assert decision.should_failover is False


# ---------------------------------------------------------------------------
# Custom overload_max_retries
# ---------------------------------------------------------------------------

class TestCustomOverloadMaxRetries:
    def test_custom_cap_retries_through(self) -> None:
        p = TimeoutRetryPolicy(overload_max_retries=5)
        for attempt in range(1, 6):
            decision = p.decide(TimeoutKind.PROVIDER_ERROR, attempt)
            assert decision.should_retry is True

    def test_custom_cap_exhausts_at_cap_plus_1(self) -> None:
        p = TimeoutRetryPolicy(overload_max_retries=5)
        decision = p.decide(TimeoutKind.PROVIDER_ERROR, 6)
        assert decision.should_retry is False
        assert decision.should_failover is True
