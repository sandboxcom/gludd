"""Tests for model timeout detection, classification, and retry with failover.

Covers:
- TimeoutClassifier: classifies exceptions into timeout categories
- ModelHealthTracker: tracks per-model health state and cooldowns
- TimeoutRetryPolicy: decides retry/failover/wait strategies
- Integration with ModelGateway.call_model
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

_TEST_INPUT_COST_PER_TOKEN = 0.000_001
_TEST_OUTPUT_COST_PER_TOKEN = 0.000_002


class TestTimeoutClassifier:
    def test_classify_connection_timeout(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.ConnectTimeout("connection timed out")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.CONNECTION_TIMEOUT

    def test_classify_read_timeout(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.ReadTimeout("read timed out")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.READ_TIMEOUT

    def test_classify_write_timeout(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.WriteTimeout("write timed out")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.READ_TIMEOUT

    def test_classify_pool_timeout(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.PoolTimeout("pool timed out")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.CONNECTION_TIMEOUT

    def test_classify_connect_error(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.ConnectError("connection refused")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.CONNECTION_TIMEOUT

    def test_classify_generic_timeout(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = TimeoutError("timed out")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.READ_TIMEOUT

    def test_classify_generic_exception(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = RuntimeError("something broke")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.UNKNOWN

    def test_classify_rate_limit_from_status_code(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.HTTPStatusError(
            "rate limited",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.RATE_LIMITED

    def test_classify_server_error_from_status_code(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        for code in (500, 502, 503):
            exc = httpx.HTTPStatusError(
                f"server error {code}",
                request=MagicMock(),
                response=MagicMock(status_code=code),
            )
            kind = TimeoutClassifier.classify(exc)
            assert kind == TimeoutKind.PROVIDER_ERROR, f"expected PROVIDER_ERROR for {code}"

    def test_classify_context_length_from_status_code(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.HTTPStatusError(
            "context length",
            request=MagicMock(),
            response=MagicMock(status_code=400),
        )
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.CONTEXT_LENGTH

    def test_classify_400_other(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.HTTPStatusError(
            "bad request",
            request=MagicMock(),
            response=MagicMock(status_code=400),
        )
        # A generic 400 (not a context-length error) is a non-auth client error.
        # Labeling it AUTH_ERROR would poison health metrics and mislead the
        # operator; auth failures are signaled by 401/403, not a bare 400.
        kind = TimeoutClassifier.classify(exc, response_body="invalid value for 'temperature'")
        assert kind == TimeoutKind.INVALID_REQUEST

    def test_classify_401(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.HTTPStatusError(
            "unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.AUTH_ERROR

    def test_classify_403(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.HTTPStatusError(
            "forbidden",
            request=MagicMock(),
            response=MagicMock(status_code=403),
        )
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.AUTH_ERROR

    def test_classify_context_length_from_body(self) -> None:
        from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

        exc = httpx.HTTPStatusError(
            "bad request",
            request=MagicMock(),
            response=MagicMock(status_code=400),
        )
        kind = TimeoutClassifier.classify(
            exc, response_body="maximum context length exceeded"
        )
        assert kind == TimeoutKind.CONTEXT_LENGTH


class TestModelHealthTracker:
    def test_new_model_is_healthy(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        assert tracker.is_healthy("gpt-4") is True

    def test_record_failure_makes_unhealthy(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker()
        event = TimeoutEvent(
            model_id="gpt-4",
            kind=TimeoutKind.READ_TIMEOUT,
            timestamp=time.monotonic(),
            duration_s=30.0,
        )
        for _ in range(3):
            tracker.record_event(event)
        assert tracker.is_healthy("gpt-4") is False

    def test_cooldown_expires(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        event = TimeoutEvent(
            model_id="gpt-4",
            kind=TimeoutKind.READ_TIMEOUT,
            timestamp=time.monotonic(),
            duration_s=30.0,
        )
        tracker.record_event(event)
        tracker.record_event(event)
        assert tracker.is_healthy("gpt-4") is False
        time.sleep(0.02)
        assert tracker.is_healthy("gpt-4") is True

    def test_rate_limit_marks_unhealthy_within_cooldown(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        # RATE_LIMITED IS retryable-after-cooldown, so a 429'd model must be
        # reported unhealthy (and backed off) until the cooldown elapses, not
        # treated as permanently healthy.
        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=60.0)
        event = TimeoutEvent(
            model_id="gpt-4",
            kind=TimeoutKind.RATE_LIMITED,
            timestamp=time.monotonic(),
            duration_s=0.5,
        )
        for _ in range(10):
            tracker.record_event(event)
        assert tracker.is_healthy("gpt-4") is False

    def test_auth_error_does_not_mark_unhealthy(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2)
        event = TimeoutEvent(
            model_id="gpt-4",
            kind=TimeoutKind.AUTH_ERROR,
            timestamp=time.monotonic(),
            duration_s=0.1,
        )
        tracker.record_event(event)
        tracker.record_event(event)
        assert tracker.is_healthy("gpt-4") is True

    def test_context_length_does_not_mark_unhealthy(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2)
        event = TimeoutEvent(
            model_id="gpt-4",
            kind=TimeoutKind.CONTEXT_LENGTH,
            timestamp=time.monotonic(),
            duration_s=0.1,
        )
        tracker.record_event(event)
        tracker.record_event(event)
        assert tracker.is_healthy("gpt-4") is True

    def test_record_success_resets_failures(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=3)
        event = TimeoutEvent(
            model_id="gpt-4",
            kind=TimeoutKind.READ_TIMEOUT,
            timestamp=time.monotonic(),
            duration_s=30.0,
        )
        tracker.record_event(event)
        tracker.record_event(event)
        tracker.record_success("gpt-4")
        tracker.record_event(event)
        assert tracker.is_healthy("gpt-4") is True

    def test_get_health_status(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2)
        event = TimeoutEvent(
            model_id="gpt-4",
            kind=TimeoutKind.PROVIDER_ERROR,
            timestamp=time.monotonic(),
            duration_s=5.0,
        )
        tracker.record_event(event)
        status = tracker.get_health("gpt-4")
        assert status["consecutive_failures"] == 1
        assert status["total_failures"] == 1
        assert status["last_failure_kind"] == "provider_error"

    def test_get_health_unknown_model(self) -> None:
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        status = tracker.get_health("nonexistent")
        assert status["consecutive_failures"] == 0
        assert status["healthy"] is True

    def test_mixed_failures_count_toward_threshold(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=3)
        tracker.record_event(TimeoutEvent(
            model_id="gpt-4", kind=TimeoutKind.READ_TIMEOUT,
            timestamp=time.monotonic(), duration_s=30.0,
        ))
        tracker.record_event(TimeoutEvent(
            model_id="gpt-4", kind=TimeoutKind.CONNECTION_TIMEOUT,
            timestamp=time.monotonic(), duration_s=10.0,
        ))
        tracker.record_event(TimeoutEvent(
            model_id="gpt-4", kind=TimeoutKind.PROVIDER_ERROR,
            timestamp=time.monotonic(), duration_s=5.0,
        ))
        assert tracker.is_healthy("gpt-4") is False


class TestModelHealthTrackerConcurrency:
    """Concurrency fixes 1 & 2: locked record_event/record_success and an
    atomic, single-flight is_healthy cooldown gate."""

    def test_concurrent_failures_not_lost_breaker_trips(self) -> None:
        """Fix 1: record_event under a barrier-synchronized thundering herd must
        count EVERY failure (no lost updates), so the breaker trips."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        n = 64
        tracker = ModelHealthTracker(failure_threshold=n, cooldown_seconds=60.0)
        barrier = threading.Barrier(n)

        def hammer() -> None:
            barrier.wait()  # force maximal interleave on the read-modify-write
            tracker.record_event(
                TimeoutEvent(
                    model_id="m",
                    kind=TimeoutKind.READ_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=0.0,
                )
            )

        threads = [threading.Thread(target=hammer) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every one of the n failures was counted (no lost update).
        assert tracker.get_health("m")["consecutive_failures"] == n
        assert tracker.get_health("m")["total_failures"] == n
        # With threshold == n, exactly n failures crosses it -> unhealthy.
        assert tracker.is_healthy("m", admit_probe=False) is False

    def test_history_not_corrupted_under_concurrency(self) -> None:
        """Fix 1: concurrent appends + truncation of _history must not race
        (no lost entries, length capped at max_event_history)."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        cap = 50
        n = 200
        tracker = ModelHealthTracker(
            failure_threshold=10_000, cooldown_seconds=60.0, max_event_history=cap
        )
        barrier = threading.Barrier(n)

        def hammer() -> None:
            barrier.wait()
            tracker.record_event(
                TimeoutEvent(
                    model_id="m",
                    kind=TimeoutKind.READ_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=0.0,
                )
            )

        threads = [threading.Thread(target=hammer) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # total counter saw every event; history is truncated to the cap.
        assert tracker.get_health("m")["total_failures"] == n
        assert len(tracker._history["m"]) == cap

    def test_cooldown_admits_only_one_probe(self) -> None:
        """Fix 2: after cooldown elapses, a barrier-synchronized herd of
        is_healthy() callers must admit EXACTLY ONE half-open probe; the rest
        see the breaker still open."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        # cooldown_seconds MUST comfortably exceed the time the 50-thread herd
        # below takes to drain the lock. The single-flight probe slot auto-expires
        # once it is older than ONE cooldown window (the leaked-slot reclaim in
        # is_healthy — see TestProbeSlotAutoExpiry::test_leaked_probe_unblocks_
        # after_cooldown), so if the herd spans more than one window a later caller
        # legitimately reclaims the live probe's slot and admits a SECOND probe —
        # correct for the breaker, but it breaks this test's "exactly one" premise.
        # A tiny (0.01s) cooldown flaked under CI load (assert 2 == 1); a 2.0s
        # window keeps the whole herd inside a single cooldown window.
        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=2.0)
        for _ in range(2):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m",
                    kind=TimeoutKind.READ_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=0.0,
                )
            )
        assert tracker.is_healthy("m", admit_probe=False) is False
        time.sleep(2.1)  # cooldown elapses (must exceed cooldown_seconds above)

        n = 50
        barrier = threading.Barrier(n)
        results: list[bool] = []
        results_lock = threading.Lock()

        def probe() -> None:
            barrier.wait()
            ok = tracker.is_healthy("m")
            with results_lock:
                results.append(ok)

        threads = [threading.Thread(target=probe) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly ONE caller was admitted as the half-open probe.
        assert results.count(True) == 1
        assert results.count(False) == n - 1

    def test_probe_does_not_reset_consecutive(self) -> None:
        """Fix 2: admitting a half-open probe must NOT reset _consecutive to 0 —
        the breaker stays OPEN until record_success (or a failed probe re-arms)."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(3):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m",
                    kind=TimeoutKind.READ_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=0.0,
                )
            )
        time.sleep(0.02)
        # First poll admits the probe...
        assert tracker.is_healthy("m") is True
        # ...but did NOT reset the counter (breaker still open underneath).
        assert tracker._consecutive["m"] == 3
        # A status poll never consumes a probe and reports the open breaker as
        # "would admit" (healthy) without resetting either.
        assert tracker.get_health("m")["consecutive_failures"] == 3

    def test_probe_success_clears_breaker(self) -> None:
        """Fix 2: a successful probe (record_success) clears the breaker and the
        probe-in-flight slot, so the model is healthy again."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(3):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m",
                    kind=TimeoutKind.READ_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=0.0,
                )
            )
        time.sleep(0.02)
        assert tracker.is_healthy("m") is True  # probe admitted
        tracker.record_success("m")             # probe succeeded
        assert tracker.is_healthy("m", admit_probe=False) is True
        assert tracker._consecutive["m"] == 0

    def test_failed_probe_re_arms_and_admits_next_window(self) -> None:
        """Fix 2: if the probe fails (record_event), the probe slot is released
        and re-armed so the NEXT cooldown window can admit a fresh probe."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(2):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m",
                    kind=TimeoutKind.READ_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=0.0,
                )
            )
        time.sleep(0.02)
        assert tracker.is_healthy("m") is True   # window 1 probe admitted
        assert tracker.is_healthy("m") is False  # slot held within window
        # Probe fails -> records a new event, re-arming the breaker.
        tracker.record_event(
            TimeoutEvent(
                model_id="m",
                kind=TimeoutKind.READ_TIMEOUT,
                timestamp=time.monotonic(),
                duration_s=0.0,
            )
        )
        time.sleep(0.02)  # window 2 cooldown elapses
        # A fresh probe is admitted for the new window.
        assert tracker.is_healthy("m") is True

    def test_get_health_does_not_consume_probe(self) -> None:
        """Fix 2: status polls (get_health -> admit_probe=False) must not consume
        the single probe slot, so a real caller can still get it."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(3):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m",
                    kind=TimeoutKind.READ_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=0.0,
                )
            )
        time.sleep(0.02)
        # Several status polls...
        for _ in range(5):
            assert tracker.get_health("m")["healthy"] is True
        # ...did not consume the probe; the real caller still gets it.
        assert tracker.is_healthy("m") is True
        assert tracker.is_healthy("m") is False  # now consumed


