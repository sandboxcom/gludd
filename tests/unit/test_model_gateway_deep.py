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
    PayloadLimitError,
    SSRFRejectionError,
    StreamLimitError,
    _coerce_token_count,
    _extract_retry_after_seconds,
    _extract_tool_calls,
    _LimitedChatModel,
    _positive_profile_limit,
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
        allowed = gw.check_budget("unlimited", estimated_cost=999999.0, budget_remaining=float("inf"))
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
