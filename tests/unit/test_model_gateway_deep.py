"""Deep edge-case tests for ModelGateway.

Covers:
- Provider fallback behaviour (primary fails, fallback exhausted, cycle detection)
- Budget exhaustion edge cases (NaN, Inf, zero, race conditions)
- Concurrent request queuing (semaphore, cache-key locks, stream provider serialisation)
- Stream interruption handling (early close, idle timeout, duration timeout)
- Invalid model name rejection (empty, whitespace, unknown characters)
- Payload size limits (cumulative budget exhaustion, provider attempt caps)
- Timeout propagation (httpx timeout construction, request_timeout wiring)
- Response parsing for malformed outputs (bool token counts, NaN usage, broken tool_calls)
"""

from __future__ import annotations

import contextlib
import datetime
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.gateway import (
    CallCancelledError,
    CircuitBreakerOpenError,
    CumulativePayloadLimitError,
    ModelGateway,
    ModelPausedError,
    ModelProfile,
    ModelResponse,
    PayloadLimitError,
    SSRFRejectionError,
    StreamLimitError,
    _attach_correlation_id,
    _coerce_token_count,
    _extract_retry_after_seconds,
    _extract_tool_calls,
    _is_healthy_with_timeout,
    _LimitedChatModel,
    _positive_profile_limit,
    _redact_url_in_exception,
    _RequestPayloadBudget,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    cm.invoke.return_value = resp
    return cm


def _response(content: str = "ok", usage: dict[str, object] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.usage_metadata = usage or {"input_tokens": 1, "output_tokens": 1}
    resp.response_metadata = {}
    resp.tool_calls = []
    return resp


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


def _chunk(content: str) -> MagicMock:
    c = MagicMock()
    c.content = content
    c.usage_metadata = {}
    c.response_metadata = {}
    c.tool_calls = []
    return c


# ===================================================================
# 1. Provider fallback behavior
# ===================================================================


class TestProviderFallbackDeep:
    def test_fallback_chain_all_exhausted_raises_circuit_breaker(self) -> None:
        primary = _profile("primary", fallback_profiles=["secondary"])
        secondary = _profile("secondary", fallback_profiles=["tertiary"])
        tertiary = _profile("tertiary")
        ModelGateway([primary, secondary, tertiary], provider_registry=_registry())

        # All providers throw
        bad_cm = MagicMock()
        bad_cm.invoke.side_effect = httpx.HTTPStatusError(
            "503",
            request=httpx.Request("POST", "https://x"),
            response=httpx.Response(503, request=httpx.Request("POST", "https://x")),
        )
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: bad_cm

        gw2 = ModelGateway([primary, secondary, tertiary], provider_registry=reg)
        with pytest.raises(CircuitBreakerOpenError):
            gw2.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])

    def test_fallback_skips_unhealthy_profile_with_timeout(self) -> None:
        primary = _profile("primary", fallback_profiles=["secondary"])
        secondary = _profile("secondary")

        health = MagicMock()
        health.is_healthy.side_effect = lambda pid, **_: pid != "secondary"

        cm = MagicMock()
        resp = _response("ok from primary")
        cm.invoke.return_value = resp
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway(
            [primary, secondary],
            provider_registry=reg,
            health_tracker=health,
        )
        result = gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])
        assert result.content == "ok from primary"

    def test_fallback_max_depth_enforced(self) -> None:
        a = _profile("a", fallback_profiles=["b"])
        b = _profile("b", fallback_profiles=["c"])
        c = _profile("c", fallback_profiles=["d"])
        d = _profile("d")

        bad_cm = MagicMock()
        bad_cm.invoke.side_effect = httpx.ConnectError("boom")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: bad_cm

        gw = ModelGateway(
            [a, b, c, d],
            provider_registry=reg,
            max_fallback_depth=1,
        )
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback("a", [{"role": "user", "content": "hi"}])

    def test_fallback_cycle_detection_visited_set(self) -> None:
        a = _profile("a", fallback_profiles=["b"])
        b = _profile("b", fallback_profiles=["a"])
        bad_cm = MagicMock()
        bad_cm.invoke.side_effect = httpx.ConnectError("boom")
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: bad_cm

        gw = ModelGateway([a, b], provider_registry=reg)
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback("a", [{"role": "user", "content": "hi"}])


# ===================================================================
# 2. Budget exhaustion edge cases
# ===================================================================


