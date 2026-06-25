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
        # Deterministic jitter (top of window) so the growth check is stable;
        # prod uses random.uniform (see test_jitter_is_randomized below).
        policy = TimeoutRetryPolicy(
            base_backoff_seconds=1.0,
            max_backoff_seconds=300.0,
            jitter_fn=lambda _lo, hi: hi,
        )
        w1 = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=1).wait_seconds
        w2 = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=2).wait_seconds
        assert w2 > w1, f"expected w2 ({w2}) > w1 ({w1})"

    def test_connection_timeout_backoff_double_vs_read_timeout(self) -> None:
        # Deterministic jitter so the exact 2x relationship is reproducible.
        policy = TimeoutRetryPolicy(
            base_backoff_seconds=1.0,
            max_backoff_seconds=300.0,
            failover_after_retries=10,
            jitter_fn=lambda _lo, hi: hi,
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
# TimeoutRetryPolicy — RANDOMIZED jitter (anti-thundering-herd)
# ---------------------------------------------------------------------------

class TestRandomizedJitter:
    def test_jitter_fn_is_invoked(self) -> None:
        """The backoff must DRAW from jitter_fn (not a deterministic formula)."""
        calls: list[tuple[float, float]] = []

        def spy(lo: float, hi: float) -> float:
            calls.append((lo, hi))
            return lo  # deterministic for the assertion below

        policy = TimeoutRetryPolicy(base_backoff_seconds=1.0, jitter_fn=spy)
        policy.decide(TimeoutKind.READ_TIMEOUT, attempt=2)
        assert calls, "jitter_fn was never called — backoff is still deterministic"
        # Equal-jitter window for attempt=2 (exp=2.0) is [0, exp/2=1.0].
        lo, hi = calls[-1]
        assert lo == 0.0
        assert hi == 1.0

    def test_default_jitter_is_random_not_deterministic(self) -> None:
        """With the real (default) random jitter, repeated identical decisions
        do NOT all collapse to one value — that spread is what prevents the
        thundering herd. Use enough draws that an all-equal run is astronomically
        unlikely if jitter is truly random, and impossible-to-pass if it is the
        old deterministic `0.5 + attempt*0.1` formula."""
        policy = TimeoutRetryPolicy(base_backoff_seconds=1.0, max_backoff_seconds=300.0)
        waits = {
            policy.decide(TimeoutKind.READ_TIMEOUT, attempt=3).wait_seconds
            for _ in range(50)
        }
        assert len(waits) > 1, f"backoff is deterministic across draws: {waits}"

    def test_jitter_stays_within_equal_jitter_window(self) -> None:
        """Every randomized backoff must fall in [exp/2, exp] for the attempt."""
        policy = TimeoutRetryPolicy(base_backoff_seconds=1.0, max_backoff_seconds=300.0)
        # attempt=3, READ_TIMEOUT: exp = 1.0 * 2**2 = 4.0 -> window [2.0, 4.0]
        for _ in range(100):
            w = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=3).wait_seconds
            assert 2.0 <= w <= 4.0, f"backoff {w} outside equal-jitter window [2.0, 4.0]"


# ---------------------------------------------------------------------------
# ModelHealthTracker — leaked half-open probe-slot expiry boundary
# ---------------------------------------------------------------------------

class TestProbeExpiryBoundary:
    """The leaked-probe-slot reclamation (is_healthy stale-slot sweep) uses
    `_now - ts >= cooldown_seconds`. This pins that boundary: a leaked slot is
    reclaimed at EXACTLY one cooldown window after admission (consistent with
    the cooldown gate at the same `>=`), neither one tick early nor one late."""

    def _open_and_admit_probe(self, tracker: ModelHealthTracker, t_base: float) -> None:
        for _ in range(3):
            tracker.record_event(_event("m1", TimeoutKind.READ_TIMEOUT, timestamp=t_base))

    def test_leaked_probe_not_reclaimed_just_before_window(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        self._open_and_admit_probe(tracker, t_base)
        # Admit the first probe at t_base+61 (cooldown elapsed). The caller then
        # "leaks" it (never calls record_success/record_event).
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 61.0):
            assert tracker.is_healthy("m1") is True  # probe admitted, slot held
        # 59s later (< one cooldown window since admission @1061): slot NOT yet
        # stale, so a second probe must still be BLOCKED.
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=1061.0 + 59.0):
            assert tracker.is_healthy("m1") is False

    def test_leaked_probe_reclaimed_at_exactly_one_window(self) -> None:
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        t_base = 1000.0
        self._open_and_admit_probe(tracker, t_base)
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=t_base + 61.0):
            assert tracker.is_healthy("m1") is True  # slot admitted @1061
        # Exactly one cooldown window after admission (1061 + 60 = 1121): the
        # leaked slot is reclaimed (>= boundary), so a fresh probe is admitted.
        with patch("general_ludd.models.timeout_detector.time.monotonic", return_value=1061.0 + 60.0):
            assert tracker.is_healthy("m1") is True


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
