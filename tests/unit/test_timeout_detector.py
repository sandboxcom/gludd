"""Tests for model timeout detection, classification, and retry with failover.

Covers:
- TimeoutClassifier: classifies exceptions into timeout categories
- ModelHealthTracker: tracks per-model health state and cooldowns
- TimeoutRetryPolicy: decides retry/failover/wait strategies
- Integration with ModelGateway.call_model
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest


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
        kind = TimeoutClassifier.classify(exc, response_body="invalid api key")
        assert kind == TimeoutKind.AUTH_ERROR

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

    def test_rate_limit_does_not_mark_unhealthy(self) -> None:
        from general_ludd.models.timeout_detector import (
            ModelHealthTracker,
            TimeoutEvent,
            TimeoutKind,
        )

        tracker = ModelHealthTracker(failure_threshold=2)
        event = TimeoutEvent(
            model_id="gpt-4",
            kind=TimeoutKind.RATE_LIMITED,
            timestamp=time.monotonic(),
            duration_s=0.5,
        )
        for _ in range(10):
            tracker.record_event(event)
        assert tracker.is_healthy("gpt-4") is True

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

        policy = TimeoutRetryPolicy(base_backoff_seconds=1.0)
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

        policy = TimeoutRetryPolicy()
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
            enabled=True,
            fallback_profiles=["fallback"],
        )
        fallback = ModelProfile(
            model_profile_id="fallback",
            provider="openai",
            model_name="gpt-3.5-turbo",
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
            enabled=True,
            fallback_profiles=["fallback"],
        )
        fallback = ModelProfile(
            model_profile_id="fallback",
            provider="openai",
            model_name="gpt-3.5-turbo",
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