class TestTimeoutRetryPolicy:
    def test_should_retry_on_read_timeout(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=1)
        assert decision.should_retry is True

    def test_should_not_retry_on_auth_error(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.AUTH_ERROR, attempt=1)
        assert decision.should_retry is False

    def test_should_not_retry_on_context_length(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.CONTEXT_LENGTH, attempt=1)
        assert decision.should_retry is False

    def test_should_retry_on_rate_limit_with_backoff(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        policy = TimeoutRetryPolicy()
        decision = decision = policy.decide(TimeoutKind.RATE_LIMITED, attempt=1)
        assert decision.should_retry is True
        assert decision.wait_seconds >= 1.0

    def test_should_retry_on_provider_error(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.PROVIDER_ERROR, attempt=1)
        assert decision.should_retry is True

    def test_should_failover_on_repeated_timeouts(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=3)
        assert decision.should_retry is False
        assert decision.should_failover is True

    def test_max_retries_exhausted(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        policy = TimeoutRetryPolicy(max_retries=2)
        decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=3)
        assert decision.should_retry is False

    def test_exponential_backoff(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        # Inject a deterministic jitter_fn (top of the jitter window) so the
        # monotonic-growth assertion is reproducible; prod uses random.uniform.
        policy = TimeoutRetryPolicy(
            base_backoff_seconds=1.0, jitter_fn=lambda _lo, hi: hi,
        )
        d1 = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=1)
        d2 = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=2)
        d3 = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=3)
        assert d2.wait_seconds > d1.wait_seconds
        assert d3.wait_seconds > d2.wait_seconds

    def test_rate_limit_uses_retry_after(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        policy = TimeoutRetryPolicy()
        decision = policy.decide(
            TimeoutKind.RATE_LIMITED, attempt=1, retry_after_seconds=10.0,
        )
        assert decision.wait_seconds >= 10.0

    def test_connection_timeout_retries_with_longer_backoff(self) -> None:
        from general_ludd.models.timeout_detector import (
            TimeoutKind,
            TimeoutRetryPolicy,
        )

        # Deterministic jitter so the CONNECTION>READ comparison is reproducible.
        policy = TimeoutRetryPolicy(jitter_fn=lambda _lo, hi: hi)
        ct_decision = policy.decide(TimeoutKind.CONNECTION_TIMEOUT, attempt=1)
        rt_decision = policy.decide(TimeoutKind.READ_TIMEOUT, attempt=1)
        assert ct_decision.wait_seconds > rt_decision.wait_seconds


class TestGatewayTimeoutIntegration:
    def test_call_model_wraps_with_timeout(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        profile = ModelProfile(
            model_profile_id="test-model",
            provider="openai",
            model_name="gpt-4",
            cost_per_input_token=_TEST_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_TEST_OUTPUT_COST_PER_TOKEN,
            enabled=True,
        )
        gateway = ModelGateway(
            profiles=[profile],
            health_tracker=ModelHealthTracker(),
        )

        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "hello"
        mock_response.usage_metadata = {"input_tokens": 5, "output_tokens": 2}
        mock_provider.return_value.invoke.return_value = mock_response

        with patch.object(
            gateway._registry or MagicMock(),
            "get_provider_class",
            return_value=mock_provider,
        ), patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_provider),
            ),
        ):
            result = gateway.call_model("test-model", [{"role": "user", "content": "hi"}])
            assert result.content == "hello"

    def test_call_model_records_timeout_on_failure(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker(failure_threshold=2)
        profile = ModelProfile(
            model_profile_id="test-model",
            provider="openai",
            model_name="gpt-4",
            cost_per_input_token=_TEST_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_TEST_OUTPUT_COST_PER_TOKEN,
            enabled=True,
        )
        gateway = ModelGateway(
            profiles=[profile],
            health_tracker=tracker,
        )

        mock_provider = MagicMock()
        mock_provider.return_value.invoke.side_effect = httpx.ReadTimeout("timed out")

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_provider),
            ),
        ), pytest.raises(httpx.ReadTimeout):
            gateway.call_model("test-model", [{"role": "user", "content": "hi"}])

        status = tracker.get_health("test-model")
        assert status["consecutive_failures"] == 1
        assert status["last_failure_kind"] == "read_timeout"

    def test_call_model_with_retry_succeeds_on_second_try(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        profile = ModelProfile(
            model_profile_id="test-model",
            provider="openai",
            model_name="gpt-4",
            cost_per_input_token=_TEST_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_TEST_OUTPUT_COST_PER_TOKEN,
            enabled=True,
        )
        gateway = ModelGateway(
            profiles=[profile],
            health_tracker=tracker,
        )

        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "success on retry"
        mock_response.usage_metadata = {"input_tokens": 5, "output_tokens": 2}
        mock_provider.return_value.invoke.side_effect = [
            httpx.ReadTimeout("timed out"),
            mock_response,
        ]

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_provider),
            ),
        ):
            result = gateway.call_model_with_retry(
                "test-model", [{"role": "user", "content": "hi"}],
            )
            assert result.content == "success on retry"

    def test_call_model_with_retry_exhausted_falls_over(self) -> None:
        from general_ludd.models.gateway import ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        profile = ModelProfile(
            model_profile_id="primary",
            provider="openai",
            model_name="gpt-4",
            cost_per_input_token=_TEST_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_TEST_OUTPUT_COST_PER_TOKEN,
            enabled=True,
            fallback_profiles=["fallback"],
        )
        fallback = ModelProfile(
            model_profile_id="fallback",
            provider="openai",
            model_name="gpt-3.5-turbo",
            cost_per_input_token=_TEST_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_TEST_OUTPUT_COST_PER_TOKEN,
            enabled=True,
        )
        from general_ludd.models.gateway import ModelGateway
        gateway = ModelGateway(
            profiles=[profile, fallback],
            health_tracker=tracker,
        )

        primary_provider = MagicMock()
        primary_provider.return_value.invoke.side_effect = httpx.ReadTimeout("timed out")

        fallback_provider = MagicMock()
        fallback_response = MagicMock()
        fallback_response.content = "fallback response"
        fallback_response.usage_metadata = {"input_tokens": 5, "output_tokens": 2}
        fallback_provider.return_value.invoke.return_value = fallback_response

        call_count = [0]

        def get_provider(provider_name: str) -> MagicMock:
            call_count[0] += 1
            if call_count[0] <= 3:
                return primary_provider
            return fallback_provider

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=get_provider,
            ),
        ):
            result = gateway.call_model_with_retry(
                "primary", [{"role": "user", "content": "hi"}],
            )
            assert result.content == "fallback response"
            assert result.model_name == "gpt-3.5-turbo"

    def test_call_model_with_retry_no_fallback_raises(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        profile = ModelProfile(
            model_profile_id="test-model",
            provider="openai",
            model_name="gpt-4",
            cost_per_input_token=_TEST_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_TEST_OUTPUT_COST_PER_TOKEN,
            enabled=True,
        )
        gateway = ModelGateway(
            profiles=[profile],
            health_tracker=tracker,
        )

        mock_provider = MagicMock()
        mock_provider.return_value.invoke.side_effect = httpx.ReadTimeout("timed out")

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_provider),
            ),
        ), pytest.raises(httpx.ReadTimeout):
            gateway.call_model_with_retry(
                "test-model", [{"role": "user", "content": "hi"}],
            )

    def test_unhealthy_model_skipped_by_with_retry(self) -> None:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2)
        event = TimeoutEvent(
            model_id="primary",
            kind=TimeoutKind.READ_TIMEOUT,
            timestamp=time.monotonic(),
            duration_s=30.0,
        )
        tracker.record_event(event)
        tracker.record_event(event)

        primary = ModelProfile(
            model_profile_id="primary",
            provider="openai",
            model_name="gpt-4",
            cost_per_input_token=_TEST_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_TEST_OUTPUT_COST_PER_TOKEN,
            enabled=True,
            fallback_profiles=["fallback"],
        )
        fallback = ModelProfile(
            model_profile_id="fallback",
            provider="openai",
            model_name="gpt-3.5-turbo",
            cost_per_input_token=_TEST_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_TEST_OUTPUT_COST_PER_TOKEN,
            enabled=True,
        )
        gateway = ModelGateway(
            profiles=[primary, fallback],
            health_tracker=tracker,
        )

        fb_provider = MagicMock()
        fb_response = MagicMock()
        fb_response.content = "from fallback"
        fb_response.usage_metadata = {"input_tokens": 5, "output_tokens": 2}
        fb_provider.return_value.invoke.return_value = fb_response

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=fb_provider),
            ),
        ):
            result = gateway.call_model_with_retry(
                "primary", [{"role": "user", "content": "hi"}],
            )
            assert result.model_name == "gpt-3.5-turbo"


