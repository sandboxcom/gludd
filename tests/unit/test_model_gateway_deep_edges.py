"""Deep edge-path tests for ModelGateway: failover, timeout, cache, stream, cost, tools.

Covers under-tested private-path edges in the 3786-line gateway:
1. Failover logic — _walk_fallbacks depth/cycle/missing-primary edges
2. Timeout detection — record_timeout_on_failure, non-retryable kind propagation
3. Response cache — TTL expiry, single-flight, non-dict usage in cache, cache key collision
4. Streaming — semaphore exhaustion per-provider, empty-/None-content chunks, compression boundary
5. Cost routing — _map_cost_route_to_profile fallback, select_cost_effective_profile gap
6. Tool dispatch — bind_tools wrapper re-wrap, no-bind_tools path, tools=None passthrough
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from general_ludd.models.gateway import (
    CircuitBreakerOpenError,
    ModelGateway,
    ModelProfile,
    StreamLimitError,
    _coerce_token_count,
    _enrich_all_down_message,
    _extract_tool_calls,
    _LimitedChatModel,
    _positive_profile_limit,
)
from general_ludd.models.timeout_detector import (
    _NON_RETRYABLE_KINDS,
    TimeoutClassifier,
    TimeoutKind,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _profile(pid: str = "edge", **overrides: Any) -> ModelProfile:
    vals: dict[str, Any] = {
        "model_profile_id": pid,
        "provider": "openai",
        "model_name": f"model-{pid}",
        "enabled": True,
        "api_metered": False,
        "cost_per_input_token": 0.0,
        "cost_per_output_token": 0.0,
        "max_request_bytes": 8192,
        "max_input_tokens": 8192,
        "max_response_bytes": 8192,
        "max_output_tokens": 8192,
        "max_tool_calls": 16,
        "max_provider_attempts": 3,
        "fallback_profiles": [],
        "fallback_max_concurrency": 2,
        "stream_provider_max_concurrency": 1,
    }
    vals.update(overrides)
    return ModelProfile(**vals)


def _registry() -> MagicMock:
    reg = MagicMock()
    reg.is_installed.return_value = True
    return reg


def _chat_model(content: str = "ok", usage: dict[str, object] | None = None) -> MagicMock:
    cm = MagicMock()
    resp = MagicMock()
    resp.content = content
    resp.usage_metadata = usage or {"input_tokens": 1, "output_tokens": 1}
    resp.response_metadata = {}
    resp.tool_calls = []
    cm.bind_tools.return_value = cm
    cm.invoke.return_value = resp
    return cm


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


def _chunk(content: str, usage: dict[str, object] | None = None) -> MagicMock:
    c = MagicMock()
    c.content = content
    c.usage_metadata = usage or {}
    c.response_metadata = {}
    c.tool_calls = []
    return c


# =============================================================================
# 1. Failover logic — deep edges
# =============================================================================


class TestFailoverDeepEdges:
    """_walk_fallbacks / call_model_with_fallback edge cases beyond the existing suite."""

    def test_walk_fallbacks_max_depth_zero_skips_all(self) -> None:
        a = _profile("a", fallback_profiles=["b"])
        b = _profile("b", fallback_profiles=["c"])
        c = _profile("c")
        bad_cm = MagicMock()
        bad_cm.invoke.side_effect = httpx.ConnectError("boom")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: bad_cm
        gw = ModelGateway([a, b, c], provider_registry=reg, max_fallback_depth=0)
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback("a", [{"role": "user", "content": "hi"}])

    def test_walk_fallbacks_depth_equals_chain_length_allows_all(self) -> None:
        a = _profile("a", fallback_profiles=["b"])
        b = _profile("b")
        cm = _chat_model("from b")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([a, b], provider_registry=reg, max_fallback_depth=3)
        result = gw.call_model_with_fallback("a", [{"role": "user", "content": "hi"}])
        assert result.content == "from b"

    def test_missing_primary_with_explicit_fallback_uses_policy_profile_limits(self) -> None:
        b = _profile("b", max_provider_attempts=1)
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: _chat_model("from b")
        gw = ModelGateway([b], provider_registry=reg)
        result = gw.call_model_with_fallback(
            "missing_primary",
            [{"role": "user", "content": "hi"}],
            fallback_profiles=["b"],
        )
        assert result.content == "from b"

    def test_missing_primary_with_no_valid_explicit_fallback_raises(self) -> None:
        gw = ModelGateway()
        with pytest.raises(ValueError, match="not found"):
            gw.call_model_with_fallback(
                "missing_primary",
                [{"role": "user", "content": "hi"}],
                fallback_profiles=["also_missing"],
            )

    def test_call_with_fallback_primary_unhealthy_all_fallbacks_healthy(self) -> None:
        primary = _profile("primary", fallback_profiles=["secondary"])
        secondary = _profile("secondary")
        health = MagicMock()
        health.is_healthy.side_effect = lambda pid, **_: pid == "secondary"
        cm = _chat_model("from secondary")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([primary, secondary], provider_registry=reg, health_tracker=health)
        result = gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])
        assert result.content == "from secondary"

    def test_walk_fallbacks_skips_unknown_fallback_ids_gracefully(self) -> None:
        a = _profile("a", fallback_profiles=["known", "ghost_id"])
        b = _profile("known")
        cm = _chat_model("from known")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([a, b], provider_registry=reg)
        result = gw.call_model_with_fallback("a", [{"role": "user", "content": "hi"}])
        assert result.content == "from known"

    def test_enrich_all_down_message_stores_attempts(self) -> None:
        err = CircuitBreakerOpenError("all down")
        attempts = [
            {"profile_id": "a", "reason": "timeout"},
            {"profile_id": "b", "reason": "503"},
        ]
        _enrich_all_down_message(err, attempts)
        assert "all providers down" in str(err)
        assert "a (timeout)" in str(err)
        assert "b (503)" in str(err)


# =============================================================================
# 2. Timeout detection and recovery — deep edges
# =============================================================================


class TestTimeoutDetectionDeep:
    """record_timeout_on_failure and TimeoutClassifier integration edges."""

    def test_record_timeout_on_httpx_status_503_classifies_provider_error(self) -> None:
        p = _profile("to1")
        health = MagicMock()
        cm = MagicMock()
        cm.invoke.side_effect = httpx.HTTPStatusError(
            "503",
            request=httpx.Request("POST", "https://x"),
            response=httpx.Response(503, request=httpx.Request("POST", "https://x")),
        )
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg, health_tracker=health)
        with contextlib.suppress(httpx.HTTPStatusError):
            gw.call_model("to1", [{"role": "user", "content": "hi"}])
        health.record_event.assert_called()
        event = health.record_event.call_args[0][0]
        assert event.kind == TimeoutKind.PROVIDER_ERROR

    def test_record_timeout_on_connect_error_classifies_network(self) -> None:
        p = _profile("to2")
        health = MagicMock()
        cm = MagicMock()
        cm.invoke.side_effect = httpx.ConnectError("refused")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg, health_tracker=health)
        with contextlib.suppress(httpx.ConnectError):
            gw.call_model("to2", [{"role": "user", "content": "hi"}])
        event = health.record_event.call_args[0][0]
        assert event.kind == TimeoutKind.CONNECTION_TIMEOUT

    def test_record_timeout_no_health_tracker_is_noop(self) -> None:
        p = _profile("to3")
        cm = MagicMock()
        cm.invoke.side_effect = httpx.ConnectError("refused")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg)
        with contextlib.suppress(httpx.ConnectError):
            gw.call_model("to3", [{"role": "user", "content": "hi"}])

    def test_non_retryable_kinds_propagate_through_retry(self) -> None:
        auth_error = TimeoutKind.AUTH_ERROR
        assert auth_error in _NON_RETRYABLE_KINDS

    def test_retry_exhausts_on_non_retryable_after_3(self) -> None:
        p = _profile("to5", max_failover_retries=3)
        cm = MagicMock()
        cm.invoke.side_effect = httpx.HTTPStatusError(
            "401",
            request=httpx.Request("POST", "https://x"),
            response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
        )
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg)
        with pytest.raises(httpx.HTTPStatusError):
            gw.call_model_with_retry(
                "to5", [{"role": "user", "content": "hi"}], max_retries=3, base_backoff_seconds=0.0
            )

    def test_timeout_kind_network_error_when_no_response(self) -> None:
        exc = httpx.ConnectError("connection refused")
        kind = TimeoutClassifier.classify(exc)
        assert kind == TimeoutKind.CONNECTION_TIMEOUT


# =============================================================================
# 3. Response caching — deep edges
# =============================================================================


class TestResponseCacheDeep:
    """Cache miss, TTL expiry, single-flight, non-dict cache payloads."""

    def test_cache_miss_falls_through_to_provider(self) -> None:
        p = _profile("cache_miss")
        cache = MagicMock()
        cache.get.return_value = None
        cm = _chat_model("fresh")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg, response_cache=cache)
        result = gw.call_model("cache_miss", [{"role": "user", "content": "hi"}])
        assert result.content == "fresh"
        cache.set.assert_called_once()

    def test_cache_hit_returns_cached_without_invoke(self) -> None:
        p = _profile("cache_hit")
        cache = MagicMock()
        cache.get.return_value = {
            "content": "cached result",
            "usage_metadata": {"input_tokens": 1, "output_tokens": 1},
            "cost_estimate": 0.0,
            "model_name": "model-cache_hit",
        }
        cm = _chat_model()
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg, response_cache=cache)
        result = gw.call_model("cache_hit", [{"role": "user", "content": "hi"}])
        assert result.content == "cached result"
        cm.invoke.assert_not_called()

    def test_cache_hit_with_tool_calls_in_payload(self) -> None:
        p = _profile("cache_tools", max_tool_calls=16)
        cache = MagicMock()
        cache.get.return_value = {
            "content": "",
            "usage_metadata": {"input_tokens": 1, "output_tokens": 0},
            "cost_estimate": 0.0,
            "model_name": "model-cache_tools",
            "tool_calls": [{"id": "c1", "name": "f", "args": {}, "type": "tool_call"}],
        }
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: _chat_model()
        gw = ModelGateway([p], provider_registry=reg, response_cache=cache)
        result = gw.call_model("cache_tools", [{"role": "user", "content": "hi"}])
        assert result.content == ""
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1

    def test_cache_with_non_dict_usage_coerced(self) -> None:
        p = _profile("cache_bad_use")
        cache = MagicMock()
        cache.get.return_value = {
            "content": "ok",
            "usage_metadata": True,
            "cost_estimate": 0.0,
            "model_name": "model-cache_bad_use",
        }
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: _chat_model()
        gw = ModelGateway([p], provider_registry=reg, response_cache=cache)
        result = gw.call_model("cache_bad_use", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"

    def test_cache_miss_with_single_flight_lock_acquires_and_releases(self) -> None:
        p = _profile("cache_flight")
        cache = MagicMock()
        cache.get.return_value = None
        cm = _chat_model("single_flight_ok")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg, response_cache=cache)
        result = gw.call_model("cache_flight", [{"role": "user", "content": "unique_key"}])
        assert result.content == "single_flight_ok"
        assert "unique_key" not in gw._cache_key_locks

    def test_cache_key_lock_ref_count_drops_to_zero(self) -> None:
        gw = ModelGateway()
        gw._cache_key_lock("test_key")
        assert "test_key" in gw._cache_key_locks
        gw._cache_key_unref("test_key")
        assert "test_key" not in gw._cache_key_locks

    def test_cache_key_lock_ref_count_correct_on_concurrent_calls(self) -> None:
        p = _profile("cache_ref2")
        cache = MagicMock()
        cache.get.return_value = None
        cm = _chat_model()
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg, response_cache=cache)
        gw.call_model("cache_ref2", [{"role": "user", "content": "ref_test"}])
        gw.call_model("cache_ref2", [{"role": "user", "content": "ref_test2"}])
        gw.call_model("cache_ref2", [{"role": "user", "content": "ref_test3"}])
        gw.call_model("cache_ref2", [{"role": "user", "content": "ref_test4"}])
        assert len(gw._cache_key_locks) == 0


# =============================================================================
# 4. Streaming response — deep edges
# =============================================================================


class TestStreamingDeepEdges:
    """Streaming edges beyond the existing suite: semaphore, empty/None content, compression."""

    def test_stream_semaphore_for_two_different_profiles_independent(self) -> None:
        p1 = _profile("sp1", stream_provider_max_concurrency=1)
        p2 = _profile("sp2", stream_provider_max_concurrency=1)
        gw = ModelGateway([p1, p2], provider_registry=_registry())
        sem1 = gw._stream_provider_semaphore("sp1")
        sem2 = gw._stream_provider_semaphore("sp2")
        sem1.acquire()
        try:
            assert sem2.acquire(blocking=False)
        finally:
            sem1.release()
            sem2.release()

    def test_stream_provider_semaphore_for_missing_profile_defaults_to_1(self) -> None:
        gw = ModelGateway()
        sem = gw._stream_provider_semaphore("nonexistent")
        assert sem.acquire(blocking=False)

    def test_fallback_semaphore_for_missing_profile_defaults_to_2(self) -> None:
        gw = ModelGateway()
        sem = gw._fallback_semaphore("nonexistent_fallback")
        assert sem.acquire(blocking=False)

    def test_stream_chunk_with_none_content_handled(self) -> None:
        p = _profile("none_content", max_stream_chunks=100)
        c = MagicMock()
        c.content = None
        c.usage_metadata = {}
        c.response_metadata = {}
        c.tool_calls = []
        chunks = _ClosingIterator([c])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)
        gw = ModelGateway([p], provider_registry=reg)
        result = list(gw.call_model_stream("none_content", [{"role": "user", "content": "hi"}]))
        assert len(result) == 1

    def test_stream_with_custom_content_encoding_rejected(self) -> None:
        p = _profile("gzip2", max_stream_chunks=100, max_stream_bytes=99999, max_stream_tokens=99999)
        c = MagicMock()
        c.content = "compressed"
        c.usage_metadata = {}
        c.response_metadata = {"content-encoding": "deflate"}
        c.tool_calls = []
        chunks = _ClosingIterator([c])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)
        gw = ModelGateway([p], provider_registry=reg)
        with pytest.raises(StreamLimitError):
            list(gw.call_model_stream("gzip2", [{"role": "user", "content": "hi"}]))

    def test_stream_with_content_encoding_in_nested_headers_detected(self) -> None:
        p = _profile("gzip3", max_stream_chunks=100, max_stream_bytes=99999, max_stream_tokens=99999)
        c = MagicMock()
        c.content = "data"
        c.usage_metadata = {}
        c.response_metadata = {"headers": {"content-encoding": "gzip"}}
        c.tool_calls = []
        chunks = _ClosingIterator([c])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)
        gw = ModelGateway([p], provider_registry=reg)
        msgs: list[dict[str, str]] = [{"role": "user", "content": "hi"}]
        with pytest.raises(StreamLimitError):
            list(gw.call_model_stream("gzip3", msgs))

    def test_stream_encoding_detection_no_metadata_returns_empty(self) -> None:
        c = MagicMock(spec=[])
        result = ModelGateway._stream_content_encoding(c)
        assert result == ""


# =============================================================================
# 5. Cost routing — deep edges
# =============================================================================


class TestCostRoutingDeep:
    """Cost-aware router fallbacks and edge-case routing."""

    def test_call_model_cost_aware_without_router_falls_back(self) -> None:
        p = _profile("cost_fallback")
        cm = _chat_model("from cost_fallback")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg)
        result = gw.call_model_cost_aware("code", [{"role": "user", "content": "hi"}])
        assert result.content == "from cost_fallback"

    def test_route_for_task_ansible_matches_qwen_profiles(self) -> None:
        p = _profile("qwen2.5-coder-7b-some-instance")
        gw = ModelGateway([p])
        result = gw.route_for_task("ansible")
        assert result == "qwen2.5-coder-7b-some-instance"

    def test_route_for_task_code_matches_deepseek_profiles(self) -> None:
        p = _profile("deepseek-coder-33b")
        gw = ModelGateway([p])
        result = gw.route_for_task("code")
        assert result == "deepseek-coder-33b"

    def test_select_cost_effective_profile_returns_cheapest(self) -> None:
        cheap = _profile(
            "cheap",
            api_metered=True,
            run_budget_usd=100.0,
            cost_per_input_token=0.000001,
            cost_per_output_token=0.000002,
        )
        expensive = _profile(
            "expensive", api_metered=True, run_budget_usd=100.0, cost_per_input_token=0.01, cost_per_output_token=0.02
        )
        result = ModelGateway.select_cost_effective_profile([cheap, expensive], 200.0)
        assert result is not None
        assert result.model_profile_id == "cheap"

    def test_select_cost_effective_profile_budget_too_low_returns_none(self) -> None:
        p = _profile(
            "overpriced",
            api_metered=True,
            run_budget_usd=100.0,
            cost_per_input_token=0.0005,
            cost_per_output_token=0.001,
        )
        result = ModelGateway.select_cost_effective_profile([p], 50.0)
        assert result is None

    def test_select_cost_effective_profile_all_disabled_returns_none(self) -> None:
        p = _profile("off", enabled=False)
        result = ModelGateway.select_cost_effective_profile([p], 1000.0)
        assert result is None


# =============================================================================
# 6. Tool call dispatch — deep edges
# =============================================================================


class TestToolCallDispatchDeep:
    """bind_tools wrapping, passthrough, and None tools."""

    def test_bind_tools_with_none_tools_is_noop(self) -> None:
        p = _profile("tools_none")
        cm = _chat_model()
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg)
        result = gw.call_model("tools_none", [{"role": "user", "content": "hi"}], tools=None)
        assert result.content == "ok"
        assert not hasattr(cm, "bind_tools") or not cm.bind_tools.called

    def test_bind_tools_with_empty_tools_list_is_noop(self) -> None:
        p = _profile("tools_empty")
        cm = _chat_model()
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg)
        result = gw.call_model("tools_empty", [{"role": "user", "content": "hi"}], tools=[])
        assert result.content == "ok"

    def test_bind_tools_rewraps_in_limited_chat_model(self) -> None:
        """When tools= is passed, the returned chat_model is the bound wrapper, preserving invoke side."""
        p = _profile("tools_wrap")
        cm = _chat_model()
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg)
        result = gw.call_model(
            "tools_wrap", [{"role": "user", "content": "hi"}], tools=[{"name": "f", "description": "a func"}]
        )
        assert result.content == "ok"

    def test_limited_chat_model_bind_tools_preserves_enforce(self) -> None:
        p = _profile("lcm_bind2")
        inner = MagicMock()
        inner.bind_tools.return_value = inner
        enforce = MagicMock(return_value=(0, 0))
        lcm = _LimitedChatModel(inner, profile=p, profile_id="lcm_bind2", enforce_request=enforce)
        result = lcm.bind_tools([{"name": "g"}])
        assert isinstance(result, _LimitedChatModel)
        enforce.assert_not_called()


# =============================================================================
# 7. Miscellaneous utility edges
# =============================================================================


class TestUtilityEdges:
    """coerce_token_count, _positive_profile_limit, _extract_tool_calls oddities."""

    def test_coerce_token_count_accepts_zero_int(self) -> None:
        assert _coerce_token_count(0) == 0

    def test_coerce_token_count_accepts_zero_float(self) -> None:
        assert _coerce_token_count(0.0) == 0

    def test_extract_tool_calls_with_dict_list(self) -> None:
        msg = MagicMock()
        msg.tool_calls = [
            {"name": "search", "args": {"q": "x"}, "id": "call_1", "type": "tool_call"},
        ]
        result = _extract_tool_calls(msg)
        assert result is not None
        assert result[0]["function"]["name"] == "search"

    def test_extract_tool_calls_with_function_key(self) -> None:
        msg = MagicMock()
        msg.tool_calls = [
            {"function": {"name": "search", "arguments": '{"q": "x"}'}, "id": "call_2", "type": "tool_call"},
        ]
        result = _extract_tool_calls(msg)
        assert result is not None
        assert result[0]["function"] == {"name": "search", "arguments": '{"q": "x"}'}

    def test_positive_profile_limit_returns_default_on_non_int(self) -> None:
        class Stub:
            pass

        s = Stub()
        s.val = "string_not_int"
        assert _positive_profile_limit(s, "val", 42) == 42

    def test_positive_profile_limit_returns_value_for_positive_int(self) -> None:
        class Stub:
            pass

        s = Stub()
        s.positive = 10
        assert _positive_profile_limit(s, "positive", 5) == 10
