"""D-30 raw-runnable wrapping: enforce payload limits through get_chat_model."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.models.gateway import (
    ModelGateway,
    ModelProfile,
    PayloadLimitError,
)


def _profile(**overrides: Any) -> ModelProfile:
    values: dict[str, Any] = {
        "model_profile_id": "limited",
        "provider": "test-provider",
        "model_name": "test-model",
        "enabled": True,
        "api_metered": False,
        "cost_per_input_token": 0.0,
        "cost_per_output_token": 0.0,
        "max_request_bytes": 4096,
        "max_input_tokens": 4096,
        "max_response_bytes": 4096,
        "max_output_tokens": 4096,
        "max_tool_calls": 64,
    }
    values.update(overrides)
    return ModelProfile(**values)


def _make_gateway(
    profile: ModelProfile,
    *,
    invoke_result: Any = None,
) -> tuple[ModelGateway, MagicMock, MagicMock]:
    chat_model = MagicMock()
    if invoke_result is not None:
        chat_model.invoke.return_value = invoke_result
    else:
        result = MagicMock()
        result.content = "short ok"
        result.usage_metadata = {
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 5,
        }
        chat_model.invoke.return_value = result
    provider_factory = MagicMock(return_value=chat_model)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = provider_factory
    gateway = ModelGateway([profile], provider_registry=registry)
    return gateway, provider_factory, chat_model


_MESSAGES: list[dict[str, str]] = [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# get_chat_model returns a limited runnable
# ---------------------------------------------------------------------------


def test_get_chat_model_runnable_invokes_within_limits() -> None:
    """`.invoke()` through get_chat_model completes when within all limits."""
    gateway, _, chat_model = _make_gateway(_profile())
    runnable = gateway.get_chat_model("limited")
    result = runnable.invoke(_MESSAGES)
    assert result.content == "short ok"
    chat_model.invoke.assert_called_once()


def test_get_chat_model_runnable_rejects_oversize_request_bytes() -> None:
    """`.invoke()` raises PayloadLimitError when request bytes exceed profile."""
    gateway, _, _ = _make_gateway(_profile(max_request_bytes=5))
    runnable = gateway.get_chat_model("limited")
    with pytest.raises(PayloadLimitError) as caught:
        runnable.invoke(_MESSAGES)
    assert caught.value.dimension == "bytes"
    assert caught.value.stage == "request"


def test_get_chat_model_runnable_rejects_oversize_request_tokens() -> None:
    """`.invoke()` raises when conservative UTF-8 token count exceeds limit."""
    gateway, _, _ = _make_gateway(_profile(max_input_tokens=1))
    runnable = gateway.get_chat_model("limited")
    with pytest.raises(PayloadLimitError) as caught:
        runnable.invoke(_MESSAGES)
    assert caught.value.dimension == "tokens"
    assert caught.value.stage == "request"


def test_get_chat_model_runnable_rejects_oversize_response_bytes() -> None:
    """`.invoke()` raises when provider response bytes exceed profile."""
    result = MagicMock()
    result.content = "x" * 500
    result.usage_metadata = {
        "input_tokens": 2,
        "output_tokens": 10,
        "total_tokens": 12,
    }
    gateway, _, _ = _make_gateway(_profile(max_response_bytes=10), invoke_result=result)
    runnable = gateway.get_chat_model("limited")
    with pytest.raises(PayloadLimitError) as caught:
        runnable.invoke(_MESSAGES)
    assert caught.value.dimension == "bytes"
    assert caught.value.stage == "response"


def test_get_chat_model_runnable_rejects_oversize_response_tokens() -> None:
    """`.invoke()` raises when provider usage_metadata tokens exceed profile."""
    result = MagicMock()
    result.content = "ok"
    result.usage_metadata = {
        "input_tokens": 2,
        "output_tokens": 100,
        "total_tokens": 102,
    }
    gateway, _, _ = _make_gateway(_profile(max_output_tokens=10), invoke_result=result)
    runnable = gateway.get_chat_model("limited")
    with pytest.raises(PayloadLimitError) as caught:
        runnable.invoke(_MESSAGES)
    assert caught.value.dimension == "tokens"
    assert caught.value.stage == "response"


def test_get_chat_model_runnable_rejects_oversize_tool_calls() -> None:
    """`.invoke()` raises when provider tool_calls count exceeds profile."""
    result = MagicMock()
    result.content = ""
    result.usage_metadata = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    result.tool_calls = [MagicMock() for _ in range(10)]
    gateway, _, _ = _make_gateway(_profile(max_tool_calls=2), invoke_result=result)
    runnable = gateway.get_chat_model("limited")
    with pytest.raises(PayloadLimitError) as caught:
        runnable.invoke(_MESSAGES)
    assert caught.value.dimension == "tool_calls"
    assert caught.value.stage == "response"


def test_get_chat_model_runnable_does_not_bill_or_cache() -> None:
    """get_chat_model path does NOT bill, cache, or record metrics."""
    budget = MagicMock()
    cache = MagicMock()
    metrics = MagicMock()
    gateway, _, _chat_model = _make_gateway(_profile())
    gateway._budget_guard = budget
    gateway._response_cache = cache
    gateway._metrics_collector = metrics
    gateway._metrics_agent_id = "agent-1"

    runnable = gateway.get_chat_model("limited")
    result = runnable.invoke(_MESSAGES)

    assert result.content == "short ok"
    budget.record_spend.assert_not_called()
    cache.get.assert_not_called()
    cache.set.assert_not_called()
    metrics.record_model_call.assert_not_called()


def test_get_chat_model_runnable_fails_closed_on_absent_profile() -> None:
    """get_chat_model preserves original ValueError for missing profiles."""
    gateway, _, _ = _make_gateway(_profile())
    with pytest.raises(ValueError, match="not found"):
        gateway.get_chat_model("missing")


def test_get_chat_model_runnable_fails_closed_on_uninstalled_provider() -> None:
    """get_chat_model preserves original ImportError for uninstalled providers."""
    gateway, _, _ = _make_gateway(_profile())
    gateway._registry = MagicMock()
    gateway._registry.is_installed.return_value = False
    gateway._registry.get_provider_class.side_effect = RuntimeError("no class")
    with pytest.raises(ImportError):
        gateway.get_chat_model("limited")
