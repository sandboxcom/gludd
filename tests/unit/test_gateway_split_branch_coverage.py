"""Focused branch coverage for the extracted gateway layers."""

from __future__ import annotations

import threading
from collections.abc import Coroutine, Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import general_ludd.models.gateway as gateway_module
from general_ludd.models.gateway import (
    BudgetExceededError,
    CallCancelledError,
    CircuitBreakerOpenError,
    ModelGateway,
    ModelPausedError,
    ModelProfile,
    PayloadLimitError,
    SSRFRejectionError,
    StreamLimitError,
)


def _profile(profile_id: str = "stream", **overrides: Any) -> ModelProfile:
    values: dict[str, Any] = {
        "model_profile_id": profile_id,
        "provider": "test-provider",
        "model_name": "test-model",
        "enabled": True,
        "api_metered": False,
        "max_stream_seconds": 60,
        "max_stream_idle_seconds": 60,
    }
    values.update(overrides)
    return ModelProfile(**values)


def _chunk(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        usage_metadata={"input_tokens": 1, "output_tokens": 1},
        response_metadata={},
        tool_calls=[],
    )


def _registry(provider: object, *, installed: bool = True) -> MagicMock:
    registry = MagicMock()
    registry.is_installed.return_value = installed
    registry.get_provider_class.return_value = provider
    return registry


def _gateway_with_stream(
    profile: ModelProfile,
    chunks: Iterator[object] | list[object],
    **gateway_kwargs: Any,
) -> tuple[ModelGateway, MagicMock, MagicMock]:
    chat_model = MagicMock()
    chat_model.stream.return_value = iter(chunks)
    factory = MagicMock(return_value=chat_model)
    model_gateway = ModelGateway(
        [profile],
        provider_registry=_registry(factory),
        **gateway_kwargs,
    )
    return model_gateway, factory, chat_model


def test_runtime_stream_route_and_registration_lifecycle() -> None:
    profile = _profile("runtime")
    runtime = MagicMock()
    runtime.call_model_stream.return_value = [_chunk("runtime")]
    model_gateway = ModelGateway()

    model_gateway.register_runtime_profile(profile, runtime)

    chunks = cast(Iterator[Any], model_gateway.call_model_stream("runtime", []))
    assert [chunk.content for chunk in chunks] == ["runtime"]
    runtime.call_model_stream.assert_called_once()
    assert model_gateway.remove_runtime_profile("runtime", cast(Any, object())) is False
    assert model_gateway.remove_runtime_profile("runtime", runtime) is True


def test_runtime_registration_validates_duplicates_and_rolls_back_notifications() -> None:
    profile = _profile("runtime")
    runtime = MagicMock(call_model=MagicMock(), call_model_stream=MagicMock())
    model_gateway = ModelGateway()

    with pytest.raises(ValueError, match="ModelProfile"):
        model_gateway.register_runtime_profile(cast(Any, object()), runtime)
    with pytest.raises(ValueError, match="buffered and streaming"):
        model_gateway.register_runtime_profile(profile, cast(Any, object()))
    with pytest.raises(ValueError, match="buffered and streaming"):
        model_gateway.register_runtime_profile(profile, model_gateway)

    model_gateway.register_runtime_profile(profile, runtime)
    with pytest.raises(ValueError, match="already registered"):
        model_gateway.register_runtime_profile(profile, runtime)

    failing_bus = MagicMock()
    failing_bus.publish.side_effect = RuntimeError("publish failed")
    rollback_gateway = ModelGateway(event_bus=failing_bus)
    with pytest.raises(RuntimeError, match="publish failed"):
        rollback_gateway.register_runtime_profile(_profile("rollback"), runtime)
    assert rollback_gateway.get_profile("rollback") is None


def test_runtime_removal_rolls_back_when_notification_fails() -> None:
    profile = _profile("runtime")
    runtime = MagicMock(call_model=MagicMock(), call_model_stream=MagicMock())
    event_bus = MagicMock()
    model_gateway = ModelGateway(event_bus=event_bus)
    model_gateway.register_runtime_profile(profile, runtime)
    event_bus.publish.side_effect = RuntimeError("remove failed")

    with pytest.raises(RuntimeError, match="remove failed"):
        model_gateway.remove_runtime_profile("runtime", runtime)

    assert model_gateway.get_profile("runtime") is profile


