"""D-30 phase-one hard limits at the buffered model-gateway boundary."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from general_ludd.models.gateway import (
    CumulativePayloadLimitError,
    ModelGateway,
    ModelProfile,
    PayloadLimitError,
    SSRFRejectionError,
    _LimitedChatModel,
)


def _profile(profile_id: str = "limited", **overrides: Any) -> ModelProfile:
    values: dict[str, Any] = {
        "model_profile_id": profile_id,
        "provider": "test-provider",
        "model_name": "test-model",
        "enabled": True,
        "api_metered": False,
        "cost_per_input_token": 0.25,
        "cost_per_output_token": 0.5,
        "max_request_bytes": 4096,
        "max_input_tokens": 4096,
        "max_response_bytes": 4096,
        "max_output_tokens": 4096,
        "max_tool_calls": 8,
    }
    values.update(overrides)
    return ModelProfile(**values)


def _response(
    content: str = "ok",
    *,
    usage: dict[str, object] | None = None,
    tool_calls: list[dict[str, object]] | None = None,
) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.usage_metadata = usage if usage is not None else {"input_tokens": 1, "output_tokens": 1}
    response.tool_calls = tool_calls or []
    response.response_metadata = {}
    return response


def _gateway(
    profile: ModelProfile,
    response: MagicMock | None = None,
    *,
    request_token_counter: Callable[[ModelProfile, list[dict[str, str]]], int] | None = None,
    response_cache: MagicMock | None = None,
    budget_guard: MagicMock | None = None,
    metrics: MagicMock | None = None,
    tracer: MagicMock | None = None,
    health: MagicMock | None = None,
) -> tuple[ModelGateway, MagicMock, MagicMock]:
    chat_model = MagicMock()
    chat_model.invoke.return_value = response or _response()
    provider_factory = MagicMock(return_value=chat_model)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = provider_factory
    gateway = ModelGateway(
        [profile],
        provider_registry=registry,
        request_token_counter=request_token_counter,
        response_cache=response_cache,
        budget_guard=budget_guard,
        metrics_collector=metrics,
        metrics_agent_id="agent-1" if metrics is not None else None,
        langsmith_tracer=tracer,
        health_tracker=health,
    )
    return gateway, provider_factory, chat_model


def _compact_message_bytes(messages: list[dict[str, str]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _assert_no_post_response_side_effects(
    *,
    cache: MagicMock,
    budget: MagicMock,
    metrics: MagicMock,
    tracer: MagicMock,
    health: MagicMock,
) -> None:
    cache.set.assert_not_called()
    budget.record_spend.assert_not_called()
    metrics.record_model_call.assert_not_called()
    tracer.is_enabled.assert_not_called()
    tracer.trace_call.assert_not_called()
    health.record_success.assert_not_called()


def test_profile_payload_limits_are_positive_configurable_integers() -> None:
    profile = _profile(
        max_request_bytes=101,
        max_input_tokens=102,
        max_response_bytes=103,
        max_output_tokens=104,
        max_tool_calls=5,
        max_cumulative_request_bytes=201,
        max_cumulative_input_tokens=202,
        max_cumulative_response_bytes=203,
        max_cumulative_output_tokens=204,
        max_cumulative_tool_calls=6,
        max_provider_attempts=7,
    )
    assert (
        profile.max_request_bytes,
        profile.max_input_tokens,
        profile.max_response_bytes,
        profile.max_output_tokens,
        profile.max_tool_calls,
        profile.max_cumulative_request_bytes,
        profile.max_cumulative_input_tokens,
        profile.max_cumulative_response_bytes,
        profile.max_cumulative_output_tokens,
        profile.max_cumulative_tool_calls,
        profile.max_provider_attempts,
    ) == (101, 102, 103, 104, 5, 201, 202, 203, 204, 6, 7)

    for field in (
        "max_request_bytes",
        "max_input_tokens",
        "max_response_bytes",
        "max_output_tokens",
        "max_tool_calls",
        "max_cumulative_request_bytes",
        "max_cumulative_input_tokens",
        "max_cumulative_response_bytes",
        "max_cumulative_output_tokens",
        "max_cumulative_tool_calls",
        "max_provider_attempts",
    ):
        invalid: dict[str, Any] = {field: 0}
        with pytest.raises(ValueError, match="at least 1"):
            _profile(**invalid)

    assert _profile("  bounded  ").model_profile_id == "bounded"
    with pytest.raises(ValueError, match="must not be empty"):
        _profile("   ")


@pytest.mark.parametrize(
    ("dimension_case", "expected_stage", "expected_dimension", "expected_actual", "provider_calls"),
    [
        ("request_bytes", "request", "bytes", None, 1),
        ("request_tokens", "request", "tokens", 6, 1),
        ("response_bytes", "response", "bytes", 2, 2),
        ("response_tokens", "response", "tokens", 4, 2),
        ("tool_calls", "response", "tool_calls", 2, 2),
        ("provider_attempts", "request", "provider_attempts", 2, 1),
    ],
)
def test_retry_chain_shares_one_cumulative_payload_budget(
    dimension_case: str,
    expected_stage: str,
    expected_dimension: str,
    expected_actual: int | None,
    provider_calls: int,
) -> None:
    messages = [{"role": "user", "content": "retry me"}]
    request_bytes = _compact_message_bytes(messages)
    overrides: dict[str, int] = {
        "max_cumulative_request_bytes": 4096,
        "max_cumulative_input_tokens": 4096,
        "max_cumulative_response_bytes": 4096,
        "max_cumulative_output_tokens": 4096,
        "max_cumulative_tool_calls": 8,
        "max_provider_attempts": 8,
    }
    response = _response(
        " ",
        usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    )
    counter: Callable[[ModelProfile, list[dict[str, str]]], int] | None = None

    if dimension_case == "request_bytes":
        overrides["max_cumulative_request_bytes"] = request_bytes
        expected_actual = request_bytes * 2
    elif dimension_case == "request_tokens":
        overrides["max_cumulative_input_tokens"] = 3
        counter = MagicMock(return_value=3)
    elif dimension_case == "response_bytes":
        overrides["max_cumulative_response_bytes"] = 1
    elif dimension_case == "response_tokens":
        overrides["max_cumulative_output_tokens"] = 2
        response = _response(
            " ",
            usage={"input_tokens": 0, "output_tokens": 2, "total_tokens": 2},
        )
    elif dimension_case == "tool_calls":
        overrides["max_cumulative_tool_calls"] = 1
        response = _response(
            "",
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            tool_calls=[{"id": "malformed-without-name"}],
        )
    else:
        overrides["max_provider_attempts"] = 1

    cache = MagicMock()
    cache.get.return_value = None
    budget = MagicMock()
    metrics = MagicMock()
    tracer = MagicMock()
    health = MagicMock()
    health.is_healthy.return_value = True
    gateway, _, chat_model = _gateway(
        _profile("limited", **overrides),
        response,
        request_token_counter=counter,
        response_cache=cache,
        budget_guard=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )

    with (
        patch("general_ludd.models.gateway.asyncio.sleep", new=AsyncMock()),
        patch("general_ludd.models.gateway.default_token_tracker") as token_tracker,
        pytest.raises(CumulativePayloadLimitError) as caught,
    ):
        gateway.call_model_with_retry(
            "limited",
            messages,
            max_retries=3,
            base_backoff_seconds=0,
        )

    error = caught.value
    assert (error.stage, error.dimension, error.actual, error.source) == (
        expected_stage,
        expected_dimension,
        expected_actual,
        "gateway",
    )
    assert error.count_source == "request_wide_cumulative"
    assert "retry me" not in str(error)
    assert chat_model.invoke.call_count == provider_calls
    token_tracker.assert_not_called()
    _assert_no_post_response_side_effects(
        cache=cache,
        budget=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )


@pytest.mark.parametrize(
    ("dimension_case", "expected_stage", "expected_dimension", "provider_calls"),
    [
        ("request_bytes", "request", "bytes", 1),
        ("request_tokens", "request", "tokens", 1),
        ("response_bytes", "response", "bytes", 2),
        ("response_tokens", "response", "tokens", 2),
        ("tool_calls", "response", "tool_calls", 2),
        ("provider_attempts", "request", "provider_attempts", 1),
    ],
)
def test_fallback_chain_shares_one_cumulative_payload_budget(
    dimension_case: str,
    expected_stage: str,
    expected_dimension: str,
    provider_calls: int,
) -> None:
    messages = [{"role": "user", "content": "one logical request"}]
    request_bytes = _compact_message_bytes(messages)
    overrides: dict[str, int] = {
        "max_cumulative_request_bytes": 4096,
        "max_cumulative_input_tokens": 4096,
        "max_cumulative_response_bytes": 4096,
        "max_cumulative_output_tokens": 4096,
        "max_cumulative_tool_calls": 8,
        "max_provider_attempts": 8,
    }
    response = _response(
        " ",
        usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    )
    counter: Callable[[ModelProfile, list[dict[str, str]]], int] | None = None
    expected_actual = 2
    if dimension_case == "request_bytes":
        overrides["max_cumulative_request_bytes"] = request_bytes
        expected_actual = request_bytes * 2
    elif dimension_case == "request_tokens":
        overrides["max_cumulative_input_tokens"] = 3
        counter = MagicMock(return_value=3)
        expected_actual = 6
    elif dimension_case == "response_bytes":
        overrides["max_cumulative_response_bytes"] = 1
    elif dimension_case == "response_tokens":
        overrides["max_cumulative_output_tokens"] = 2
        response = _response(
            " ",
            usage={"input_tokens": 0, "output_tokens": 2, "total_tokens": 2},
        )
        expected_actual = 4
    elif dimension_case == "tool_calls":
        overrides["max_cumulative_tool_calls"] = 1
        response = _response(
            "",
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            tool_calls=[{"id": "malformed-without-name"}],
        )
    else:
        overrides["max_provider_attempts"] = 1

    primary = _profile(
        "primary",
        fallback_profiles=["fallback"],
        **overrides,
    )
    fallback = _profile("fallback")
    chat_model = MagicMock()
    chat_model.invoke.return_value = response
    provider_factory = MagicMock(return_value=chat_model)
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = provider_factory
    cache = MagicMock()
    cache.get.return_value = None
    budget = MagicMock()
    metrics = MagicMock()
    tracer = MagicMock()
    gateway = ModelGateway(
        [primary, fallback],
        provider_registry=registry,
        request_token_counter=counter,
        response_cache=cache,
        budget_guard=budget,
        metrics_collector=metrics,
        metrics_agent_id="agent-1",
        langsmith_tracer=tracer,
    )

    with (
        patch("general_ludd.models.gateway.default_token_tracker") as token_tracker,
        pytest.raises(CumulativePayloadLimitError) as caught,
    ):
        gateway.call_model_with_fallback("primary", messages)

    assert (caught.value.stage, caught.value.dimension, caught.value.actual) == (
        expected_stage,
        expected_dimension,
        expected_actual,
    )
    assert caught.value.profile_id == "fallback"
    assert provider_factory.call_count == provider_calls
    assert chat_model.invoke.call_count == provider_calls
    token_tracker.assert_not_called()
    cache.set.assert_not_called()
    budget.record_spend.assert_not_called()
    metrics.record_model_call.assert_not_called()
    tracer.is_enabled.assert_not_called()


def test_request_byte_limit_uses_exact_compact_utf8_and_never_calls_provider() -> None:
    messages = [{"role": "user", "content": "café 🧪"}]
    exact_bytes = _compact_message_bytes(messages)

    accepting, _, accepting_model = _gateway(_profile(max_request_bytes=exact_bytes, max_input_tokens=exact_bytes))
    assert accepting.call_model("limited", messages).content == "ok"
    accepting_model.invoke.assert_called_once()

    rejecting, provider_factory, rejecting_model = _gateway(
        _profile(max_request_bytes=exact_bytes - 1, max_input_tokens=exact_bytes)
    )
    with pytest.raises(PayloadLimitError) as caught:
        rejecting.call_model("limited", messages)

    error = caught.value
    assert (error.stage, error.dimension, error.actual, error.limit, error.source) == (
        "request",
        "bytes",
        exact_bytes,
        exact_bytes - 1,
        "gateway",
    )
    provider_factory.assert_not_called()
    rejecting_model.invoke.assert_not_called()


def test_trusted_request_token_counter_rejects_before_provider_call() -> None:
    messages = [{"role": "user", "content": "small bytes, many model tokens"}]
    counter = MagicMock(return_value=7)
    gateway, provider_factory, chat_model = _gateway(
        _profile(max_input_tokens=6),
        request_token_counter=counter,
    )

    with pytest.raises(PayloadLimitError) as caught:
        gateway.call_model("limited", messages)

    assert caught.value.dimension == "tokens"
    assert caught.value.actual == 7
    assert caught.value.count_source == "configured_token_counter"
    counter.assert_called_once()
    provider_factory.assert_not_called()
    chat_model.invoke.assert_not_called()


def test_invalid_request_token_counter_fails_closed_to_utf8_byte_count() -> None:
    messages = [{"role": "user", "content": "🧪"}]
    exact_bytes = _compact_message_bytes(messages)
    counter = MagicMock(return_value=True)
    gateway, provider_factory, _ = _gateway(
        _profile(max_input_tokens=exact_bytes - 1),
        request_token_counter=counter,
    )

    with pytest.raises(PayloadLimitError) as caught:
        gateway.call_model("limited", messages)

    assert caught.value.actual == exact_bytes
    assert caught.value.count_source == "utf8_bytes_conservative"
    provider_factory.assert_not_called()


def test_failing_request_token_counter_fails_closed_without_provider_call() -> None:
    messages = [{"role": "user", "content": "safe fallback"}]
    exact_bytes = _compact_message_bytes(messages)
    counter = MagicMock(side_effect=RuntimeError("tokenizer unavailable"))
    gateway, provider_factory, _ = _gateway(
        _profile(max_input_tokens=exact_bytes - 1),
        request_token_counter=counter,
    )

    with pytest.raises(PayloadLimitError) as caught:
        gateway.call_model("limited", messages)

    assert caught.value.count_source == "utf8_bytes_conservative"
    provider_factory.assert_not_called()


def test_request_byte_limit_includes_forwarded_tool_schemas() -> None:
    messages = [{"role": "user", "content": "hi"}]
    message_bytes = _compact_message_bytes(messages)
    tools: list[dict[str, object]] = [
        {
            "type": "function",
            "function": {
                "name": "bounded",
                "description": "🧪" * 20,
                "parameters": {"type": "object"},
            },
        }
    ]
    gateway, provider_factory, chat_model = _gateway(
        _profile(max_request_bytes=message_bytes + 1, max_input_tokens=4096)
    )

    with pytest.raises(PayloadLimitError) as caught:
        gateway.call_model("limited", messages, tools=tools)

    assert caught.value.dimension == "bytes"
    assert caught.value.actual > message_bytes + 1
    provider_factory.assert_not_called()
    chat_model.invoke.assert_not_called()


@pytest.mark.parametrize(
    ("usage", "expected_source"),
    [
        ({"input_tokens": 1, "output_tokens": 3, "total_tokens": 4}, "provider_usage_metadata"),
        ({"input_tokens": 1}, "utf8_bytes_conservative"),
        ({"input_tokens": 1, "output_tokens": True}, "utf8_bytes_conservative"),
        ({"input_tokens": 1, "output_tokens": 1, "total_tokens": 99}, "utf8_bytes_conservative"),
    ],
)
def test_response_token_limit_rejects_before_billing_or_observability(
    usage: dict[str, object],
    expected_source: str,
) -> None:
    cache = MagicMock()
    cache.get.return_value = None
    budget = MagicMock()
    metrics = MagicMock()
    tracer = MagicMock()
    health = MagicMock()
    content = "abc"
    gateway, _, chat_model = _gateway(
        _profile(max_output_tokens=2),
        _response(content, usage=usage),
        response_cache=cache,
        budget_guard=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )

    with (
        patch("general_ludd.models.gateway.default_token_tracker") as token_tracker,
        pytest.raises(PayloadLimitError) as caught,
    ):
        gateway.call_model("limited", [{"role": "user", "content": "hi"}])

    assert caught.value.dimension == "tokens"
    assert caught.value.actual == 3
    assert caught.value.count_source == expected_source
    chat_model.invoke.assert_called_once()
    token_tracker.assert_not_called()
    _assert_no_post_response_side_effects(
        cache=cache,
        budget=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )


def test_response_byte_limit_counts_utf8_content_exactly() -> None:
    cache = MagicMock()
    cache.get.return_value = None
    budget = MagicMock()
    metrics = MagicMock()
    tracer = MagicMock()
    health = MagicMock()
    content = "é🧪"
    exact_bytes = len(content.encode("utf-8"))
    gateway, _, _ = _gateway(
        _profile(max_response_bytes=exact_bytes - 1),
        _response(content, usage={"input_tokens": 1, "output_tokens": 1}),
        response_cache=cache,
        budget_guard=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )

    with pytest.raises(PayloadLimitError) as caught:
        gateway.call_model("limited", [{"role": "user", "content": "hi"}])

    assert (caught.value.dimension, caught.value.actual, caught.value.limit) == (
        "bytes",
        exact_bytes,
        exact_bytes - 1,
    )
    _assert_no_post_response_side_effects(
        cache=cache,
        budget=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )


def test_response_byte_limit_includes_normalized_tool_arguments() -> None:
    tool_calls: list[dict[str, object]] = [{"id": "1", "name": "large_args", "args": {"value": "🧪" * 20}}]
    cache = MagicMock()
    cache.get.return_value = None
    budget = MagicMock()
    metrics = MagicMock()
    tracer = MagicMock()
    health = MagicMock()
    gateway, _, _ = _gateway(
        _profile(max_response_bytes=8, max_output_tokens=4096),
        _response("", usage={"input_tokens": 1, "output_tokens": 1}, tool_calls=tool_calls),
        response_cache=cache,
        budget_guard=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )

    with pytest.raises(PayloadLimitError) as caught:
        gateway.call_model("limited", [{"role": "user", "content": "hi"}])

    assert caught.value.dimension == "bytes"
    assert caught.value.actual > 8
    _assert_no_post_response_side_effects(
        cache=cache,
        budget=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )


def test_tool_call_count_limit_rejects_before_normalization_and_side_effects() -> None:
    tool_calls: list[dict[str, object]] = [
        {"id": "1", "name": "first", "args": {}},
        {"id": "2", "name": "second", "args": {}},
    ]
    cache = MagicMock()
    cache.get.return_value = None
    budget = MagicMock()
    metrics = MagicMock()
    tracer = MagicMock()
    health = MagicMock()
    gateway, _, _ = _gateway(
        _profile(max_tool_calls=1),
        _response("", tool_calls=tool_calls),
        response_cache=cache,
        budget_guard=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )

    with pytest.raises(PayloadLimitError) as caught:
        gateway.call_model("limited", [{"role": "user", "content": "hi"}])

    assert (caught.value.dimension, caught.value.actual, caught.value.limit) == ("tool_calls", 2, 1)
    _assert_no_post_response_side_effects(
        cache=cache,
        budget=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )


def test_oversized_cache_hit_is_rejected_without_provider_or_side_effects() -> None:
    cache = MagicMock()
    cache.get.return_value = {
        "content": "cache payload too large",
        "usage_metadata": {"input_tokens": 1, "output_tokens": 1},
        "cost_estimate": 99.0,
        "model_name": "test-model",
    }
    budget = MagicMock()
    metrics = MagicMock()
    tracer = MagicMock()
    health = MagicMock()
    gateway, provider_factory, chat_model = _gateway(
        _profile(max_response_bytes=4),
        response_cache=cache,
        budget_guard=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )

    with (
        patch("general_ludd.models.gateway.default_token_tracker") as token_tracker,
        pytest.raises(PayloadLimitError) as caught,
    ):
        gateway.call_model("limited", [{"role": "user", "content": "hi"}])

    assert caught.value.source == "cache"
    provider_factory.assert_not_called()
    chat_model.invoke.assert_not_called()
    token_tracker.assert_not_called()
    _assert_no_post_response_side_effects(
        cache=cache,
        budget=budget,
        metrics=metrics,
        tracer=tracer,
        health=health,
    )


def test_payload_error_is_not_swallowed_by_fallback_routing() -> None:
    messages = [{"role": "user", "content": "blocked before any model"}]
    primary = _profile("primary", max_request_bytes=1, fallback_profiles=["fallback"])
    fallback = _profile("fallback", max_request_bytes=1)
    registry = MagicMock()
    registry.is_installed.return_value = True
    gateway = ModelGateway([primary, fallback], provider_registry=registry)

    with pytest.raises(PayloadLimitError):
        gateway.call_model_with_fallback("primary", messages)

    registry.get_provider_class.assert_not_called()


def test_get_chat_model_uses_aliases_and_binds_tools_without_invocation() -> None:
    profile = _profile(
        credential_alias="model-key",
        api_base_alias="model-base",
    )
    registry = MagicMock()
    registry.is_installed.return_value = True
    raw_model = MagicMock()
    bound_model = MagicMock()
    raw_model.bind_tools.return_value = bound_model
    provider_factory = MagicMock(return_value=raw_model)
    registry.get_provider_class.return_value = provider_factory
    secrets = MagicMock()
    secrets.resolve.side_effect = lambda alias: {
        "model-key": "secret-value",
        "model-base": "https://api.openai.com/v1",
    }[alias]
    gateway = ModelGateway([profile], provider_registry=registry, secrets_manager=secrets)
    tools: list[dict[str, object]] = [{"type": "function", "function": {"name": "safe"}}]

    returned = gateway.get_chat_model("limited", tools=tools)

    assert isinstance(returned, _LimitedChatModel)
    assert returned._inner is bound_model
    provider_factory.assert_called_once()
    init_kwargs = dict(provider_factory.call_args.kwargs)
    timeout = init_kwargs.pop("request_timeout")
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0
    assert timeout.read == 60.0
    assert init_kwargs == {
        "model": "test-model",
        "api_key": "secret-value",
        "base_url": "https://api.openai.com/v1",
    }
    raw_model.bind_tools.assert_called_once_with(tools)
    raw_model.invoke.assert_not_called()


def test_get_chat_model_fails_closed_for_missing_registry_profile_and_provider() -> None:
    gateway = ModelGateway([_profile()])
    with pytest.raises(ValueError, match="not found"):
        gateway.get_chat_model("missing")
    with pytest.raises(ValueError, match="No provider registry"):
        gateway.get_chat_model("limited")

    registry = MagicMock()
    registry.is_installed.return_value = False
    gateway = ModelGateway([_profile()], provider_registry=registry)
    with pytest.raises(ImportError, match="not installed"):
        gateway.get_chat_model("limited")
    registry.install_provider.assert_called_once_with("test-provider")
    registry.get_provider_class.assert_not_called()


def test_get_chat_model_blocks_unsafe_alias_before_provider_construction() -> None:
    profile = _profile(api_base_alias="model-base")
    registry = MagicMock()
    registry.is_installed.return_value = True
    registry.get_provider_class.return_value = MagicMock()
    secrets = MagicMock()
    secrets.resolve.return_value = "http://127.0.0.1/private"
    gateway = ModelGateway([profile], provider_registry=registry, secrets_manager=secrets)

    with pytest.raises(SSRFRejectionError, match="SSRF guard"):
        gateway.get_chat_model("limited")

    registry.get_provider_class.return_value.assert_not_called()


def test_get_chat_model_omits_unresolved_aliases_and_warns_for_unbound_tools(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ChatModelWithoutTools:
        pass

    profile = _profile(
        credential_alias="missing-key",
        api_base_alias="missing-base",
    )
    registry = MagicMock()
    registry.is_installed.return_value = True
    raw_model = ChatModelWithoutTools()
    provider_factory = MagicMock(return_value=raw_model)
    registry.get_provider_class.return_value = provider_factory
    secrets = MagicMock()
    secrets.resolve.return_value = None
    gateway = ModelGateway([profile], provider_registry=registry, secrets_manager=secrets)

    returned = gateway.get_chat_model(
        "limited",
        tools=[{"type": "function", "function": {"name": "safe"}}],
    )

    assert isinstance(returned, _LimitedChatModel)
    assert returned._inner is raw_model
    provider_factory.assert_called_once()
    init_kwargs = dict(provider_factory.call_args.kwargs)
    timeout = init_kwargs.pop("request_timeout")
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0
    assert timeout.read == 60.0
    assert init_kwargs == {"model": "test-model"}
    assert "does not support bind_tools" in caplog.text


def test_get_chat_model_constructs_minimal_model_without_aliases_or_tools() -> None:
    profile = _profile(credential_alias=None, api_base_alias=None)
    registry = MagicMock()
    registry.is_installed.return_value = True
    raw_model = object()
    provider_factory = MagicMock(return_value=raw_model)
    registry.get_provider_class.return_value = provider_factory
    gateway = ModelGateway([profile], provider_registry=registry)

    returned = gateway.get_chat_model("limited")

    assert isinstance(returned, _LimitedChatModel)
    assert returned._inner is raw_model
    provider_factory.assert_called_once()
    init_kwargs = dict(provider_factory.call_args.kwargs)
    timeout = init_kwargs.pop("request_timeout")
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0
    assert timeout.read == 60.0
    assert init_kwargs == {"model": "test-model"}


def test_add_remove_profile_exposes_limits_and_notifies_without_payloads() -> None:
    event_bus = MagicMock()
    hooks = MagicMock()
    broadcaster = MagicMock()
    gateway = ModelGateway(
        event_bus=event_bus,
        hook_system=hooks,
        worker_broadcaster=broadcaster,
    )

    profile = gateway.add_profile(
        "dynamic",
        provider="local",
        model="bounded-model",
        api_metered=False,
        max_request_bytes=101,
        max_input_tokens=102,
        max_response_bytes=103,
        max_output_tokens=104,
        max_tool_calls=5,
        ignored_unknown="not-forwarded",
    )

    assert (
        profile.max_request_bytes,
        profile.max_input_tokens,
        profile.max_response_bytes,
        profile.max_output_tokens,
        profile.max_tool_calls,
    ) == (101, 102, 103, 104, 5)
    assert gateway.get_profile("dynamic") is profile
    event_bus.publish.assert_called_once()
    hooks.fire.assert_called_once_with(
        "on_model_added",
        {"model_id": "dynamic", "profile": profile.model_dump()},
    )
    broadcaster.broadcast_model_update.assert_called_once_with(
        "add",
        "dynamic",
        profile.model_dump(),
    )

    gateway.remove_profile("dynamic")

    assert gateway.get_profile("dynamic") is None
    assert event_bus.publish.call_count == 2
    hooks.fire.assert_called_with("on_model_removed", {"model_id": "dynamic"})
    broadcaster.broadcast_model_update.assert_called_with("remove", "dynamic", {})


def test_profile_change_survives_redacted_worker_broadcast_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event_bus = MagicMock()
    hooks = MagicMock()
    broadcaster = MagicMock()
    broadcaster.broadcast_model_update.side_effect = RuntimeError("https://private.example.invalid/token")
    gateway = ModelGateway(
        event_bus=event_bus,
        hook_system=hooks,
        worker_broadcaster=broadcaster,
    )

    profile = gateway.add_profile(
        "dynamic",
        provider="local",
        model="bounded-model",
        api_metered=False,
    )

    assert gateway.get_profile("dynamic") is profile
    event_bus.publish.assert_called_once()
    hooks.fire.assert_called_once()
    assert "Worker broadcast failed" in caplog.text
    assert "token" not in caplog.text


def test_profile_change_and_cost_selection_work_without_optional_observers() -> None:
    gateway = ModelGateway()
    profile = gateway.add_profile(
        "dynamic",
        provider="local",
        model="bounded-model",
        api_metered=False,
    )
    gateway.remove_profile("dynamic")

    assert gateway.get_profile("dynamic") is None
    assert (
        gateway.select_cost_effective_profile(
            [
                profile.model_copy(update={"enabled": False}),
                _profile(
                    "over-budget",
                    api_metered=True,
                    run_budget_usd=10.0,
                    cost_per_input_token=0.5,
                    cost_per_output_token=0.5,
                ),
                profile,
            ],
            budget_remaining=1.0,
        )
        is profile
    )
    assert (
        gateway.select_cost_effective_profile(
            [profile.model_copy(update={"enabled": False})],
            budget_remaining=1.0,
        )
        is None
    )