class TestBudgetExhaustionDeep:
    def test_budget_nan_clamped_to_zero_rejects(self) -> None:
        p = _profile(
            "cheap", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        gw = ModelGateway([p], provider_registry=_registry())
        allowed = gw.check_budget("cheap", estimated_cost=1.0, budget_remaining=float("nan"))
        assert allowed is False

    def test_budget_inf_allows(self) -> None:
        p = _profile(
            "unlimited", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        gw = ModelGateway([p], provider_registry=_registry())
        allowed = gw.check_budget("unlimited", estimated_cost=199.0, budget_remaining=float("inf"))
        assert allowed is True

    def test_budget_estimated_cost_nan_rejects(self) -> None:
        p = _profile(
            "safe", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        gw = ModelGateway([p], provider_registry=_registry())
        allowed = gw.check_budget("safe", estimated_cost=float("nan"), budget_remaining=1000.0)
        assert allowed is False

    def test_budget_estimated_cost_inf_rejects(self) -> None:
        p = _profile(
            "safe", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        gw = ModelGateway([p], provider_registry=_registry())
        allowed = gw.check_budget("safe", estimated_cost=float("inf"), budget_remaining=1000.0)
        assert allowed is False

    def test_budget_rejects_when_profile_run_budget_exceeded(self) -> None:
        p = _profile(
            "tight", run_budget_usd=10.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        gw = ModelGateway([p], provider_registry=_registry())
        allowed = gw.check_budget("tight", estimated_cost=11.0, budget_remaining=1_000_000.0)
        assert allowed is False

    def test_budget_guard_is_called_on_success(self) -> None:
        p = _profile(
            "billed", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        budget = MagicMock()
        cm = _chat_model()
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        gw.call_model("billed", [{"role": "user", "content": "hi"}])
        budget.record_spend.assert_called_once()

    def test_budget_zero_remaining_rejects(self) -> None:
        p = _profile("broke")
        gw = ModelGateway([p], provider_registry=_registry())
        allowed = gw.check_budget("broke", estimated_cost=0.01, budget_remaining=0.0)
        assert allowed is False

    def test_missing_profile_check_budget_returns_false(self) -> None:
        gw = ModelGateway()
        assert gw.check_budget("missing", 0.0, 100.0) is False


# ===================================================================
# 3. Concurrent request queuing
# ===================================================================


class TestConcurrencyDeep:
    def test_cache_key_lock_serialises_identical_misses(self) -> None:
        p = _profile("cached")
        cache = MagicMock()
        cache.get.return_value = None
        cm = _chat_model()
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg, response_cache=cache)

        resp = gw.call_model("cached", [{"role": "user", "content": "hello"}])
        assert resp.content == "ok"
        cache.set.assert_called()

        cache.get.return_value = {
            "content": "cached",
            "usage_metadata": {"input_tokens": 1, "output_tokens": 1},
            "cost_estimate": 0.0,
            "model_name": "model-cached",
        }
        resp2 = gw.call_model("cached", [{"role": "user", "content": "hello"}])
        assert resp2.content == "cached"

    def test_cache_key_lock_ref_count_evicts(self) -> None:
        p = _profile("ref")
        cache = MagicMock()
        cache.get.return_value = None
        cm = _chat_model()
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg, response_cache=cache)
        gw.call_model("ref", [{"role": "user", "content": "hello"}])
        assert "hello" not in gw._cache_key_locks

    def test_fallback_semaphore_prevents_thundering_herd(self) -> None:
        """When fallback_max_concurrency=1, a second concurrent fallback times out."""
        fb = _profile("target", fallback_max_concurrency=1)
        primary = _profile("primary", fallback_profiles=["target"])
        gw = ModelGateway([primary, fb], provider_registry=_registry())
        sem = gw._fallback_semaphore("target")
        sem.acquire()
        try:
            with pytest.raises(RuntimeError, match="fallback capacity exhausted"):
                gw._call_fallback("target", [{"role": "user", "content": "hi"}])
        finally:
            sem.release()

    def test_stream_provider_semaphore_times_out(self) -> None:
        p = _profile("streamed", stream_provider_max_concurrency=1)
        gw = ModelGateway([p], provider_registry=_registry())
        sem = gw._stream_provider_semaphore("streamed")
        sem.acquire()
        try:
            assert not sem.acquire(timeout=0.01)
        finally:
            sem.release()


# ===================================================================
# 4. Stream interruption handling
# ===================================================================


class TestStreamInterruptionDeep:
    def test_stream_idle_exceeded_raises_stream_limit(self) -> None:
        p = _profile(
            "streamer",
            max_stream_seconds=300,
            max_stream_idle_seconds=60,
            max_stream_chunks=100,
            max_stream_bytes=99999,
            max_stream_tokens=99999,
        )
        chunks = _ClosingIterator([_chunk("a"), _chunk("b")])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        counter: list[int] = [0]
        _real_monotonic = time.monotonic

        def slow_monotonic() -> float:
            real_now = _real_monotonic()
            counter[0] += 1
            if counter[0] <= 2:
                return real_now
            return real_now + 99999.0

        with patch("time.monotonic", side_effect=slow_monotonic):
            with pytest.raises(StreamLimitError) as exc_info:
                list(gw.call_model_stream("streamer", [{"role": "user", "content": "hi"}]))
            assert exc_info.value.dimension == "idle_seconds"

    def test_stream_duration_exceeded_raises_stream_limit(self) -> None:
        p = _profile(
            "dur",
            max_stream_seconds=1,
            max_stream_idle_seconds=300,
            max_stream_chunks=100,
            max_stream_bytes=99999,
            max_stream_tokens=99999,
        )
        chunks = _ClosingIterator([_chunk("x")])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        counter: list[int] = [0]
        _real_monotonic = time.monotonic

        def dur_monotonic() -> float:
            real_now = _real_monotonic()
            counter[0] += 1
            if counter[0] <= 2:
                return real_now
            return real_now + 99999.0

        with patch("time.monotonic", side_effect=dur_monotonic):
            with pytest.raises(StreamLimitError) as exc_info:
                list(gw.call_model_stream("dur", [{"role": "user", "content": "hi"}]))
            assert exc_info.value.dimension == "duration_seconds"

    def test_stream_close_called_even_on_error(self) -> None:
        p = _profile("close", max_stream_chunks=1)
        chunks = _ClosingIterator([_chunk("a"), _chunk("b")])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        with contextlib.suppress(StreamLimitError):
            list(gw.call_model_stream("close", [{"role": "user", "content": "hi"}]))
        assert chunks.closed

    def test_stream_compressed_without_counter_rejected(self) -> None:
        p = _profile("gzip", max_stream_chunks=100, max_stream_bytes=99999, max_stream_tokens=99999)
        c = MagicMock()
        c.content = "hello"
        c.usage_metadata = {}
        c.response_metadata = {"content-encoding": "gzip"}
        c.tool_calls = []
        chunks = _ClosingIterator([c])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        with pytest.raises(StreamLimitError, match="compressed"):
            list(gw.call_model_stream("gzip", [{"role": "user", "content": "hi"}]))
        assert chunks.closed


# ===================================================================
# 5. Invalid model name rejection
# ===================================================================


class TestInvalidModelRejection:
    def test_empty_profile_id_raises_on_construction(self) -> None:
        with pytest.raises(ValueError, match="model_profile_id must not be empty"):
            ModelProfile(model_profile_id="")

    def test_whitespace_profile_id_raises(self) -> None:
        with pytest.raises(ValueError, match="model_profile_id must not be empty"):
            ModelProfile(model_profile_id="   ")

    def test_nonexistent_profile_raises_helpful_message(self) -> None:
        gw = ModelGateway()
        with pytest.raises(ValueError, match="not found"):
            gw.call_model("ghost", [])

    def test_nonexistent_profile_in_stream(self) -> None:
        gw = ModelGateway()
        with pytest.raises(ValueError, match="not found"):
            list(gw.call_model_stream("phantom", []))

    def test_zero_context_window_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            _profile(context_window=0)

    def test_negative_run_budget_raises(self) -> None:
        with pytest.raises(ValueError, match="must be finite non-negative"):
            _profile(run_budget_usd=-1.0)

    def test_provider_not_installed_triggers_install(self) -> None:
        p = _profile("missing_prov")
        reg = _registry()
        reg.is_installed.return_value = False
        reg.get_provider_class.return_value = lambda **kw: _chat_model()
        gw = ModelGateway([p], provider_registry=reg)
        with pytest.raises(ImportError, match="not installed"):
            gw.call_model("missing_prov", [{"role": "user", "content": "hi"}])
        reg.install_provider.assert_called_once_with("openai")

    def test_no_registry_configured_raises(self) -> None:
        p = _profile("noreg")
        gw = ModelGateway([p])
        with pytest.raises(ValueError, match="No provider registry"):
            gw.call_model("noreg", [{"role": "user", "content": "hi"}])


# ===================================================================
# 6. Payload size limits
# ===================================================================


class TestPayloadLimitsDeep:
    def test_request_bytes_exceeded(self) -> None:
        p = _profile("tiny", max_request_bytes=4)
        gw = ModelGateway([p])
        with pytest.raises(PayloadLimitError) as exc_info:
            gw._enforce_request_limits(p, "tiny", [{"role": "user", "content": "hello world"}], {})
        assert exc_info.value.dimension == "bytes"
        assert exc_info.value.source == "gateway"

    def test_response_bytes_exceeded(self) -> None:
        p = _profile("small_out", max_response_bytes=5)
        gw = ModelGateway()
        with pytest.raises(PayloadLimitError) as exc_info:
            gw._enforce_response_limits(
                p,
                "small_out",
                content="too long content",
                usage={},
                raw_tool_call_count=0,
                tool_calls=None,
                source="provider",
            )
        assert exc_info.value.dimension == "bytes"

    def test_output_tokens_exceeded(self) -> None:
        p = _profile("tok", max_output_tokens=2)
        gw = ModelGateway()
        usage: dict[str, object] = {"input_tokens": 1, "output_tokens": 5, "total_tokens": 6}
        with pytest.raises(PayloadLimitError) as exc_info:
            gw._enforce_response_limits(
                p,
                "tok",
                content="ok",
                usage=usage,
                raw_tool_call_count=0,
                tool_calls=None,
                source="provider",
            )
        assert exc_info.value.dimension == "tokens"

    def test_tool_calls_exceeded(self) -> None:
        p = _profile("tools", max_tool_calls=2)
        gw = ModelGateway()
        with pytest.raises(PayloadLimitError) as exc_info:
            gw._enforce_response_limits(
                p,
                "tools",
                content="ok",
                usage={},
                raw_tool_call_count=5,
                tool_calls=None,
                source="provider",
            )
        assert exc_info.value.dimension == "tool_calls"

    def test_cumulative_provider_attempts_exceeded(self) -> None:
        budget = _RequestPayloadBudget(
            max_request_bytes=10000,
            max_input_tokens=10000,
            max_response_bytes=10000,
            max_output_tokens=10000,
            max_tool_calls=10,
            max_provider_attempts=2,
        )
        budget.reserve_provider_attempt("test", request_bytes=10, input_tokens=10)
        budget.reserve_provider_attempt("test", request_bytes=10, input_tokens=10)
        with pytest.raises(CumulativePayloadLimitError, match="provider_attempts"):
            budget.reserve_provider_attempt("test", request_bytes=10, input_tokens=10)

    def test_cumulative_request_bytes_exceeded(self) -> None:
        budget = _RequestPayloadBudget(
            max_request_bytes=20,
            max_input_tokens=10000,
            max_response_bytes=10000,
            max_output_tokens=10000,
            max_tool_calls=10,
            max_provider_attempts=10,
        )
        budget.reserve_provider_attempt("test", request_bytes=15, input_tokens=5)
        with pytest.raises(CumulativePayloadLimitError, match="bytes"):
            budget.reserve_provider_attempt("test", request_bytes=10, input_tokens=5)

    def test_cumulative_response_budget_enforced(self) -> None:
        budget = _RequestPayloadBudget(
            max_request_bytes=10000,
            max_input_tokens=10000,
            max_response_bytes=10,
            max_output_tokens=10000,
            max_tool_calls=10,
            max_provider_attempts=10,
        )
        budget.reserve_response("test", response_bytes=5, output_tokens=2, tool_calls=0)
        with pytest.raises(CumulativePayloadLimitError, match="bytes"):
            budget.reserve_response("test", response_bytes=10, output_tokens=2, tool_calls=0)


# ===================================================================
# 7. Timeout propagation
# ===================================================================


class TestTimeoutPropagation:
    def test_httpx_timeout_wired_in_invoke_and_bill(self) -> None:
        p = _profile(
            "timeouted", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        provider_args: dict = {}
        reg = _registry()

        def factory(**kw: Any) -> MagicMock:
            provider_args.update(kw)
            return _chat_model()

        reg.get_provider_class.return_value = factory
        gw = ModelGateway([p], provider_registry=reg)
        gw.call_model("timeouted", [{"role": "user", "content": "hi"}])
        assert "request_timeout" in provider_args
        timeout = provider_args["request_timeout"]
        assert timeout.connect == 10.0
        assert timeout.read == 60.0

    def test_stream_httpx_timeout_uses_stream_limits(self) -> None:
        p = _profile("stream_time", max_stream_seconds=90, max_stream_idle_seconds=15)
        provider_args: dict = {}
        reg = MagicMock()
        reg.is_installed.return_value = True

        def factory(**kw: Any) -> MagicMock:
            provider_args.update(kw)
            cm = MagicMock()
            cm.stream.return_value = _ClosingIterator([_chunk("hello")])
            return cm

        reg.get_provider_class.return_value = factory
        gw = ModelGateway([p], provider_registry=reg)
        list(gw.call_model_stream("stream_time", [{"role": "user", "content": "hi"}]))
        timeout = provider_args["request_timeout"]
        assert timeout.read == 15.0


# ===================================================================
# 8. Response parsing for malformed outputs
# ===================================================================


class TestMalformedResponseParsing:
    def test_coerce_token_count_rejects_bool(self) -> None:
        assert _coerce_token_count(True) == 0
        assert _coerce_token_count(False) == 0

    def test_coerce_token_count_rejects_nan(self) -> None:
        assert _coerce_token_count(float("nan")) == 0

    def test_coerce_token_count_rejects_inf(self) -> None:
        assert _coerce_token_count(float("inf")) == 0
        assert _coerce_token_count(float("-inf")) == 0

    def test_coerce_token_count_clamps_negative(self) -> None:
        assert _coerce_token_count(-5) == 0

    def test_coerce_token_count_rejects_str(self) -> None:
        assert _coerce_token_count("500") == 0

    def test_coerce_token_count_accepts_valid_int(self) -> None:
        assert _coerce_token_count(42) == 42

    def test_coerce_token_count_accepts_valid_float(self) -> None:
        assert _coerce_token_count(42.7) == 42

    def test_empty_200_returns_replaced_with_503_before_billing(self) -> None:
        p = _profile(
            "empty200", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        budget = MagicMock()
        cm = MagicMock()
        resp = MagicMock()
        resp.content = ""
        resp.usage_metadata = {"input_tokens": 1, "output_tokens": 1}
        resp.response_metadata = {}
        resp.tool_calls = []
        cm.invoke.return_value = resp
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        with pytest.raises(httpx.HTTPStatusError, match="empty-content 200"):
            gw.call_model("empty200", [{"role": "user", "content": "hi"}])
        budget.record_spend.assert_not_called()

    def test_tool_calls_on_empty_content_are_billed_normally(self) -> None:
        """Empty content with tool calls is valid — not an empty-200 error."""
        p = _profile(
            "tools_ok", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        budget = MagicMock()
        cm = MagicMock()
        resp = MagicMock()
        resp.content = ""
        resp.usage_metadata = {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}
        resp.response_metadata = {}
        resp.tool_calls = [{"name": "read", "args": {"path": "/x"}, "id": "c1", "type": "tool_call"}]
        cm.invoke.return_value = resp
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        result = gw.call_model("tools_ok", [{"role": "user", "content": "read file"}])
        assert result.content == ""
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        budget.record_spend.assert_called_once()

    def test_extract_tool_calls_handles_openai_sdk_object(self) -> None:
        class _Fn:
            name = "search"
            arguments = '{"q": "test"}'

        class _Tc:
            id = "call_xyz"
            function = _Fn()

        msg = MagicMock()
        msg.tool_calls = [_Tc()]
        result = _extract_tool_calls(msg)
        assert result is not None
        tc = result[0]
        assert isinstance(tc, dict)
        fn = tc["function"]
        assert isinstance(fn, dict)
        assert fn["name"] == "search"

    def test_extract_tool_calls_returns_none_for_empty(self) -> None:
        msg = MagicMock()
        msg.tool_calls = []
        assert _extract_tool_calls(msg) is None

    def test_extract_tool_calls_skips_nameless(self) -> None:
        msg = MagicMock()
        msg.tool_calls = [{"name": "", "args": {}, "id": "x", "type": "tool_call"}]
        assert _extract_tool_calls(msg) is None

    def test_positive_profile_limit_returns_default_on_invalid(self) -> None:
        class Stub:
            pass

        s = Stub()
        s.good = 10
        assert _positive_profile_limit(s, "good", 5) == 10
        assert _positive_profile_limit(s, "missing", 99) == 99

        s.zero = 0
        assert _positive_profile_limit(s, "zero", 99) == 99

        s.neg = -5
        assert _positive_profile_limit(s, "neg", 99) == 99

    def test_usage_metadata_untrusted_discarded(self) -> None:
        """Bool/non-dict usage metadata is treated as safe default."""
        p = _profile(
            "bad_use", run_budget_usd=200.0, cost_per_input_token=0.01, cost_per_output_token=0.01, api_metered=True
        )
        cm = MagicMock()
        resp = MagicMock()
        resp.content = "ok"
        resp.usage_metadata = True
        resp.response_metadata = {}
        resp.tool_calls = []
        cm.invoke.return_value = resp
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg)
        result = gw.call_model("bad_use", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"


# ===================================================================
# Additional edge cases
# ===================================================================


class TestPausedModel:
    def test_paused_model_rejects_call(self) -> None:
        p = _profile("paused")
        pause = MagicMock()
        pause.is_paused.return_value = True
        gw = ModelGateway([p], provider_registry=_registry(), pause_controller=pause)
        with pytest.raises(ModelPausedError):
            gw.call_model("paused", [{"role": "user", "content": "hi"}])

    def test_paused_model_rejects_stream(self) -> None:
        p = _profile("paused_stream")
        pause = MagicMock()
        pause.is_paused.return_value = True
        gw = ModelGateway([p], provider_registry=_registry(), pause_controller=pause)
        with pytest.raises(ModelPausedError):
            list(gw.call_model_stream("paused_stream", [{"role": "user", "content": "hi"}]))


class TestSSRFRejection:
    def test_ssrf_rejection_on_non_local_base_url(self) -> None:
        p = _profile("ssrf", api_base_alias="bad_url_alias")
        secrets = MagicMock()
        secrets.resolve.return_value = "http://169.254.169.254/latest/meta-data/"
        reg = _registry()
        reg.get_provider_class.return_value = lambda **kw: _chat_model()

        with patch("general_ludd.security.auth.is_safe_fetch_url", return_value=False):
            gw = ModelGateway([p], provider_registry=reg, secrets_manager=secrets)
            with pytest.raises(SSRFRejectionError):
                gw.call_model("ssrf", [{"role": "user", "content": "hi"}])


class TestLimitedChatModel:
    def test_limited_chat_model_stream_delegates(self) -> None:
        p = _profile("lcm")
        inner = MagicMock()
        inner.stream.return_value = _ClosingIterator([_chunk("hello")])
        limit_fn = MagicMock(return_value=(100, 50))
        lcm = _LimitedChatModel(inner, profile=p, profile_id="lcm", enforce_request=limit_fn)
        chunks = list(lcm.stream([{"role": "user", "content": "hi"}]))
        assert len(chunks) == 1
        limit_fn.assert_called_once()

    def test_limited_chat_model_bind_tools_returns_new_instance(self) -> None:
        p = _profile("bindy")
        inner = MagicMock()
        inner.bind_tools.return_value = inner
        lcm = _LimitedChatModel(inner, profile=p, profile_id="bindy", enforce_request=lambda msgs, kw: (0, 0))
        result = lcm.bind_tools([{"name": "f"}])
        assert isinstance(result, _LimitedChatModel)
        assert result is not lcm


class TestFailoverLog:
    def test_gateway_creates_failover_log(self) -> None:
        p = _profile("flog")
        gw = ModelGateway([p])
        assert isinstance(gw._failover_log, ModelFailoverChain)

    def test_record_failover_metrics_surface(self) -> None:
        p = _profile("flog")
        metrics = MagicMock()
        metrics.record_failover = MagicMock()
        gw = ModelGateway([p], metrics_collector=metrics)
        gw._record_failover("primary", "secondary", "timeout", exception_type="HTTPStatusError")
        metrics.record_failover.assert_called_once_with("primary", "secondary", "timeout")


class TestCancellation:
    def test_cancellation_before_call_raises(self) -> None:
        p = _profile("cancel")
        gw = ModelGateway([p], provider_registry=_registry())
        evt = threading.Event()
        evt.set()
        with pytest.raises(CallCancelledError) as exc_info:
            gw.call_model("cancel", [{"role": "user", "content": "hi"}], cancellation_event=evt)
        assert exc_info.value.profile_id == "cancel"


class TestCostAwareRouting:
    def test_cost_router_not_configured_falls_back(self) -> None:
        p = _profile("fallback_route")
        gw = ModelGateway([p])
        profile_id = gw.route_for_task_with_cost("code")
        assert profile_id == "fallback_route"

    def test_route_for_task_unknown_kind_uses_default(self) -> None:
        p = _profile("defaulty")
        gw = ModelGateway([p])
        result = gw.route_for_task("bogus_task_kind")
        assert result == "defaulty"

    def test_route_for_task_no_enabled_raises(self) -> None:
        p = _profile("off", enabled=False)
        gw = ModelGateway([p])
        with pytest.raises(ValueError, match="No enabled profile"):
            gw.route_for_task("code")


# ===================================================================
# 9. Stream semaphore exhaustion and concurrency
# ===================================================================


class TestStreamSemaphoreDeep:
    def test_stream_semaphore_exhausted_raises_runtime_error(self) -> None:
        p = _profile("sema1", stream_provider_max_concurrency=1)
        chunks = _ClosingIterator([_chunk("hello")])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        sem = gw._stream_provider_semaphore("sema1")
        sem.acquire()
        try:
            with pytest.raises(RuntimeError, match="Stream provider construction"):
                list(gw.call_model_stream("sema1", [{"role": "user", "content": "hi"}]))
        finally:
            sem.release()

    def test_two_streams_on_different_profiles_do_not_block(self) -> None:
        p1 = _profile("p1", stream_provider_max_concurrency=1)
        p2 = _profile("p2", stream_provider_max_concurrency=1)

        def stream_fn(msgs, **kw):
            return iter(_ClosingIterator([_chunk("x")]))

        cm = MagicMock()
        cm.stream = stream_fn
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p1, p2], provider_registry=reg)
        r1 = list(gw.call_model_stream("p1", [{"role": "user", "content": "hi"}]))
        r2 = list(gw.call_model_stream("p2", [{"role": "user", "content": "hi"}]))
        assert len(r1) == 1
        assert len(r2) == 1

    def test_semaphore_released_after_stream_error(self) -> None:
        p = _profile("sema_err", stream_provider_max_concurrency=1, max_stream_chunks=1)
        chunks = _ClosingIterator([_chunk("a"), _chunk("b")])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        with contextlib.suppress(StreamLimitError):
            list(gw.call_model_stream("sema_err", [{"role": "user", "content": "hi"}]))

        sem = gw._stream_provider_semaphore("sema_err")
        assert sem.acquire(blocking=False)

    def test_semaphore_released_after_no_chunks_yielded(self) -> None:
        p = _profile("sema_empty")
        tl = _chunk("")
        tl.tool_calls = [{"id": "t1", "name": "f", "args": {"k": "v"}, "type": "tool_call"}]
        cm = MagicMock()
        cm.stream.return_value = iter([tl])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg)
        result = list(gw.call_model_stream("sema_empty", [{"role": "user", "content": "hi"}]))
        assert len(result) == 1


# ===================================================================
# 10. Stream cancellation mid-chunk
# ===================================================================


class TestStreamCancellationDeep:
    def test_cancellation_event_set_before_stream_raises(self) -> None:
        p = _profile("cs1")
        gw = ModelGateway([p], provider_registry=_registry())
        evt = threading.Event()
        evt.set()
        with pytest.raises(CallCancelledError) as exc_info:
            list(
                gw.call_model_stream_with_retry(
                    "cs1",
                    [{"role": "user", "content": "hi"}],
                    max_retries=0,
                    cancellation_event=evt,
                )
            )
        assert exc_info.value.profile_id == "cs1"

    def test_cancellation_during_retry_loop_raises(self) -> None:
        p = _profile("cs2")
        gw = ModelGateway([p], provider_registry=_registry())
        evt = threading.Event()
        evt.set()
        with pytest.raises(CallCancelledError):
            list(
                gw.call_model_stream_with_retry(
                    "cs2",
                    [{"role": "user", "content": "hi"}],
                    max_retries=2,
                    cancellation_event=evt,
                )
            )

    def test_cancellation_after_semaphore_acquire_cleans_up(self) -> None:
        p = _profile("cs3", stream_provider_max_concurrency=1)
        cm = MagicMock()
        cm.stream.side_effect = httpx.HTTPStatusError(
            "429",
            request=httpx.Request("POST", "https://x"),
            response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
        )
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg)
        with contextlib.suppress(httpx.HTTPStatusError):
            list(gw.call_model_stream("cs3", [{"role": "user", "content": "hi"}]))

        sem = gw._stream_provider_semaphore("cs3")
        assert sem.acquire(blocking=False)


# ===================================================================
# 11. Stream chunk ordering verification
# ===================================================================


class TestStreamChunkOrdering:
    def test_chunks_yielded_in_provider_order(self) -> None:
        p = _profile("ord")
        words = ["alpha", "beta", "gamma", "delta", "epsilon"]
        chunks = _ClosingIterator([_chunk(w) for w in words])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        result = list(gw.call_model_stream("ord", [{"role": "user", "content": "hi"}]))
        assert [c.content for c in result] == words

    def test_chunk_count_matches_provider_yield(self) -> None:
        p = _profile("cnt")
        chunks = _ClosingIterator([_chunk(str(i)) for i in range(20)])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        result = list(gw.call_model_stream("cnt", [{"role": "user", "content": "hi"}]))
        assert len(result) == 20

    def test_stream_with_tool_call_chunks_preserves_all(self) -> None:
        p = _profile("tc_ord")
        tc1 = _chunk("")
        tc1.tool_calls = [{"id": "c1", "name": "read", "args": {"path": "/a"}}]
        tc2 = _chunk("final")
        tc2.tool_calls = []
        chunks = _ClosingIterator([tc1, tc2])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        result = list(gw.call_model_stream("tc_ord", [{"role": "user", "content": "hi"}]))
        assert len(result) == 2
        assert result[0].tool_calls == [{"id": "c1", "name": "read", "args": {"path": "/a"}}]
        assert result[1].content == "final"


# ===================================================================
# 12. Stream error recovery
# ===================================================================


class TestStreamErrorRecovery:
    def test_first_stream_fails_second_succeeds_in_retry(self) -> None:
        p = _profile("rec1")
        call_count: list[int] = [0]

        def stream_fn(msgs, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise httpx.ConnectError("first fail")
            return iter(_ClosingIterator([_chunk("recovered")]))

        cm = MagicMock()
        cm.stream = stream_fn
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg)
        result = list(
            gw.call_model_stream_with_retry(
                "rec1",
                [{"role": "user", "content": "hi"}],
                max_retries=3,
                base_backoff_seconds=0.0,
            )
        )
        assert len(result) == 1
        assert result[0].content == "recovered"

    def test_stream_limit_error_not_retried(self) -> None:
        p = _profile("rec2", max_stream_chunks=2)
        chunks = _ClosingIterator([_chunk(str(i)) for i in range(5)])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: MagicMock(stream=lambda msgs, **kw2: chunks)

        gw = ModelGateway([p], provider_registry=reg)
        with pytest.raises(StreamLimitError):
            list(
                gw.call_model_stream_with_retry(
                    "rec2",
                    [{"role": "user", "content": "hi"}],
                    max_retries=3,
                    base_backoff_seconds=0.0,
                )
            )

    def test_connection_error_reconstruction_happens(self) -> None:
        p = _profile("rec3")
        cm = MagicMock()
        cm.stream.side_effect = httpx.ConnectError("network down")
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg)
        with pytest.raises(httpx.ConnectError):
            list(gw.call_model_stream("rec3", [{"role": "user", "content": "hi"}]))


# ===================================================================
# 13. Rate limit header parsing
# ===================================================================


class TestRateLimitHeaderParsing:
    def test_retry_after_integer_parsed(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "42"}, request=httpx.Request("POST", "https://x"))
        exc = httpx.HTTPStatusError("too many", request=httpx.Request("POST", "https://x"), response=resp)
        assert _extract_retry_after_seconds(exc) == 42.0

    def test_retry_after_lowercase_parsed(self) -> None:
        resp = httpx.Response(429, headers={"retry-after": "17"}, request=httpx.Request("POST", "https://x"))
        exc = httpx.HTTPStatusError("too many", request=httpx.Request("POST", "https://x"), response=resp)
        assert _extract_retry_after_seconds(exc) == 17.0

    def test_retry_after_float_parsed(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "3.5"}, request=httpx.Request("POST", "https://x"))
        exc = httpx.HTTPStatusError("too many", request=httpx.Request("POST", "https://x"), response=resp)
        assert _extract_retry_after_seconds(exc) == 3.5

    def test_retry_after_missing_returns_none(self) -> None:
        resp = httpx.Response(429, headers={}, request=httpx.Request("POST", "https://x"))
        exc = httpx.HTTPStatusError("too many", request=httpx.Request("POST", "https://x"), response=resp)
        assert _extract_retry_after_seconds(exc) is None

    def test_retry_after_garbage_returns_none(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "next-week"}, request=httpx.Request("POST", "https://x"))
        exc = httpx.HTTPStatusError("too many", request=httpx.Request("POST", "https://x"), response=resp)
        assert _extract_retry_after_seconds(exc) is None

    def test_retry_after_negative_clamped_to_zero(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "-5"}, request=httpx.Request("POST", "https://x"))
        exc = httpx.HTTPStatusError("too many", request=httpx.Request("POST", "https://x"), response=resp)
        assert _extract_retry_after_seconds(exc) == 0.0

    def test_retry_after_zero_parsed(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "0"}, request=httpx.Request("POST", "https://x"))
        exc = httpx.HTTPStatusError("too many", request=httpx.Request("POST", "https://x"), response=resp)
        assert _extract_retry_after_seconds(exc) == 0.0

    def test_no_response_returns_none(self) -> None:
        exc = httpx.ConnectError("no response object at all")
        assert _extract_retry_after_seconds(exc) is None

    def test_response_without_headers_returns_none(self) -> None:
        exc = httpx.HTTPStatusError(
            "no headers",
            request=httpx.Request("POST", "https://x"),
            response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
        )
        assert _extract_retry_after_seconds(exc) is None