@pytest.mark.parametrize("installed", [False])
def test_stream_installs_missing_provider_then_fails_closed(installed: bool) -> None:
    registry = _registry(MagicMock(), installed=installed)
    model_gateway = ModelGateway([_profile()], provider_registry=registry)

    with pytest.raises(ImportError, match="not installed"):
        list(model_gateway.call_model_stream("stream", []))

    registry.install_provider.assert_called_once_with("test-provider")


def test_stream_without_registry_or_with_open_circuit_fails_closed() -> None:
    profile = _profile()
    with pytest.raises(ValueError, match="No provider registry"):
        list(ModelGateway([profile]).call_model_stream("stream", []))

    health = MagicMock()
    health.is_healthy.return_value = False
    guarded = ModelGateway([profile], provider_registry=_registry(MagicMock()), health_tracker=health)
    with pytest.raises(CircuitBreakerOpenError, match="circuit is open"):
        list(guarded.call_model_stream("stream", []))


def test_stream_forwards_validated_secrets_tools_guidance_and_observers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Resolver:
        def resolve(self, alias: str) -> str:
            return {
                "credential": "secret-key",
                "base": "http://localhost:11434/v1",
            }[alias]

    profile = _profile(
        "local-stream",
        credential_alias="credential",
        api_base_alias="base",
    )
    budget = MagicMock()
    metrics = MagicMock()
    health = MagicMock()
    health.is_healthy.return_value = True
    tracer = MagicMock()
    tracer.is_enabled.return_value = True
    model_gateway, factory, chat_model = _gateway_with_stream(
        profile,
        [_chunk()],
        secrets_manager=Resolver(),
        budget_guard=budget,
        metrics_collector=metrics,
        metrics_agent_id="agent",
        health_tracker=health,
        langsmith_tracer=tracer,
    )
    chat_model.bind_tools.return_value = chat_model
    tracker = MagicMock()
    monkeypatch.setattr(gateway_module, "default_token_tracker", tracker)
    logger = MagicMock()
    monkeypatch.setattr(gateway_module, "logger", logger)

    result = list(
        model_gateway.call_model_stream(
            "local-stream",
            [{"role": "user", "content": "hello"}],
            tools=[{"type": "function"}],
            work_type=None,
            base_url="https://ignored.invalid",
            api_key="ignored",
            extra_body="invalid",
            guided_json={"type": "object"},
        )
    )

    assert len(result) == 1
    init_kwargs = factory.call_args.kwargs
    assert init_kwargs["api_key"] == "secret-key"
    assert init_kwargs["base_url"] == "http://localhost:11434/v1"
    assert init_kwargs["extra_body"] == {"guided_json": {"type": "object"}}
    chat_model.bind_tools.assert_called_once()
    assert logger.warning.call_count == 2
    budget.record_spend.assert_called_once()
    metrics.record_model_call.assert_called_once()
    health.record_success.assert_called_once_with("local-stream")
    tracer.trace_call.assert_called_once()
    tracker.return_value.record.assert_called_once_with("unknown", 1, 1)


def test_stream_remote_base_url_uses_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resolver:
        def resolve(self, alias: str) -> str:
            return "https://models.example.invalid/v1"

    monkeypatch.setattr("general_ludd.security.auth.is_safe_fetch_url", lambda _url: True)
    profile = _profile(api_base_alias="base")
    model_gateway, factory, _ = _gateway_with_stream(
        profile,
        [_chunk()],
        secrets_manager=Resolver(),
    )

    assert len(list(model_gateway.call_model_stream("stream", []))) == 1
    assert factory.call_args.kwargs["base_url"] == "https://models.example.invalid/v1"


def test_stream_remote_unsafe_base_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resolver:
        def resolve(self, alias: str) -> str:
            return "http://127.0.0.1:8000/v1"

    monkeypatch.setattr("general_ludd.security.auth.is_safe_fetch_url", lambda _url: False)
    profile = _profile(api_base_alias="base")
    model_gateway, _, _ = _gateway_with_stream(profile, [], secrets_manager=Resolver())

    with pytest.raises(SSRFRejectionError):
        list(model_gateway.call_model_stream("stream", []))


def test_stream_tools_require_provider_binding_support() -> None:
    class ProviderWithoutTools:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def stream(self, messages: list[dict[str, str]]) -> Iterator[object]:
            return iter([_chunk()])

    model_gateway = ModelGateway(
        [_profile()],
        provider_registry=_registry(ProviderWithoutTools),
    )

    with pytest.raises(ValueError, match="does not support streamed tools"):
        list(model_gateway.call_model_stream("stream", [], tools=[{"type": "function"}]))


