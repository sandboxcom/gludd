"""TDD proof: W4.1 — call_model_with_retry uses tenacity as THE retry path.

Proves:
1. retry-then-succeed: primary fails once with retryable error, succeeds on 2nd attempt.
2. exhaustion + fallover: primary fails failover_after times → fallback profile succeeds.
3. non-retryable (AUTH_ERROR) → raises immediately without retry.
4. call_with_tenacity does NOT exist (demo deleted).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest


class TestTenacityIsTheOnlyRetryPath:
    def test_call_with_tenacity_demo_deleted(self) -> None:
        """call_with_tenacity must NOT exist — only one retry implementation."""
        from general_ludd.models.gateway import ModelGateway

        assert not hasattr(ModelGateway, "call_with_tenacity"), (
            "call_with_tenacity (demo) must be deleted: only call_model_with_retry exists"
        )

    def test_call_model_with_retry_exists(self) -> None:
        from general_ludd.models.gateway import ModelGateway

        assert hasattr(ModelGateway, "call_model_with_retry")

    def test_retry_then_succeed_uses_tenacity_internally(self) -> None:
        """Primary fails once with ReadTimeout; succeeds on retry.
        Proves tenacity path handles retry-then-succeed correctly."""
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        profile = ModelProfile(
            model_profile_id="p1", provider="openai", model_name="m1", enabled=True
        )
        gateway = ModelGateway(profiles=[profile], health_tracker=tracker)

        call_count: list[int] = [0]
        good_response = MagicMock()
        good_response.content = "tenacity-success"
        good_response.usage_metadata = {"input_tokens": 3, "output_tokens": 1}

        def mock_invoke(*args: object, **kwargs: object) -> MagicMock:
            call_count[0] += 1
            if call_count[0] == 1:
                raise httpx.ReadTimeout("timeout on first call")
            return good_response

        mock_cls = MagicMock()
        mock_cls.return_value.invoke.side_effect = mock_invoke

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_cls),
            ),
        ):
            result = gateway.call_model_with_retry("p1", [{"role": "user", "content": "hi"}])

        assert result.content == "tenacity-success"
        assert call_count[0] == 2, f"Expected 2 calls (1 retry), got {call_count[0]}"

    def test_exhaustion_falls_over_to_fallback(self) -> None:
        """Primary exhausts failover_after attempts → fallback profile used."""
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        primary = ModelProfile(
            model_profile_id="primary", provider="openai", model_name="gpt-4",
            enabled=True, fallback_profiles=["fallback"],
        )
        fallback = ModelProfile(
            model_profile_id="fallback", provider="openai", model_name="gpt-3.5",
            enabled=True,
        )
        gateway = ModelGateway(profiles=[primary, fallback], health_tracker=tracker)

        fallback_response = MagicMock()
        fallback_response.content = "fallback-result"
        fallback_response.usage_metadata = {"input_tokens": 2, "output_tokens": 1}

        call_tracker: list[int] = [0]

        def get_provider_v2(provider_name: str) -> MagicMock:
            call_tracker[0] += 1
            if call_tracker[0] <= 3:
                m = MagicMock()
                m.return_value.invoke.side_effect = httpx.ReadTimeout("always fails")
                return m
            m = MagicMock()
            m.return_value.invoke.return_value = fallback_response
            return m

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=get_provider_v2,
            ),
        ):
            result = gateway.call_model_with_retry(
                "primary", [{"role": "user", "content": "hi"}]
            )

        assert result.content == "fallback-result"

    def test_non_retryable_auth_error_raises_immediately(self) -> None:
        """AUTH_ERROR (401) → no retry, immediate re-raise."""
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        profile = ModelProfile(
            model_profile_id="auth-test", provider="openai", model_name="m1", enabled=True
        )
        gateway = ModelGateway(profiles=[profile], health_tracker=tracker)

        call_count: list[int] = [0]
        auth_exc = httpx.HTTPStatusError(
            "401 unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )

        def mock_invoke(*args: object, **kwargs: object) -> None:
            call_count[0] += 1
            raise auth_exc

        mock_cls = MagicMock()
        mock_cls.return_value.invoke.side_effect = mock_invoke

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_cls),
            ),
        ), pytest.raises(httpx.HTTPStatusError):
            gateway.call_model_with_retry("auth-test", [{"role": "user", "content": "hi"}])

        assert call_count[0] == 1, (
            f"AUTH_ERROR must not be retried, but got {call_count[0]} call(s)"
        )
