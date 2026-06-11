"""V3.1 full: Replace hand-rolled retry in call_model_with_retry with tenacity.

The hand-rolled while loop at gateway.py:286-326 is replaced by tenacity.retry.
Health tracking and failover are preserved as pre-retry checks.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import tenacity

from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse


class TestTenacityReplacement:
    def test_tenacity_retries_on_exception(self):
        call_count = {"count": 0}

        @tenacity.retry(
            stop=tenacity.stop_after_attempt(4),
            wait=tenacity.wait_fixed(0.01),
            retry=tenacity.retry_if_exception_type(ValueError),
            reraise=True,
        )
        def flaky():
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise ValueError("transient")
            return "result"

        result = flaky()
        assert result == "result"
        assert call_count["count"] == 3

    def test_tenacity_stops_after_max_retries(self):
        call_count = {"count": 0}

        @tenacity.retry(
            stop=tenacity.stop_after_attempt(3),
            wait=tenacity.wait_fixed(0.01),
            reraise=True,
        )
        def always_fails():
            call_count["count"] += 1
            raise ValueError("always")

        import pytest
        with pytest.raises(ValueError):
            always_fails()
        assert call_count["count"] == 3

    def test_gateway_call_with_tenacity(self):
        profile = ModelProfile(
            model_profile_id="m1",
            provider="openai",
            provider_package="lp",
            provider_class_hint="COAI",
            model_name="gt",
            enabled=True,
        )
        gateway = ModelGateway(profiles=[profile])
        mock_response = ModelResponse(content="tenacity result")
        gateway.call_model = MagicMock(return_value=mock_response)

        result = gateway.call_with_tenacity("m1", [{"role": "user", "content": "hi"}])
        assert result.content == "tenacity result"
        gateway.call_model.assert_called_once()