class TestProbeSlotAutoExpiry:
    """is_healthy must not wedge permanently when a probe slot leaks.

    Regression for the bug where __probe_in_flight was a bare set[str]:
    if a probe was admitted (is_healthy returned True) but the caller raised
    before record_event/record_success, the slot was never cleared and
    is_healthy returned False forever for that model (breaker wedged open).
    Fix: store admission timestamp in a dict[str, float] and auto-expire
    slots older than one cooldown window inside is_healthy.
    """

    def test_leaked_probe_unblocks_after_cooldown(self) -> None:
        """Admit a probe, never record, advance past cooldown → new probe admitted."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        cooldown = 30.0
        tracker = ModelHealthTracker(
            failure_threshold=2,
            cooldown_seconds=cooldown,
        )
        model_id = "m1"

        # Trip the breaker.
        for _ in range(tracker._failure_threshold):
            tracker.record_event(
                TimeoutEvent(
                    model_id=model_id,
                    kind=TimeoutKind.PROVIDER_ERROR,
                    timestamp=time.monotonic(),
                    duration_s=1.0,
                )
            )

        # Simulate the cooldown elapsing so a probe is possible.
        # We patch time.monotonic so last_failure.timestamp appears old.
        failure_ts = tracker._last_failure[model_id].timestamp
        advanced = failure_ts + cooldown + 1.0  # well past cooldown

        with patch("general_ludd.models.timeout_detector.time") as mock_time:
            mock_time.monotonic.return_value = advanced

            # First call: probe is admitted (slot written).
            first = tracker.is_healthy(model_id, admit_probe=True)
            assert first is True, "probe should be admitted after cooldown"

            # Second call WITHOUT any record_event/record_success → slot still set.
            # This is the leaked-slot scenario.
            second = tracker.is_healthy(model_id, admit_probe=True)
            assert second is False, "probe slot already claimed — second caller must be blocked"

            # Now advance time by another full cooldown so the slot is expired.
            mock_time.monotonic.return_value = advanced + cooldown + 1.0

            # Third call: expired slot must be swept → new probe admitted.
            third = tracker.is_healthy(model_id, admit_probe=True)
            assert third is True, (
                "breaker must unblock after cooldown even without record_event — "
                "leaked probe slot should auto-expire"
            )

    def test_normal_clear_via_record_success_still_works(self) -> None:
        """record_success() must still clear the in-flight slot (no regression)."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=30.0)
        model_id = "m2"

        for _ in range(tracker._failure_threshold):
            tracker.record_event(
                TimeoutEvent(
                    model_id=model_id,
                    kind=TimeoutKind.PROVIDER_ERROR,
                    timestamp=time.monotonic(),
                    duration_s=1.0,
                )
            )

        failure_ts = tracker._last_failure[model_id].timestamp
        advanced = failure_ts + 31.0

        with patch("general_ludd.models.timeout_detector.time") as mock_time:
            mock_time.monotonic.return_value = advanced

            assert tracker.is_healthy(model_id, admit_probe=True) is True

            # Simulate probe succeeded.
            tracker.record_success(model_id)

            # After record_success the consecutive counter is 0 → model is healthy
            # and a subsequent is_healthy call returns True (healthy path, not probe).
            assert tracker.is_healthy(model_id, admit_probe=True) is True

    def test_normal_clear_via_record_event_still_works(self) -> None:
        """record_event() after a failed probe must re-arm the breaker (no regression)."""
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=30.0)
        model_id = "m3"

        for _ in range(tracker._failure_threshold):
            tracker.record_event(
                TimeoutEvent(
                    model_id=model_id,
                    kind=TimeoutKind.PROVIDER_ERROR,
                    timestamp=time.monotonic(),
                    duration_s=1.0,
                )
            )

        failure_ts = tracker._last_failure[model_id].timestamp
        advanced = failure_ts + 31.0

        with patch("general_ludd.models.timeout_detector.time") as mock_time:
            mock_time.monotonic.return_value = advanced

            # Probe admitted.
            assert tracker.is_healthy(model_id, admit_probe=True) is True

            # Probe itself fails → record_event must clear the slot AND re-arm breaker.
            tracker.record_event(
                TimeoutEvent(
                    model_id=model_id,
                    kind=TimeoutKind.PROVIDER_ERROR,
                    timestamp=advanced,
                    duration_s=0.5,
                )
            )

            # Breaker re-armed: is_healthy must return False (cooldown not elapsed yet).
            assert tracker.is_healthy(model_id, admit_probe=True) is False


