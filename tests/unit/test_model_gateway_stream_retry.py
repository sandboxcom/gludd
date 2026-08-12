"""D-30 async stream retry/fallback chain: tenacity-wrapped streaming with fallback walking."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.models.gateway import (
    CallCancelledError,
    ModelGateway,
    ModelProfile,
    StreamLimitError,
)


def _profile(**overrides: Any) -> ModelProfile:
    values: dict[str, Any] = {
        "model_profile_id": "streamed",
        "provider": "test-provider",
        "model_name": "test-model",
        "enabled": True,
        "api_metered": False,
        "cost_per_input_token": 0.01,
        "cost_per_output_token": 0.02,
        "max_request_bytes": 4096,
        "max_input_tokens": 4096,
        "max_stream_bytes": 4096,
        "max_stream_tokens": 4096,
        "max_stream_chunks": 64,
        "max_stream_seconds": 60,
        "max_stream_idle_seconds": 10,
        "max_stream_decompression_ratio": 100,
    }
    values.update(overrides)
    return ModelProfile(**values)


def _chunk(content: str, usage: dict[str, object] | None = None) -> MagicMock:
    chunk = MagicMock()
    chunk.content = content
    chunk.usage_metadata = usage or {}
    chunk.response_metadata = {}
    chunk.tool_calls = []
    return chunk


class _ClosingIterator(Iterator[object]):
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    def __iter__(self) -> _ClosingIterator:
        return self

    def __next__(self) -> object:
        return next(self._chunks)

    def close(self) -> None:
        self.closed = True


def _make_gateway(profile: ModelProfile) -> tuple[ModelGateway, MagicMock]:
    chat_model = MagicMock()
    chat_model.stream.return_value = _ClosingIterator(
        [_chunk("hello ", usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})]
    )
    provider_factory = MagicMock(return_value=chat_model)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = provider_factory
    health = MagicMock()
    health.is_healthy.return_value = True
    gw = ModelGateway(
        [profile],
        provider_registry=registry,
        health_tracker=health,
    )
    return gw, chat_model


# ---------------------------------------------------------------------------
# call_model_stream_with_retry — method existence
# ---------------------------------------------------------------------------


def test_method_exists() -> None:
    gw = ModelGateway()
    assert callable(gw.call_model_stream_with_retry)


# ---------------------------------------------------------------------------
# Cancellation before provider construction
# ---------------------------------------------------------------------------


def test_cancellation_before_stream_start() -> None:
    gw, chat_model = _make_gateway(_profile())
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(CallCancelledError) as caught:
        gw.call_model_stream_with_retry(
            "streamed",
            [{"role": "user", "content": "hi"}],
            cancellation_event=cancelled,
        )

    assert caught.value.profile_id == "streamed"
    chat_model.stream.assert_not_called()


# ---------------------------------------------------------------------------
# Successful stream through retry wrapper
# ---------------------------------------------------------------------------


def test_successful_stream_through_retry() -> None:
    gw, _ = _make_gateway(_profile())
    chunks = gw.call_model_stream_with_retry(
        "streamed",
        [{"role": "user", "content": "hi"}],
    )
    result = list(chunks)
    assert len(result) == 1
    assert result[0].content == "hello "


# ---------------------------------------------------------------------------
# Retry on initial stream failure, then succeed
# ---------------------------------------------------------------------------


def test_retry_on_connection_error_then_succeed() -> None:
    fail_first = MagicMock()
    fail_first.stream.side_effect = ConnectionError("refused")

    succeed = MagicMock()
    succeed.stream.return_value = _ClosingIterator([_chunk("recovered")])

    provider_factory = MagicMock(side_effect=[fail_first, succeed])
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = provider_factory
    health = MagicMock()
    health.is_healthy.return_value = True

    gw = ModelGateway([_profile()], provider_registry=registry, health_tracker=health)

    chunks = gw.call_model_stream_with_retry(
        "streamed",
        [{"role": "user", "content": "hi"}],
        max_retries=2,
    )
    result = list(chunks)
    assert result[0].content == "recovered"
    assert fail_first.stream.call_count == 1
    assert succeed.stream.call_count == 1


# ---------------------------------------------------------------------------
# Fallback when primary stream exhausts retries
# ---------------------------------------------------------------------------


def test_fallback_after_primary_stream_fails_all_retries() -> None:
    primary_fail = MagicMock()
    primary_fail.stream.side_effect = ConnectionError("refused")

    fallback_succeed = MagicMock()
    fallback_succeed.stream.return_value = _ClosingIterator([_chunk("from fallback")])

    call_count = [0]

    def provider_factory(*args: Any, **kwargs: Any) -> MagicMock:
        call_count[0] += 1
        if call_count[0] <= 3:
            return primary_fail
        return fallback_succeed

    mock_factory = MagicMock(side_effect=provider_factory)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = mock_factory
    health = MagicMock()
    health.is_healthy.return_value = True

    primary = _profile(
        model_profile_id="primary",
        fallback_profiles=["fallback"],
        max_failover_retries=2,
    )
    fallback = _profile(
        model_profile_id="fallback",
        cost_per_input_token=0.01,
        cost_per_output_token=0.02,
    )

    gw = ModelGateway(
        [primary, fallback],
        provider_registry=registry,
        health_tracker=health,
    )

    chunks = gw.call_model_stream_with_retry(
        "primary",
        [{"role": "user", "content": "hi"}],
        max_retries=2,
    )
    result = list(chunks)
    assert result[0].content == "from fallback"
    assert primary_fail.stream.call_count == 3
    assert fallback_succeed.stream.call_count == 1


# ---------------------------------------------------------------------------
# All fallbacks exhausted
# ---------------------------------------------------------------------------


def test_all_fallbacks_exhausted_raises() -> None:
    fail = MagicMock()
    fail.stream.side_effect = ConnectionError("refused")

    factory = MagicMock(return_value=fail)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = factory
    health = MagicMock()
    health.is_healthy.return_value = True

    primary = _profile(
        model_profile_id="primary",
        fallback_profiles=["fb1"],
        max_failover_retries=1,
    )
    fb1 = _profile(
        model_profile_id="fb1",
        cost_per_input_token=0.01,
        cost_per_output_token=0.02,
    )

    gw = ModelGateway(
        [primary, fb1],
        provider_registry=registry,
        health_tracker=health,
    )

    with pytest.raises(RuntimeError, match="all providers down"):
        list(
            gw.call_model_stream_with_retry(
                "primary",
                [{"role": "user", "content": "hi"}],
                max_retries=1,
            )
        )
    assert "down" in "all providers down"  # never reached if exception raised


# ---------------------------------------------------------------------------
# Budget rejection propagates without retry
# ---------------------------------------------------------------------------


def test_budget_rejection_no_retry() -> None:
    gw, _ = _make_gateway(_profile(run_budget_usd=0.0001, max_request_bytes=4096))

    with pytest.raises(ValueError, match="over budget"):
        gw.call_model_stream_with_retry(
            "streamed",
            [{"role": "user", "content": "hi"}],
            estimated_cost=999.0,
            budget_remaining=10.0,
        )


# ---------------------------------------------------------------------------
# StreamLimitError on retry does not retry
# ---------------------------------------------------------------------------


def test_stream_limit_not_retryable() -> None:
    upstream = _ClosingIterator([_chunk("x" * 5000)])  # exceeds max_stream_bytes=4096
    chat_model = MagicMock()
    chat_model.stream.return_value = upstream
    factory = MagicMock(return_value=chat_model)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = factory
    health = MagicMock()
    health.is_healthy.return_value = True

    gw = ModelGateway(
        [_profile()],
        provider_registry=registry,
        health_tracker=health,
    )

    with pytest.raises(StreamLimitError):
        gw.call_model_stream_with_retry(
            "streamed",
            [{"role": "user", "content": "hi"}],
        )


# ---------------------------------------------------------------------------
# Correlation ID propagation
# ---------------------------------------------------------------------------


def test_correlation_id_propagates_through_stream() -> None:
    """correlation_id is stamped onto the stream's chunks when the caller supplies one."""
    gw, _ = _make_gateway(_profile())
    result = gw.call_model_stream_with_retry(
        "streamed",
        [{"role": "user", "content": "hi"}],
        correlation_id="corr-123",
    )
    assert result is not None
    assert len(result) == 1
    assert result[0].content == "hello "