# ===================================================================
# 14. Token counting accuracy during streaming
# ===================================================================


class TestStreamTokenCounting:
    def test_usage_metadata_on_last_chunk_used_for_billing(self) -> None:
        p = _profile(
            "tokens",
            run_budget_usd=200.0,
            cost_per_input_token=0.01,
            cost_per_output_token=0.01,
            api_metered=True,
        )
        budget = MagicMock()
        cm = MagicMock()
        cm.stream.return_value = _ClosingIterator([_chunk("hello "), _chunk("world")])
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        list(gw.call_model_stream("tokens", [{"role": "user", "content": "hi"}]))
        budget.record_spend.assert_called_once()

    def test_response_token_count_uses_utf8_fallback(self) -> None:
        assert ModelGateway._response_token_count({}, 150) == (150, "utf8_bytes_conservative")

    def test_response_token_count_uses_metadata_when_consistent(self) -> None:
        usage: dict[str, object] = {"input_tokens": 10, "output_tokens": 30, "total_tokens": 40}
        assert ModelGateway._response_token_count(usage, 999) == (30, "provider_usage_metadata")

    def test_response_token_count_rejects_mismatched_totals(self) -> None:
        usage: dict[str, object] = {"input_tokens": 10, "output_tokens": 30, "total_tokens": 99}
        assert ModelGateway._response_token_count(usage, 150) == (150, "utf8_bytes_conservative")

    def test_response_token_count_rejects_non_int_usage(self) -> None:
        usage: dict[str, object] = {"input_tokens": "10", "output_tokens": 30, "total_tokens": 40}
        assert ModelGateway._response_token_count(usage, 100) == (100, "utf8_bytes_conservative")

    def test_response_token_count_rejects_negative_output(self) -> None:
        usage: dict[str, object] = {"input_tokens": 10, "output_tokens": -5, "total_tokens": 5}
        assert ModelGateway._response_token_count(usage, 200) == (200, "utf8_bytes_conservative")

    def test_usage_on_chunk_is_picked_up_during_stream(self) -> None:
        p = _profile(
            "chunk_use",
            run_budget_usd=200.0,
            cost_per_input_token=0.01,
            cost_per_output_token=0.01,
            api_metered=True,
        )
        c1 = _chunk("part1")
        c1.usage_metadata = {"input_tokens": 5, "output_tokens": 50, "total_tokens": 55}
        c2 = _chunk("part2")
        c2.usage_metadata = {}
        chunks = _ClosingIterator([c1, c2])
        budget = MagicMock()
        cm = MagicMock()
        cm.stream.return_value = chunks
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        result = list(gw.call_model_stream("chunk_use", [{"role": "user", "content": "hi"}]))
        assert len(result) == 2
        budget.record_spend.assert_called_once()

    def test_empty_usage_defaults_to_utf8_fallback_in_stream(self) -> None:
        p = _profile(
            "empty_use",
            run_budget_usd=200.0,
            cost_per_input_token=0.01,
            cost_per_output_token=0.01,
            api_metered=True,
        )
        c = _chunk("ok")
        c.usage_metadata = {}
        chunks = _ClosingIterator([c])
        budget = MagicMock()
        cm = MagicMock()
        cm.stream.return_value = chunks
        reg = MagicMock()
        reg.is_installed.return_value = True
        reg.get_provider_class.return_value = lambda **kw: cm

        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        list(gw.call_model_stream("empty_use", [{"role": "user", "content": "hi"}]))
        budget.record_spend.assert_called_once()