def test_non_retryable_kinds_constant_single_source() -> None:
    """_NON_RETRYABLE_KINDS is the single source of truth for all three
    decision points: the constant itself, TimeoutRetryPolicy.decide, and the
    gateway retry predicates (which import and use it directly)."""
    from general_ludd.models.timeout_detector import (
        _NON_RETRYABLE_KINDS,
        TimeoutKind,
        TimeoutRetryPolicy,
    )

    # 1. The constant has exactly the expected members.
    assert frozenset(
        {TimeoutKind.AUTH_ERROR, TimeoutKind.CONTEXT_LENGTH, TimeoutKind.INVALID_REQUEST}
    ) == _NON_RETRYABLE_KINDS

    policy = TimeoutRetryPolicy()

    # 2. Every kind IN the constant → should_retry is False (TimeoutRetryPolicy.decide).
    for kind in _NON_RETRYABLE_KINDS:
        decision = policy.decide(kind, 0)
        assert decision.should_retry is False, (
            f"expected should_retry=False for {kind!r} (non-retryable kind)"
        )

    # 3. Every kind NOT in the constant → should_retry is True at attempt 0
    #    (still within max_retries=3, so policy hasn't exhausted retries yet).
    retryable_kinds = [k for k in TimeoutKind if k not in _NON_RETRYABLE_KINDS]
    assert retryable_kinds, "expected at least one retryable kind"
    for kind in retryable_kinds:
        decision = policy.decide(kind, 0)
        assert decision.should_retry is True, (
            f"expected should_retry=True for {kind!r} at attempt 0 (retryable kind)"
        )