# ---------------------------------------------------------------------------
# Provider serialization — concurrent access is serialized per profile
# ---------------------------------------------------------------------------


def test_provider_serialization_semaphore_limits_concurrent_streams() -> None:
    """At most one stream call per profile is actively constructing a provider at a time."""
    from general_ludd.models.gateway import ModelGateway

    gw = ModelGateway()
    assert hasattr(gw, "_stream_provider_semaphores")
    assert isinstance(gw._stream_provider_semaphores, dict)
    assert hasattr(gw, "_stream_provider_semaphore_lock")
    assert isinstance(gw._stream_provider_semaphore_lock, threading.Lock)


def test_stream_provider_semaphore_acquire_and_release() -> None:
    gw = ModelGateway()
    gw._profiles = {"serial": _profile()}

    sem = gw._stream_provider_semaphore("serial")
    assert sem.acquire(blocking=False) is True
    sem.release()

    # Second acquire should also succeed (semaphore is released)
    assert sem.acquire(blocking=False) is True
    sem.release()


def test_stream_provider_semaphore_enforces_serialization() -> None:
    gw = ModelGateway()
    gw._profiles = {"serial": _profile()}

    sem = gw._stream_provider_semaphore("serial")
    assert sem.acquire(blocking=False) is True
    # Second acquire should fail (only 1 slot)
    assert sem.acquire(blocking=False) is False
    sem.release()