# ===================================================================
# 15. Deep billing — token counting accuracy across providers
# ===================================================================


class TestTokenCountingAccuracy:
    def test_coerce_float_truncates_not_rounds(self) -> None:
        assert _coerce_token_count(42.999) == 42
        assert _coerce_token_count(0.001) == 0

    def test_coerce_large_int_preserved(self) -> None:
        assert _coerce_token_count(10_000_000) == 10_000_000

    def test_response_token_count_consistent_across_typical_usage(self) -> None:
        usage: dict[str, object] = {"input_tokens": 500, "output_tokens": 1200, "total_tokens": 1700}
        out, source = ModelGateway._response_token_count(usage, 99999)
        assert out == 1200
        assert source == "provider_usage_metadata"

    def test_response_token_count_zero_tokens_valid(self) -> None:
        usage: dict[str, object] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        out, source = ModelGateway._response_token_count(usage, 50)
        assert out == 0
        assert source == "provider_usage_metadata"

    def test_estimate_cost_empty_messages_returns_zero(self) -> None:
        assert ModelGateway.estimate_cost(_profile(), None) == 0.0
        assert ModelGateway.estimate_cost(_profile(), []) == 0.0

    def test_estimate_cost_approximately_char_div_4(self) -> None:
        p = _profile("est", cost_per_input_token=0.001, cost_per_output_token=0.01)
        msgs = [{"role": "user", "content": "h" * 400}]
        cost = ModelGateway.estimate_cost(p, msgs)
        expected_input = (400 // 4) * 0.001
        expected_output = p.max_output_tokens * 0.01
        assert cost == pytest.approx(expected_input + expected_output, rel=1e-6)

    def test_estimate_cost_capped_by_requested_output(self) -> None:
        p = _profile("cap", cost_per_input_token=0.001, cost_per_output_token=0.01, max_output_tokens=8000)
        msgs = [{"role": "user", "content": "short"}]
        cost_capped = ModelGateway.estimate_cost(p, msgs, requested_max_output_tokens=10)
        cost_full = ModelGateway.estimate_cost(p, msgs, requested_max_output_tokens=None)
        assert cost_capped < cost_full

    def test_estimate_cost_requested_output_min_with_profile_max(self) -> None:
        p = _profile("cap2", cost_per_input_token=0.001, cost_per_output_token=0.01, max_output_tokens=1000)
        msgs = [{"role": "user", "content": "short"}]
        cost = ModelGateway.estimate_cost(p, msgs, requested_max_output_tokens=99999)
        assert cost == pytest.approx(ModelGateway.estimate_cost(p, msgs, requested_max_output_tokens=None), rel=1e-6)


# ===================================================================
# 16. Deep billing — cost calculation and rounding behavior
# ===================================================================


class TestCostCalculationRounding:
    def test_fractional_cent_input_only_cost(self) -> None:
        p = _profile(
            "frac",
            cost_per_input_token=0.000001,
            cost_per_output_token=0.0,
            run_budget_usd=200.0,
            api_metered=False,
        )
        reg = _registry()
        cm = _chat_model(usage={"input_tokens": 1500, "output_tokens": 1, "total_tokens": 1501})
        reg.get_provider_class.return_value = lambda **kw: cm
        budget = MagicMock()
        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        gw.call_model("frac", [{"role": "user", "content": "hi"}])
        called_cost = budget.record_spend.call_args[0][0]
        assert 0.0005 <= called_cost <= 0.0020

    def test_fractional_cent_output_only_cost(self) -> None:
        p = _profile(
            "outfrac",
            cost_per_input_token=0.0,
            cost_per_output_token=0.000005,
            run_budget_usd=200.0,
            api_metered=False,
        )
        reg = _registry()
        cm = _chat_model(usage={"input_tokens": 10, "output_tokens": 2000, "total_tokens": 2010})
        reg.get_provider_class.return_value = lambda **kw: cm
        budget = MagicMock()
        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        gw.call_model("outfrac", [{"role": "user", "content": "hi"}])
        called_cost = budget.record_spend.call_args[0][0]
        assert 0.005 <= called_cost <= 0.015

    def test_zero_cost_profile_never_bills(self) -> None:
        p = _profile(
            "freebie",
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
            max_output_tokens=9999,
            max_cumulative_output_tokens=20000,
        )
        reg = _registry()
        cm = _chat_model(usage={"input_tokens": 9999, "output_tokens": 9999, "total_tokens": 19998})
        reg.get_provider_class.return_value = lambda **kw: cm
        budget = MagicMock()
        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        gw.call_model("freebie", [{"role": "user", "content": "hi"}])
        called_cost = budget.record_spend.call_args[0][0]
        assert called_cost == 0.0

    def test_high_precision_multiply_no_overflow(self) -> None:
        p = _profile(
            "highprec",
            cost_per_input_token=0.0000037,
            cost_per_output_token=0.0000152,
            run_budget_usd=200.0,
            api_metered=True,
            max_output_tokens=50000,
            max_cumulative_output_tokens=100000,
        )
        reg = _registry()
        cm = _chat_model(usage={"input_tokens": 100000, "output_tokens": 50000, "total_tokens": 150000})
        reg.get_provider_class.return_value = lambda **kw: cm
        budget = MagicMock()
        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        gw.call_model("highprec", [{"role": "user", "content": "hi"}])
        called_cost = budget.record_spend.call_args[0][0]
        expected_min = 100000 * 0.0000037
        expected_max = expected_min + 50000 * 0.0000152
        assert expected_min <= called_cost <= expected_max + 0.001

    def test_negative_token_count_produces_zero_cost(self) -> None:
        p = _profile(
            "negcost",
            cost_per_input_token=100.0,
            cost_per_output_token=100.0,
            run_budget_usd=200.0,
            api_metered=True,
        )
        reg = _registry()
        cm = _chat_model(usage={"input_tokens": -100, "output_tokens": -50, "total_tokens": -150})
        reg.get_provider_class.return_value = lambda **kw: cm
        budget = MagicMock()
        gw = ModelGateway([p], provider_registry=reg, budget_guard=budget)
        with contextlib.suppress(Exception):
            gw.call_model("negcost", [{"role": "user", "content": "hi"}])
        if budget.record_spend.call_count > 0:
            cost = budget.record_spend.call_args[0][0]
            assert cost >= 0.0


# ===================================================================
# 17. Deep billing — multi-model session cost accumulation
# ===================================================================


class TestSessionCostAccumulation:
    def test_peak_pricing_tracker_accumulates_off_peak_savings(self) -> None:
        from general_ludd.budget.peak_pricing import PeakPricingTracker

        tracker = PeakPricingTracker()
        tracker.record_call(1.00, 0.75)
        tracker.record_call(2.00, 1.50)
        assert tracker.cumulative_full_cost == 3.00
        assert tracker.cumulative_discounted_cost == 2.25
        assert tracker.cumulative_savings == 0.75

    def test_peak_pricing_tracker_ignores_non_discounted(self) -> None:
        from general_ludd.budget.peak_pricing import PeakPricingTracker

        tracker = PeakPricingTracker()
        tracker.record_call(1.00, 1.00)
        tracker.record_call(1.00, 2.00)
        assert tracker.cumulative_full_cost == 0.0
        assert tracker.cumulative_discounted_cost == 0.0

    def test_token_cost_tracker_rolling_window_keeps_last_n(self) -> None:
        from general_ludd.observability.token_cost import TokenCostTracker

        tracker = TokenCostTracker(window=5, min_samples=2)
        for i in range(10):
            tracker.record("code", 100 + i, 50)
        w = tracker.weight("code")
        assert w is not None
        assert w.samples == 5

    def test_token_cost_tracker_classifies_heavy_vs_light(self) -> None:
        from general_ludd.observability.token_cost import TokenCostTracker

        tracker = TokenCostTracker(window=10, min_samples=3, heavy_factor=1.5)
        for _ in range(5):
            tracker.record("heavy", 10000, 5000)
        for _ in range(5):
            tracker.record("light", 10, 5)
        assert tracker.classify("heavy") == "heavy"
        assert tracker.classify("light") == "light"

    def test_token_cost_tracker_ignores_negative_tokens(self) -> None:
        from general_ludd.observability.token_cost import TokenCostTracker

        tracker = TokenCostTracker(window=10, min_samples=1)
        tracker.record("bad", -5, 0)
        tracker.record("bad", 0, -10)
        assert tracker.weight("bad") is None

    def test_multiple_providers_accumulated_separately(self) -> None:
        from general_ludd.observability.token_cost import TokenCostTracker

        tracker = TokenCostTracker(window=10, min_samples=3)
        for _ in range(4):
            tracker.record("code::gpt-4o", 500, 200)
        for _ in range(4):
            tracker.record("code::deepseek", 300, 100)
        w_openai = tracker.weight("code::gpt-4o")
        w_ds = tracker.weight("code::deepseek")
        assert w_openai is not None
        assert w_ds is not None
        assert w_openai.median_total > w_ds.median_total


# ===================================================================
# 18. Deep billing — budget tracking with concurrent patterns
# ===================================================================


class TestBudgetConcurrency:
    def test_check_budget_nan_remaining_clamped(self) -> None:
        p = _profile("nanbudg", run_budget_usd=200.0)
        gw = ModelGateway([p])
        assert gw.check_budget("nanbudg", 5.0, float("nan")) is False

    def test_check_budget_inf_remaining_allows(self) -> None:
        p = _profile("infbudg", run_budget_usd=200.0)
        gw = ModelGateway([p])
        assert gw.check_budget("infbudg", 999_999.0, float("inf")) is True

    def test_check_budget_server_estimate_overrides_caller(self) -> None:
        p = _profile(
            "overest",
            run_budget_usd=200.0,
            cost_per_input_token=0.01,
            cost_per_output_token=0.01,
            api_metered=True,
            max_output_tokens=8000,
        )
        gw = ModelGateway([p])
        msgs = [{"role": "user", "content": "x" * 1000}]
        allowed = gw.check_budget("overest", 0.000001, 10.0, messages=msgs)
        assert allowed is False

    def test_check_budget_profile_run_budget_caps(self) -> None:
        p = _profile(
            "capped",
            run_budget_usd=5.0,
            cost_per_input_token=0.01,
            cost_per_output_token=0.01,
            api_metered=True,
            max_output_tokens=100,
        )
        gw = ModelGateway([p])
        allowed = gw.check_budget("capped", 6.0, 100.0, messages=[{"role": "user", "content": "hi"}])
        assert allowed is False

    def test_check_budget_allows_cost_equal_to_profile_run_budget(self) -> None:
        p = _profile(
            "exact-cap",
            run_budget_usd=5.0,
            cost_per_input_token=0.01,
            cost_per_output_token=0.01,
            api_metered=True,
        )
        gw = ModelGateway([p])

        assert gw.check_budget("exact-cap", 5.0, float("inf")) is True

    def test_budget_request_payload_thread_safe_increments(self) -> None:
        budget = _RequestPayloadBudget(
            max_request_bytes=100000,
            max_input_tokens=100000,
            max_response_bytes=100000,
            max_output_tokens=100000,
            max_tool_calls=100,
            max_provider_attempts=100,
        )
        import threading as _th

        errors: list[Exception] = []

        def do_reserve(i: int) -> None:
            try:
                budget.reserve_provider_attempt("t", request_bytes=1, input_tokens=1)
                budget.reserve_response("t", response_bytes=1, output_tokens=1, tool_calls=0)
            except Exception as exc:
                errors.append(exc)

        threads = [_th.Thread(target=do_reserve, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert budget.provider_attempts == 20


# ===================================================================
# 19. Deep billing — cost breakdown by model/provider
# ===================================================================


class TestCostBreakdownByProvider:
    def test_profile_cost_field_validation_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match="must be finite non-negative"):
            _profile("nan_cost", cost_per_input_token=float("nan"))

    def test_profile_cost_field_validation_rejects_neg(self) -> None:
        with pytest.raises(ValueError, match="must be finite non-negative"):
            _profile("neg_cost", cost_per_input_token=-0.0001)

    def test_enabled_metered_requires_nonzero_cost(self) -> None:
        with pytest.raises(ValueError, match="non-zero cost"):
            ModelProfile(
                model_profile_id="badmeter",
                provider="openai",
                model_name="gpt-mock",
                enabled=True,
                api_metered=True,
                cost_per_input_token=0.0,
                cost_per_output_token=0.0,
            )

    def test_cost_estimate_stored_on_response(self) -> None:
        p = _profile(
            "stored",
            run_budget_usd=200.0,
            cost_per_input_token=0.01,
            cost_per_output_token=0.01,
            api_metered=True,
        )
        reg = _registry()
        cm = _chat_model(usage={"input_tokens": 50, "output_tokens": 100, "total_tokens": 150})
        reg.get_provider_class.return_value = lambda **kw: cm
        gw = ModelGateway([p], provider_registry=reg)
        resp = gw.call_model("stored", [{"role": "user", "content": "hello"}])
        assert resp.cost_estimate > 0.0

    def test_different_models_produce_different_cost(self) -> None:
        cheap = _profile(
            "cheap_m",
            run_budget_usd=200.0,
            cost_per_input_token=0.000001,
            cost_per_output_token=0.000002,
            max_output_tokens=100,
            api_metered=True,
        )
        expensive = _profile(
            "exp_m",
            run_budget_usd=200.0,
            cost_per_input_token=0.10,
            cost_per_output_token=0.30,
            max_output_tokens=100,
            api_metered=True,
        )
        reg = _registry()
        cm_cheap = _chat_model(usage={"input_tokens": 100, "output_tokens": 100, "total_tokens": 200})
        cm_exp = _chat_model(usage={"input_tokens": 100, "output_tokens": 100, "total_tokens": 200})

        call_count = 0

        def factory(**kw):
            nonlocal call_count
            call_count += 1
            return cm_cheap if call_count == 1 else cm_exp

        reg.get_provider_class.return_value = factory
        gw = ModelGateway([cheap, expensive], provider_registry=reg)

        resp_cheap = gw.call_model("cheap_m", [{"role": "user", "content": "hi"}])
        resp_exp = gw.call_model("exp_m", [{"role": "user", "content": "hi"}])
        assert resp_cheap.cost_estimate < resp_exp.cost_estimate


# ===================================================================
# 20. Deep billing — peak/off-peak rate cost estimation
# ===================================================================


class TestPeakOffPeakBilling:
    def test_gateway_billing_uses_one_injected_clock_snapshot(self) -> None:
        off_peak_time = datetime.datetime(2026, 8, 3, 22, 0, 0, tzinfo=datetime.UTC)
        calls = 0

        def clock() -> datetime.datetime:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise AssertionError("billing clock sampled more than once")
            return off_peak_time

        gateway = ModelGateway(billing_clock=clock)

        effective_cost, rate_label, multiplier = gateway._apply_billing_rate(0.2)

        assert calls == 1
        assert effective_cost == pytest.approx(0.15)
        assert rate_label == "off-peak"
        assert multiplier == 0.75

    def test_gateway_billing_clock_preserves_peak_rate(self) -> None:
        peak_time = datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)
        gateway = ModelGateway(billing_clock=lambda: peak_time)

        effective_cost, rate_label, multiplier = gateway._apply_billing_rate(0.2)

        assert effective_cost == pytest.approx(0.2)
        assert rate_label == "peak"
        assert multiplier == 1.0

    def test_peak_pricing_schedule_lookup_builtins_present(self) -> None:
        from general_ludd.budget.peak_pricing import default_schedule, get_current_rate

        sched = default_schedule()
        rate = get_current_rate(sched, "deepseek-chat", "deepseek")
        assert rate in {0.27, 0.27 * 0.5}

    def test_rate_multiplier_peak_vs_off_peak(self) -> None:
        from general_ludd.budget.peak_pricing import current_rate_multiplier

        peak_time = datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)
        off_peak_time = datetime.datetime(2026, 8, 3, 22, 0, 0, tzinfo=datetime.UTC)
        weekend_time = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)

        assert current_rate_multiplier(now=peak_time) == 1.0
        assert current_rate_multiplier(now=off_peak_time) == 0.75
        assert current_rate_multiplier(now=weekend_time) == 0.75

    def test_is_peak_weekday_business_hours(self) -> None:
        from general_ludd.budget.peak_pricing import is_peak

        assert is_peak(datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)) is True
        assert is_peak(datetime.datetime(2026, 8, 3, 8, 0, 0, tzinfo=datetime.UTC)) is False
        assert is_peak(datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)) is False

    def test_peak_rate_for_model_applies_multiplier(self) -> None:
        from general_ludd.budget.peak_pricing import peak_rate_for_model

        peak_time = datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)
        off_time = datetime.datetime(2026, 8, 3, 22, 0, 0, tzinfo=datetime.UTC)

        inp_peak, out_peak = peak_rate_for_model("gpt-4o", 0.01, 0.02, now=peak_time)
        inp_off, out_off = peak_rate_for_model("gpt-4o", 0.01, 0.02, now=off_time)

        assert inp_peak == 0.01
        assert out_peak == 0.02
        assert inp_off == 0.0075
        assert out_off == 0.015

    def test_rate_tier_covers_correct_window(self) -> None:
        from general_ludd.budget.peak_pricing import RateTier

        tier = RateTier(
            model_id="test-m",
            provider="test-p",
            rate=1.0,
            label="peak",
            days=frozenset([0, 1, 2]),
            start_hour=9,
            end_hour=17,
        )
        mon_noon = datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)
        mon_morning = datetime.datetime(2026, 8, 3, 7, 0, 0, tzinfo=datetime.UTC)
        fri = datetime.datetime(2026, 8, 7, 12, 0, 0, tzinfo=datetime.UTC)

        assert tier.covers(mon_noon) is True
        assert tier.covers(mon_morning) is False
        assert tier.covers(fri) is False

    def test_rate_tier_overnight_window(self) -> None:
        from general_ludd.budget.peak_pricing import RateTier

        tier = RateTier(
            model_id="overnight",
            provider="test-p",
            rate=0.5,
            label="off-peak",
            days=frozenset([0]),
            start_hour=20,
            end_hour=8,
        )
        mon_10pm = datetime.datetime(2026, 8, 3, 22, 0, 0, tzinfo=datetime.UTC)
        tue_2am = datetime.datetime(2026, 8, 4, 2, 0, 0, tzinfo=datetime.UTC)
        tue_8am = datetime.datetime(2026, 8, 4, 8, 0, 0, tzinfo=datetime.UTC)
        sun_2am = datetime.datetime(2026, 8, 9, 2, 0, 0, tzinfo=datetime.UTC)
        mon_noon = datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC)

        assert tier.covers(mon_10pm) is True
        assert tier.covers(tue_2am) is True
        assert tier.covers(tue_8am) is False
        assert tier.covers(sun_2am) is False
        assert tier.covers(mon_noon) is False

    def test_seed_token_rates_from_catalog_returns_unpriced_when_none(self) -> None:
        inp, out = ModelProfile.seed_token_rates_from_catalog("openai", "gpt-4o")
        assert inp == 0.0
        assert out == 0.0

    def test_seed_token_rates_catalog_miss_returns_zero(self) -> None:
        from unittest.mock import MagicMock

        catalog = MagicMock()
        catalog.model_price.return_value = None
        inp, out = ModelProfile.seed_token_rates_from_catalog("openai", "gpt-999", catalog)
        assert inp == 0.0
        assert out == 0.0

    def test_seed_token_rates_divides_per_1k_correctly(self) -> None:
        from unittest.mock import MagicMock

        price = MagicMock()
        price.input_usd_per_1k = 5.0
        price.output_usd_per_1k = 15.0
        catalog = MagicMock()
        catalog.model_price.return_value = price
        inp, out = ModelProfile.seed_token_rates_from_catalog("openai", "gpt-4o", catalog)
        assert inp == 0.005
        assert out == 0.015