def test_stream_provider_start_failure_records_timeout_and_redacts_url() -> None:
    provider = MagicMock()
    provider.stream.side_effect = RuntimeError("failed at http://localhost:11434/v1")
    factory = MagicMock(return_value=provider)
    health = MagicMock()
    health.is_healthy.return_value = True
    profile = _profile("local-stream")
    model_gateway = ModelGateway(
        [profile],
        provider_registry=_registry(factory),
        health_tracker=health,
    )

    with pytest.raises(RuntimeError, match="REDACTED_URL"):
        list(model_gateway.call_model_stream("local-stream", []))

    health.record_event.assert_called_once()


def test_stream_unserializable_chunk_and_failing_wire_counter_are_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_gateway, _, _ = _gateway_with_stream(_profile(), [_chunk()])
    monkeypatch.setattr(
        model_gateway,
        "_stream_chunk_payload",
        MagicMock(side_effect=TypeError("bad chunk")),
    )
    with pytest.raises(StreamLimitError) as unserializable:
        list(model_gateway.call_model_stream("stream", []))
    assert unserializable.value.count_source == "unserializable_stream_chunk"

    failing_counter = MagicMock(side_effect=RuntimeError("counter failed"))
    counter_gateway, _, _ = _gateway_with_stream(
        _profile(),
        [_chunk()],
        stream_wire_byte_counter=failing_counter,
    )
    with pytest.raises(StreamLimitError) as invalid_counter:
        list(counter_gateway.call_model_stream("stream", []))
    assert invalid_counter.value.count_source == "invalid_wire_byte_counter"


@pytest.mark.parametrize("wire_delta", [True, -1, 0])
def test_stream_rejects_invalid_wire_counts(wire_delta: object) -> None:
    model_gateway, _, _ = _gateway_with_stream(
        _profile(),
        [_chunk()],
        stream_wire_byte_counter=lambda _chunk: cast(Any, wire_delta),
    )

    with pytest.raises(StreamLimitError) as caught:
        list(model_gateway.call_model_stream("stream", []))

    assert caught.value.count_source == "invalid_wire_byte_counter"


def test_stream_close_failure_is_logged_and_empty_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CloseFailure(Iterator[object]):
        def __init__(self, chunks: list[object]) -> None:
            self._chunks = iter(chunks)

        def __iter__(self) -> CloseFailure:
            return self

        def __next__(self) -> object:
            return next(self._chunks)

        def close(self) -> None:
            raise RuntimeError("close failed")

    logger = MagicMock()
    monkeypatch.setattr(gateway_module, "logger", logger)
    model_gateway, _, _ = _gateway_with_stream(_profile(), CloseFailure([_chunk()]))
    assert len(list(model_gateway.call_model_stream("stream", []))) == 1
    logger.debug.assert_called_once()

    empty_gateway, _, _ = _gateway_with_stream(_profile(), [])
    with pytest.raises(httpx.HTTPStatusError, match="empty-content 200 response"):
        list(empty_gateway.call_model_stream("stream", []))


@pytest.mark.asyncio
async def test_stream_retry_entry_points_cover_running_loop_and_async_guards() -> None:
    model_gateway, _, _ = _gateway_with_stream(_profile(), [_chunk()])
    result = model_gateway.call_model_stream_with_retry("stream", [])
    chunks = await cast(Coroutine[Any, Any, list[Any]], result)
    assert [chunk.content for chunk in chunks] == ["ok"]

    missing = ModelGateway()
    with pytest.raises(ValueError, match="not found"):
        missing.call_model_stream_with_retry("missing", [])
    with pytest.raises(ValueError, match="not found"):
        await missing._call_model_stream_with_retry_async("missing", [])

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(CallCancelledError):
        await model_gateway._call_model_stream_with_retry_async(
            "stream",
            [],
            cancellation_event=cancelled,
        )


