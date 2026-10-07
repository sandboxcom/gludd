"""Compatibility pins for the model-gateway module split."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import general_ludd.models.gateway as gateway
from general_ludd.models import gateway_streaming, gateway_types


def test_gateway_facade_reexports_contract_symbols_by_identity() -> None:
    """Existing imports keep resolving to the original class/function objects."""
    symbols = (
        "BudgetExceededError",
        "CallCancelledError",
        "CircuitBreakerOpenError",
        "CumulativePayloadLimitError",
        "ModelPausedError",
        "ModelProfile",
        "ModelResponse",
        "PayloadLimitError",
        "SSRFRejectionError",
        "StreamLimitError",
        "_RequestPayloadBudget",
        "_RuntimeModelGateway",
        "_attach_correlation_id",
        "_coerce_token_count",
        "_extract_retry_after_seconds",
        "_extract_tool_calls",
        "_is_healthy_with_timeout",
        "_positive_profile_limit",
        "_redact_url_in_exception",
    )

    for symbol in symbols:
        assert getattr(gateway, symbol) is getattr(gateway_types, symbol)


def test_gateway_owns_streaming_through_the_extracted_mixin() -> None:
    """The public class retains every streaming entry point and signature owner."""
    assert gateway_streaming.GatewayStreamingMixin in gateway.ModelGateway.__mro__
    assert (
        gateway.ModelGateway.call_model_stream
        is gateway_streaming.GatewayStreamingMixin.call_model_stream
    )
    assert (
        gateway.ModelGateway.call_model_stream_with_retry
        is gateway_streaming.GatewayStreamingMixin.call_model_stream_with_retry
    )


def test_streaming_keeps_gateway_default_token_tracker_monkeypatch_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Downstream tests patching the historical facade still intercept calls."""
    profile = gateway.ModelProfile(
        model_profile_id="split-stream",
        provider="test-provider",
        model_name="test-model",
        enabled=True,
        api_metered=False,
    )
    chunk = SimpleNamespace(
        content="ok",
        usage_metadata={"input_tokens": 1, "output_tokens": 1},
        response_metadata={},
        tool_calls=[],
    )
    chat_model = MagicMock()
    chat_model.stream.return_value = iter([chunk])
    provider_factory = MagicMock(return_value=chat_model)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = provider_factory
    token_tracker = MagicMock()
    monkeypatch.setattr(gateway, "default_token_tracker", token_tracker)
    model_gateway = gateway.ModelGateway([profile], provider_registry=registry)

    streamed = list(
        model_gateway.call_model_stream(
            "split-stream",
            [{"role": "user", "content": "hello"}],
            work_type="compatibility",
        )
    )

    assert streamed == [chunk]
    token_tracker.return_value.record.assert_called_once_with("compatibility", 1, 1)