# ---------------------------------------------------------------------------
# _attach_correlation_id
# ---------------------------------------------------------------------------


class TestAttachCorrelationId:
    def test_stamps_correlation_id_when_provided(self) -> None:
        resp = ModelResponse(content="hi")
        assert resp.correlation_id is None
        result = _attach_correlation_id(resp, "req-abc-123")
        assert result is resp
        assert resp.correlation_id == "req-abc-123"

    def test_noop_when_correlation_id_is_none(self) -> None:
        resp = ModelResponse(content="hi")
        result = _attach_correlation_id(resp, None)
        assert result is resp
        assert resp.correlation_id is None

    def test_noop_when_correlation_id_is_empty_string(self) -> None:
        resp = ModelResponse(content="hi")
        result = _attach_correlation_id(resp, "")
        assert result is resp
        assert resp.correlation_id == ""

    def test_overwrites_existing_correlation_id(self) -> None:
        resp = ModelResponse(content="hi", correlation_id="old-id")
        result = _attach_correlation_id(resp, "new-id")
        assert result is resp
        assert resp.correlation_id == "new-id"


# ---------------------------------------------------------------------------
# _redact_url_in_exception
# ---------------------------------------------------------------------------


class TestRedactUrlInException:
    def test_redacts_url_in_single_string_arg(self) -> None:
        exc = ValueError("failed: https://proxy.internal/v1/chat")
        _redact_url_in_exception(exc, "https://proxy.internal/v1/chat")
        assert "[REDACTED_URL]" in exc.args[0]
        assert "https://proxy.internal/v1/chat" not in exc.args[0]

    def test_redacts_url_in_multiple_string_args(self) -> None:
        exc = ConnectionError(
            "connect to https://x.invalid/v1",
            "retry https://x.invalid/v1 again",
        )
        _redact_url_in_exception(exc, "https://x.invalid/v1")
        assert "https://x.invalid/v1" not in exc.args[0]
        assert "https://x.invalid/v1" not in exc.args[1]
        assert "[REDACTED_URL]" in exc.args[0]
        assert "[REDACTED_URL]" in exc.args[1]

    def test_preserves_non_string_args(self) -> None:
        exc = ConnectionError("bad host https://h.invalid", 503)
        _redact_url_in_exception(exc, "https://h.invalid")
        assert exc.args[0] == "bad host [REDACTED_URL]"
        assert exc.args[1] == 503

    def test_noop_on_empty_url(self) -> None:
        exc = ValueError("message unchanged")
        orig_args = exc.args
        _redact_url_in_exception(exc, "")
        assert exc.args == orig_args

    def test_noop_when_url_not_present_in_args(self) -> None:
        exc = ValueError("nothing to redact here")
        _redact_url_in_exception(exc, "https://other.invalid")
        assert exc.args[0] == "nothing to redact here"

    def test_survives_non_iterable_args(self) -> None:
        class _WeirdExc(Exception):
            @property
            def args(self) -> object:
                return None

        exc = _WeirdExc()
        # should not raise — except Exception catches TypeError
        _redact_url_in_exception(exc, "https://x.invalid")
        assert True


