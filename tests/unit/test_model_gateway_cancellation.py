"""D-30 buffered-call cancellation: typed rejection before provider invoke."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.models.gateway import (
    CallCancelledError,
    ModelGateway,
    ModelProfile,
    PayloadLimitError,
)


def _profile(**overrides: Any) -> ModelProfile:
    values: dict[str, Any] = {
        "model_profile_id": "cancellable",
        "provider": "test-provider",
        "model_name": "test-model",
        "enabled": True,
        "api_metered": False,
        "cost_per_input_token": 0.0,
        "cost_per_output_token": 0.0,
    }
    values.update(overrides)
    return ModelProfile(**values)


_MESSAGES: list[dict[str, str]] = [{"role": "user", "content": "hello"}]


def _make_gateway(profile: ModelProfile) -> tuple[ModelGateway, MagicMock]:
    chat_model = MagicMock()
    result = MagicMock()
    result.content = "response"
    result.usage_metadata = {
        "input_tokens": 5,
        "output_tokens": 3,
        "total_tokens": 8,
    }
    chat_model.invoke.return_value = result
    provider_factory = MagicMock(return_value=chat_model)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = provider_factory
    gateway = ModelGateway([profile], provider_registry=registry)
    return gateway, chat_model


# ---------------------------------------------------------------------------
# Cancellation rejection before provider construction
# ---------------------------------------------------------------------------


def test_already_set_event_raises_before_provider_invoke() -> None:
    """A pre-set cancellation event rejects the call before .invoke()."""
    gateway, chat_model = _make_gateway(_profile())
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(CallCancelledError) as caught:
        gateway.call_model(
            "cancellable",
            _MESSAGES,
            cancellation_event=cancelled,
        )

    assert caught.value.profile_id == "cancellable"
    chat_model.invoke.assert_not_called()


def test_unset_event_allows_normal_call() -> None:
    """An unset cancellation event does not interfere with normal operation."""
    gateway, chat_model = _make_gateway(_profile())
    active = threading.Event()

    response = gateway.call_model(
        "cancellable",
        _MESSAGES,
        cancellation_event=active,
    )

    assert response.content == "response"
    chat_model.invoke.assert_called_once()


def test_no_event_behaviour_unchanged() -> None:
    """Omitting cancellation_event preserves backward-compatible behaviour."""
    gateway, _chat_model = _make_gateway(_profile())
    response = gateway.call_model("cancellable", _MESSAGES)
    assert response.content == "response"


def test_cancellation_before_budget_check_does_not_leak() -> None:
    """Pre-set cancellation returns immediately without budget guard evaluation."""
    budget = MagicMock()
    gateway, _ = _make_gateway(_profile())
    gateway._budget_guard = budget
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(CallCancelledError):
        gateway.call_model(
            "cancellable",
            _MESSAGES,
            cancellation_event=cancelled,
            estimated_cost=999.0,
            budget_remaining=0.01,
        )

    budget.record_spend.assert_not_called()


def test_cancellation_before_cache_read() -> None:
    """Pre-set cancellation skips cache lookup entirely."""
    cache = MagicMock()
    gateway, _ = _make_gateway(_profile())
    gateway._response_cache = cache
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(CallCancelledError):
        gateway.call_model(
            "cancellable",
            _MESSAGES,
            cancellation_event=cancelled,
        )

    cache.get.assert_not_called()
    cache.set.assert_not_called()


def test_cancellation_error_is_typed_and_serializable() -> None:
    """CallCancelledError carries profile_id and is a distinct exception type."""
    exc = CallCancelledError("cancellable")
    assert exc.profile_id == "cancellable"
    assert "cancellable" in str(exc)
    assert isinstance(exc, Exception)
    assert not isinstance(exc, PayloadLimitError)
    assert not isinstance(exc, ValueError)


def test_cancellation_rejects_before_provider_construction() -> None:
    """Set event means no provider instance is ever constructed."""
    gateway, _ = _make_gateway(_profile())
    gateway._registry = MagicMock()
    gateway._registry.is_installed.return_value = True
    provider_cls = MagicMock()
    gateway._registry.get_provider_class.return_value = provider_cls
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(CallCancelledError):
        gateway.call_model(
            "cancellable",
            _MESSAGES,
            cancellation_event=cancelled,
        )

    provider_cls.assert_not_called()


def test_cancellation_via_call_model_with_retry() -> None:
    """call_model_with_retry propagates cancellation before the first attempt."""
    gateway, chat_model = _make_gateway(_profile())
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(CallCancelledError) as caught:
        gateway.call_model_with_retry(
            "cancellable",
            _MESSAGES,
            cancellation_event=cancelled,
        )

    assert caught.value.profile_id == "cancellable"
    chat_model.invoke.assert_not_called()