@pytest.mark.asyncio
async def test_stream_retry_unhealthy_primary_goes_directly_to_fallbacks() -> None:
    health = MagicMock()
    health.is_healthy.return_value = False
    model_gateway = ModelGateway(
        [_profile(fallback_profiles=["fallback"]), _profile("fallback")],
        health_tracker=health,
    )
    walker = AsyncMock(return_value=[_chunk("fallback")])
    cast(Any, model_gateway)._stream_walk_fallbacks = walker

    result = await model_gateway._call_model_stream_with_retry_async("stream", [])

    assert cast(Any, result[0]).content == "fallback"
    walker.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_retry_does_not_retry_unknown_or_circuit_errors() -> None:
    model_gateway = ModelGateway([_profile()])
    call_model_stream = MagicMock(side_effect=ValueError("bad request"))
    cast(Any, model_gateway).call_model_stream = call_model_stream
    with pytest.raises(ValueError, match="bad request"):
        await model_gateway._call_model_stream_with_retry_async("stream", [], max_retries=2)
    assert call_model_stream.call_count == 1

    cast(Any, model_gateway).call_model_stream = MagicMock(
        side_effect=CircuitBreakerOpenError("open")
    )
    with pytest.raises(CircuitBreakerOpenError, match="open"):
        await model_gateway._call_model_stream_with_retry_async("stream", [], max_retries=2)


def _payload_error() -> PayloadLimitError:
    return PayloadLimitError(
        profile_id="fallback",
        stage="response",
        dimension="bytes",
        actual=2,
        limit=1,
        source="provider",
        count_source="test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        StreamLimitError(
            profile_id="fallback",
            stage="response",
            dimension="chunks",
            actual=2,
            limit=1,
            source="provider",
            count_source="test",
        ),
        _payload_error(),
        BudgetExceededError("budget"),
        SSRFRejectionError("ssrf"),
        ModelPausedError("paused"),
        CallCancelledError("fallback"),
    ],
)
async def test_stream_fallbacks_propagate_fail_closed_errors(error: Exception) -> None:
    model_gateway = ModelGateway([_profile("fallback")])
    call_model_stream = MagicMock(side_effect=error)
    cast(Any, model_gateway).call_model_stream = call_model_stream

    with pytest.raises(type(error)):
        await model_gateway._stream_walk_fallbacks(
            ["fallback"],
            [],
            from_profile_id="primary",
        )


@pytest.mark.asyncio
async def test_stream_fallback_walk_cascades_and_applies_budget_health_and_depth() -> None:
    first = _profile("first", fallback_profiles=["second", "primary"])
    second = _profile("second")
    model_gateway = ModelGateway([first, second], max_fallback_depth=4)

    def stream(profile_id: str, messages: list[dict[str, str]], **kwargs: object) -> Iterator[object]:
        if profile_id == "first":
            raise RuntimeError("first failed")
        return iter([_chunk("second")])

    cast(Any, model_gateway).call_model_stream = MagicMock(side_effect=stream)
    result = await model_gateway._stream_walk_fallbacks(
        ["first"],
        [],
        from_profile_id="primary",
        from_error=RuntimeError("primary failed"),
    )
    assert cast(Any, result[0]).content == "second"

    budget_gateway = ModelGateway([first])
    cast(Any, budget_gateway).check_budget = MagicMock(return_value=False)
    with pytest.raises(RuntimeError, match="budget exceeded"):
        await budget_gateway._stream_walk_fallbacks(
            ["first"],
            [],
            from_profile_id="primary",
            from_error=RuntimeError("primary failed"),
            estimated_cost=1.0,
            budget_remaining=0.0,
        )

    health = MagicMock()
    health.is_healthy.return_value = False
    health_gateway = ModelGateway([first], health_tracker=health, max_fallback_depth=0)
    with pytest.raises(RuntimeError, match="primary failed"):
        await health_gateway._stream_walk_fallbacks(
            ["primary", "first"],
            [],
            from_profile_id="primary",
            from_error=RuntimeError("primary failed"),
        )


@pytest.mark.asyncio
async def test_sync_stream_fallback_wrapper_preserves_running_loop_behavior() -> None:
    model_gateway = ModelGateway()
    cast(Any, model_gateway)._stream_walk_fallbacks = AsyncMock(return_value=[_chunk()])

    result = model_gateway._stream_walk_fallbacks_sync([], [], from_profile_id="primary")

    awaited = await cast(Coroutine[Any, Any, list[object]], result)
    assert cast(Any, awaited[0]).content == "ok"


@pytest.mark.asyncio
async def test_stream_retry_http_status_auth_is_not_retried() -> None:
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(401, request=request)
    error = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    model_gateway = ModelGateway([_profile()])
    call_model_stream = MagicMock(side_effect=error)
    cast(Any, model_gateway).call_model_stream = call_model_stream

    with pytest.raises(RuntimeError, match=r"all providers down.*unauthorized"):
        await model_gateway._call_model_stream_with_retry_async("stream", [], max_retries=2)

    assert call_model_stream.call_count == 1