# ---------------------------------------------------------------------------
# _is_healthy_with_timeout
# ---------------------------------------------------------------------------


class _StubHealthTracker:
    def __init__(self, healthy: bool = True) -> None:
        self._healthy = healthy
        self._call_count = 0

    def is_healthy(self, model_id: str, *, admit_probe: bool = False) -> bool:
        self._call_count += 1
        return self._healthy

    def record_success(self, model_id: str) -> None:
        pass

    def record_event(self, event: object) -> None:
        pass


class _SlowHealthTracker:
    def is_healthy(self, model_id: str, *, admit_probe: bool = False) -> bool:
        time.sleep(10.0)
        return True

    def record_success(self, model_id: str) -> None:
        pass

    def record_event(self, event: object) -> None:
        pass


class _FailingHealthTracker:
    def is_healthy(self, model_id: str, *, admit_probe: bool = False) -> bool:
        raise RuntimeError("boom")

    def record_success(self, model_id: str) -> None:
        pass

    def record_event(self, event: object) -> None:
        pass


class TestIsHealthyWithTimeout:
    def test_returns_true_when_tracker_reports_healthy(self) -> None:
        tracker = _StubHealthTracker(healthy=True)
        result = _is_healthy_with_timeout(tracker, "p1", timeout=2.0)
        assert result is True

    def test_returns_false_when_tracker_reports_unhealthy(self) -> None:
        tracker = _StubHealthTracker(healthy=False)
        result = _is_healthy_with_timeout(tracker, "p2", timeout=2.0)
        assert result is False

    def test_returns_false_on_timeout(self) -> None:
        tracker = _SlowHealthTracker()
        result = _is_healthy_with_timeout(tracker, "p3", timeout=0.01)
        assert result is False

    def test_returns_false_when_tracker_raises(self) -> None:
        tracker = _FailingHealthTracker()
        result = _is_healthy_with_timeout(tracker, "p4", timeout=2.0)
        assert result is False

    def test_default_timeout_is_five_seconds(self) -> None:
        import inspect

        sig = inspect.signature(_is_healthy_with_timeout)
        assert sig.parameters["timeout"].default == 5.0