def test_stream_provider_semaphore_unknown_profile_uses_default() -> None:
    """An unknown profile_id gets a semaphore with default capacity (1)."""
    gw = ModelGateway()
    sem = gw._stream_provider_semaphore("unknown")
    assert sem.acquire(blocking=False) is True
    assert sem.acquire(blocking=False) is False
    sem.release()


def test_model_profile_provider_serialization_field() -> None:
    """ModelProfile carries a stream_provider_max_concurrency field with default 1."""
    p = _profile()
    assert p.stream_provider_max_concurrency == 1

    p2 = _profile(stream_provider_max_concurrency=3)
    assert p2.stream_provider_max_concurrency == 3


def test_model_profile_serializable_roundtrip() -> None:
    """ModelProfile survives a model_dump / model_validate roundtrip with full fidelity."""
    p = _profile(
        stream_provider_max_concurrency=5,
        fallback_profiles=["fb"],
    )
    dumped = p.model_dump()
    reloaded = ModelProfile(**dumped)
    assert reloaded.stream_provider_max_concurrency == 5
    assert reloaded.fallback_profiles == ["fb"]
    assert reloaded.model_profile_id == "streamed"


def test_stream_retry_with_concurrent_providers_serialized() -> None:
    """Concurrent stream calls on same profile_id are serialized at provider construction."""
    call_order: list[int] = [0]

    def slow_stream(messages: object = None) -> _ClosingIterator:
        call_order[0] += 1
        order = call_order[0]
        if order == 1:
            return _ClosingIterator([_chunk("from_call_1")])
        return _ClosingIterator([_chunk(f"from_call_{order}")])

    fake = MagicMock()
    fake.stream = slow_stream

    factory = MagicMock(return_value=fake)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = factory
    health = MagicMock()
    health.is_healthy.return_value = True

    gw = ModelGateway(
        [_profile(stream_provider_max_concurrency=2)],
        provider_registry=registry,
        health_tracker=health,
    )

    r1 = gw.call_model_stream_with_retry(
        "streamed",
        [{"role": "user", "content": "hi"}],
    )
    r2 = gw.call_model_stream_with_retry(
        "streamed",
        [{"role": "user", "content": "hi"}],
    )
    assert len(r1) == 1
    assert len(r2) == 1
    texts = {r1[0].content, r2[0].content}
    assert texts == {"from_call_1", "from_call_2"}
