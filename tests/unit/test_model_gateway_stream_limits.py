"""D-30 phase-three limits for streamed model-gateway responses."""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import (
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
        "cost_per_input_token": 0.25,
        "cost_per_output_token": 0.5,
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


def _chunk(
    content: str,
    *,
    usage: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> MagicMock:
    chunk = MagicMock()
    chunk.content = content
    chunk.usage_metadata = usage or {}
    chunk.response_metadata = metadata or {}
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


def _gateway(
    profile: ModelProfile,
    stream: _ClosingIterator,
    *,
    wire_counter: MagicMock | None = None,
    cache: MagicMock | None = None,
    budget: MagicMock | None = None,
    metrics: MagicMock | None = None,
    tracer: MagicMock | None = None,
    health: MagicMock | None = None,
) -> tuple[ModelGateway, MagicMock, MagicMock]:
    chat_model = MagicMock()
    chat_model.stream.return_value = stream
    provider_factory = MagicMock(return_value=chat_model)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = provider_factory
    gateway = ModelGateway(
        [profile],
        provider_registry=registry,
        response_cache=cache,
        budget_guard=budget,
        metrics_collector=metrics,
        metrics_agent_id="agent-1" if metrics is not None else None,
        langsmith_tracer=tracer,
        health_tracker=health,
        stream_wire_byte_counter=wire_counter,
        # Peak-time billing clock (multiplier 1.0) keeps record_spend pins
        # deterministic against peak/off-peak pricing variance.
        billing_clock=lambda: datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC),
    )
    return gateway, provider_factory, chat_model


def test_stream_limits_are_positive_configurable_profile_fields() -> None:
    profile = _profile(
        max_stream_bytes=101,
        max_stream_tokens=102,
        max_stream_chunks=103,
        max_stream_seconds=104,
        max_stream_idle_seconds=105,
        max_stream_decompression_ratio=106,
    )
    assert (
        profile.max_stream_bytes,
        profile.max_stream_tokens,
        profile.max_stream_chunks,
        profile.max_stream_seconds,
        profile.max_stream_idle_seconds,
        profile.max_stream_decompression_ratio,
    ) == (101, 102, 103, 104, 105, 106)

    for field in (
        "max_stream_bytes",
        "max_stream_tokens",
        "max_stream_chunks",
        "max_stream_seconds",
        "max_stream_idle_seconds",
        "max_stream_decompression_ratio",
    ):
        with pytest.raises(ValueError, match="at least 1"):
            _profile(**{field: 0})


@pytest.mark.parametrize(
    ("overrides", "chunks", "dimension", "actual"),
    [
        ({"max_stream_bytes": 3}, [_chunk("safe"), _chunk("secret-payload")], "bytes", 4),
        ({"max_stream_tokens": 1}, [_chunk("ab")], "tokens", 2),
        ({"max_stream_chunks": 1}, [_chunk("a"), _chunk("b")], "chunks", 2),
    ],
)
def test_stream_limit_breach_closes_upstream_and_has_no_success_side_effects(
    overrides: dict[str, int],
    chunks: list[MagicMock],
    dimension: str,
    actual: int,
) -> None:
    upstream = _ClosingIterator(chunks)
    cache = MagicMock()
    budget = MagicMock()
    metrics = MagicMock()
    tracer = MagicMock()
    health = MagicMock()
    health.is_healthy.return_value = True
    gateway, _, chat_model = _gateway(
        _profile(**overrides),
        upstream,
        cache=cache,
        budget=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )

    with (
        patch("general_ludd.models.gateway.default_token_tracker") as token_tracker,
        pytest.raises(StreamLimitError) as caught,
    ):
        list(gateway.call_model_stream("streamed", [{"role": "user", "content": "hi"}]))

    assert (caught.value.dimension, caught.value.actual) == (dimension, actual)
    assert "secret-payload" not in str(caught.value)
    assert upstream.closed is True
    chat_model.stream.assert_called_once()
    cache.get.assert_not_called()
    cache.set.assert_not_called()
    budget.record_spend.assert_not_called()
    metrics.record_model_call.assert_not_called()
    tracer.is_enabled.assert_not_called()
    health.record_success.assert_not_called()
    token_tracker.assert_not_called()


@pytest.mark.parametrize(
    ("profile_overrides", "dimension"),
    [
        ({"max_stream_seconds": 1, "max_stream_idle_seconds": 10}, "duration_seconds"),
        ({"max_stream_seconds": 10, "max_stream_idle_seconds": 1}, "idle_seconds"),
    ],
)
def test_stream_time_limits_close_upstream(
    profile_overrides: dict[str, int],
    dimension: str,
) -> None:
    upstream = _ClosingIterator([_chunk("late")])
    gateway, _, _ = _gateway(_profile(**profile_overrides), upstream)

    with (
        patch("general_ludd.models.gateway.time.monotonic", side_effect=[100.0, 102.0]),
        pytest.raises(StreamLimitError) as caught,
    ):
        list(gateway.call_model_stream("streamed", [{"role": "user", "content": "hi"}]))

    assert (caught.value.dimension, caught.value.actual, caught.value.limit) == (
        dimension,
        2,
        1,
    )
    assert upstream.closed is True


def test_stream_decompression_ratio_uses_wire_counter_and_closes_upstream() -> None:
    upstream = _ClosingIterator([_chunk("x" * 101)])
    wire_counter = MagicMock(return_value=1)
    gateway, _, _ = _gateway(
        _profile(max_stream_decompression_ratio=100),
        upstream,
        wire_counter=wire_counter,
    )

    with pytest.raises(StreamLimitError) as caught:
        list(gateway.call_model_stream("streamed", [{"role": "user", "content": "hi"}]))

    assert (caught.value.dimension, caught.value.actual, caught.value.limit) == (
        "decompression_ratio",
        101,
        100,
    )
    assert caught.value.count_source == "configured_wire_byte_counter"
    wire_counter.assert_called_once()
    assert upstream.closed is True


def test_encoded_stream_without_wire_counter_fails_closed() -> None:
    upstream = _ClosingIterator([_chunk("payload", metadata={"content_encoding": "gzip"})])
    gateway, _, _ = _gateway(_profile(), upstream)

    with pytest.raises(StreamLimitError) as caught:
        list(gateway.call_model_stream("streamed", [{"role": "user", "content": "hi"}]))

    assert caught.value.dimension == "decompression_ratio"
    assert caught.value.count_source == "compressed_wire_bytes_unavailable"
    assert upstream.closed is True


def test_completed_stream_records_success_only_after_upstream_exhaustion() -> None:
    upstream = _ClosingIterator(
        [
            _chunk("hello "),
            _chunk(
                "world",
                usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            ),
        ]
    )
    budget = MagicMock()
    metrics = MagicMock()
    health = MagicMock()
    health.is_healthy.return_value = True
    gateway, provider_factory, _ = _gateway(
        _profile(),
        upstream,
        budget=budget,
        metrics=metrics,
        health=health,
    )

    with patch("general_ludd.models.gateway.default_token_tracker") as token_tracker:
        chunks = list(
            gateway.call_model_stream(
                "streamed",
                [{"role": "user", "content": "hi"}],
                work_type="game",
            )
        )

    assert [chunk.content for chunk in chunks] == ["hello ", "world"]
    assert upstream.closed is True
    provider_factory.assert_called_once()
    budget.record_spend.assert_called_once_with(2.0)
    metrics.record_model_call.assert_called_once()
    health.record_success.assert_called_once_with("streamed")
    token_tracker.return_value.record.assert_called_once_with("game", 2, 3)


def test_consumer_close_cancels_upstream_without_success_side_effects() -> None:
    upstream = _ClosingIterator([_chunk("first"), _chunk("second")])
    budget = MagicMock()
    health = MagicMock()
    health.is_healthy.return_value = True
    gateway, _, _ = _gateway(
        _profile(),
        upstream,
        budget=budget,
        health=health,
    )

    stream = gateway.call_model_stream("streamed", [{"role": "user", "content": "hi"}])
    assert next(stream).content == "first"
    stream.close()

    assert upstream.closed is True
    budget.record_spend.assert_not_called()
    health.record_success.assert_not_called()