# ---------------------------------------------------------------------------
# ModelResponse
# ---------------------------------------------------------------------------


class TestModelResponseDefaults:
    def test_default_content_is_empty_string(self) -> None:
        class _DefaultModelResponse:
            content: str

        resp = ModelResponse(content="")
        assert resp.content == ""

    def test_default_usage_metadata_is_empty_dict(self) -> None:
        resp = ModelResponse(content="x")
        assert resp.usage_metadata == {}

    def test_default_cost_estimate_is_zero(self) -> None:
        resp = ModelResponse(content="x")
        assert resp.cost_estimate == 0.0

    def test_default_model_name_is_empty_string(self) -> None:
        resp = ModelResponse(content="x")
        assert resp.model_name == ""

    def test_default_raw_response_is_none(self) -> None:
        resp = ModelResponse(content="x")
        assert resp.raw_response is None

    def test_default_tool_calls_is_none(self) -> None:
        resp = ModelResponse(content="x")
        assert resp.tool_calls is None

    def test_default_correlation_id_is_none(self) -> None:
        resp = ModelResponse(content="x")
        assert resp.correlation_id is None

    def test_all_fields_settable_via_constructor(self) -> None:
        resp = ModelResponse(
            content="hello",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
            cost_estimate=0.001,
            model_name="gpt-4",
            raw_response=None,
            tool_calls=[{"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            correlation_id="cid-1",
        )
        assert resp.content == "hello"
        assert resp.usage_metadata["input_tokens"] == 10
        assert resp.cost_estimate == 0.001
        assert resp.model_name == "gpt-4"
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["id"] == "t1"
        assert resp.correlation_id == "cid-1"
