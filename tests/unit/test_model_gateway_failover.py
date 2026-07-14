"""Model gateway failover tests: fallback chain, budget exhaustion, retry backoff.

Covers:
- Fallback chain walk (primary→secondary success, chain exhaustion)
- Budget exhaustion (pre-check skip, call-time race)
- Retry with exponential backoff
- Cycle-safe fallback (visited set)
- Max depth protection
- Error propagation (ModelPausedError, SSRFRejectionError)
- Circuit breaker open primary → immediate fallbacks
- correlation_id propagation
- _enrich_all_down_message structured errors
- ModelFailoverChain record/should_retry
- BudgetEnvelope exhaustion gating
"""

from __future__ import annotations

import threading
import time
from typing import ClassVar
from unittest import mock

import httpx
import pytest

from general_ludd.budget.envelope import BudgetEnvelope, BudgetManager
from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.gateway import (
    BudgetExceededError,
    CircuitBreakerOpenError,
    ModelGateway,
    ModelPausedError,
    ModelProfile,
    SSRFRejectionError,
    _enrich_all_down_message,
)
from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutEvent,
    TimeoutKind,
    TimeoutRetryPolicy,
)


def _server_error_503() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat")
    response = httpx.Response(503, request=request, text="service unavailable")
    return httpx.HTTPStatusError("503", request=request, response=response)


def _rate_limited_429() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat")
    response = httpx.Response(429, request=request, text="rate limited")
    return httpx.HTTPStatusError("429", request=request, response=response)


def _make_profile(
    profile_id: str,
    fallback_profiles: list[str] | None = None,
    run_budget_usd: float = 1_000_000.0,
    fallback_max_concurrency: int = 10,
) -> ModelProfile:
    return ModelProfile(
        model_profile_id=profile_id,
        model_name=f"model-{profile_id}",
        provider="openai",
        fallback_profiles=fallback_profiles or [],
        run_budget_usd=run_budget_usd,
        fallback_max_concurrency=fallback_max_concurrency,
        enabled=True,
        max_failover_retries=3,
        cost_per_input_token=0.000001,
        cost_per_output_token=0.000002,
    )


class _FakeChatModel:
    script: ClassVar[list[object]] = []
    call_count: ClassVar[int] = 0

    def __init__(self, **init_kwargs: object) -> None:
        pass

    def invoke(self, messages: list[dict[str, str]]) -> object:
        _FakeChatModel.call_count += 1
        if not _FakeChatModel.script:
            raise AssertionError("provider invoked more times than scripted")
        item = _FakeChatModel.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        resp = type("_Resp", (), {})()
        resp.content = item
        resp.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        resp.response_metadata = {}
        return resp


class _FakeRegistry:
    def is_installed(self, provider_name: str) -> bool:
        return True

    def install_provider(self, provider_name: str) -> None:
        raise AssertionError("install_provider should not be called")

    def get_provider_class(self, provider_name: str) -> type[_FakeChatModel]:
        return _FakeChatModel


class _FakePauseController:
    def is_paused(self, scope: str, target_id: str) -> bool:
        return False


@pytest.fixture(autouse=True)
def _reset_script() -> None:
    _FakeChatModel.script = []
    _FakeChatModel.call_count = 0


def _make_gateway(
    profiles: list[ModelProfile] | None = None,
    health_tracker: ModelHealthTracker | None = None,
    pause_controller: object | None = None,
    max_fallback_depth: int = 3,
) -> ModelGateway:
    if profiles is None:
        profiles = [_make_profile("primary")]
    return ModelGateway(
        profiles=profiles,
        provider_registry=_FakeRegistry(),
        health_tracker=health_tracker,
        pause_controller=pause_controller,
        max_fallback_depth=max_fallback_depth,
    )


class TestFallbackChainWalk:
    def test_primary_fails_fallback_succeeds(self):
        gw = _make_gateway(
            profiles=[
                _make_profile("primary", fallback_profiles=["secondary"]),
                _make_profile("secondary"),
            ]
        )
        _FakeChatModel.script = [
            _server_error_503(),
            "hello from secondary",
        ]
        resp = gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])
        assert resp.content == "hello from secondary"
        assert _FakeChatModel.call_count == 2

    def test_all_fallbacks_exhausted_raises_circuit_breaker(self):
        gw = _make_gateway(
            profiles=[
                _make_profile("primary", fallback_profiles=["f1", "f2"]),
                _make_profile("f1"),
                _make_profile("f2"),
            ]
        )
        _FakeChatModel.script = [_server_error_503(), _server_error_503(), _server_error_503()]
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])

    def test_fallback_chain_with_no_fallbacks_raises(self):
        gw = _make_gateway(profiles=[_make_profile("primary")])
        _FakeChatModel.script = [_server_error_503()]
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])

    def test_transitive_cascade_secondary_fails_tertiary_succeeds(self):
        gw = _make_gateway(
            profiles=[
                _make_profile("primary", fallback_profiles=["secondary"]),
                _make_profile("secondary", fallback_profiles=["tertiary"]),
                _make_profile("tertiary"),
            ]
        )
        _FakeChatModel.script = [
            _server_error_503(),
            _server_error_503(),
            "tertiary wins",
        ]
        resp = gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])
        assert resp.content == "tertiary wins"
        assert _FakeChatModel.call_count == 3

    def test_visited_set_prevents_cycles(self):
        gw = _make_gateway(
            profiles=[
                _make_profile("primary", fallback_profiles=["f1"]),
                _make_profile("f1", fallback_profiles=["primary"]),
            ]
        )
        _FakeChatModel.script = [_server_error_503(), _server_error_503()]
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])
        assert _FakeChatModel.call_count == 2

    def test_max_fallback_depth_exceeded(self):
        gw = _make_gateway(
            profiles=[
                _make_profile(f"p{i}", fallback_profiles=[f"p{i+1}"])
                for i in range(10)
            ],
            max_fallback_depth=2,
        )
        _FakeChatModel.script = [_server_error_503()] * 10
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback("p0", [{"role": "user", "content": "hi"}])
        assert _FakeChatModel.call_count <= 3


class TestBudgetExhaustion:
    def test_fallback_skipped_when_budget_exceeded_precheck(self):
        gw = _make_gateway(
            profiles=[
                _make_profile("primary", fallback_profiles=["expensive"], run_budget_usd=float("inf")),
                _make_profile("expensive", run_budget_usd=1.0),
            ]
        )
        gw._health_tracker = None
        resp_ok, _last_exc, attempts = gw._walk_fallbacks(
            ["expensive"],
            [{"role": "user", "content": "hi"}],
            from_profile_id="primary",
            estimated_cost=50.0,
            budget_remaining=2.0,
        )
        assert resp_ok is None
        assert any("budget exceeded" in a["reason"] for a in attempts)

    def test_fallback_budget_exceeded_at_call_time(self):
        p_primary = _make_profile("primary", fallback_profiles=["tight"])
        p_tight = _make_profile("tight", run_budget_usd=0.001)
        p_tight.cost_per_input_token = 1.0
        p_tight.cost_per_output_token = 1.0
        gw = ModelGateway(
            profiles=[p_primary, p_tight],
            provider_registry=_FakeRegistry(),
        )
        _FakeChatModel.script = ["success, but expensive"]
        resp_ok, last_exc, _attempts = gw._walk_fallbacks(
            ["tight"],
            [{"role": "user", "content": "hi"}],
            from_profile_id="primary",
            estimated_cost=0.0,
            budget_remaining=float("inf"),
        )
        assert resp_ok is None
        assert isinstance(last_exc, BudgetExceededError)

    def test_select_cost_effective_profile_within_budget(self):
        profiles = [
            _make_profile("cheap", run_budget_usd=5.0),
            _make_profile("expensive", run_budget_usd=100.0),
        ]
        profiles[0].cost_per_input_token = 0.0001
        profiles[0].cost_per_output_token = 0.0001
        profiles[1].cost_per_input_token = 0.01
        profiles[1].cost_per_output_token = 0.01
        result = ModelGateway.select_cost_effective_profile(profiles, budget_remaining=10.0)
        assert result is not None
        assert result.model_profile_id == "cheap"

    def test_select_cost_effective_profile_all_over_budget(self):
        profiles = [
            _make_profile("expensive", run_budget_usd=100.0),
        ]
        profiles[0].cost_per_input_token = 0.01
        profiles[0].cost_per_output_token = 0.01
        result = ModelGateway.select_cost_effective_profile(profiles, budget_remaining=5.0)
        assert result is None

    def test_envelope_exhaustion_blocks_spend(self):
        env = BudgetEnvelope("test", limit=10.0)
        result = env.try_spend(9.0)
        assert result["allowed"] is True
        result = env.try_spend(2.0)
        assert result["allowed"] is False
        assert "budget exceeded" in str(result["reason"])

    def test_envelope_nan_amount_denied(self):
        env = BudgetEnvelope("test", limit=10.0)
        result = env.try_spend(float("nan"))
        assert result["allowed"] is False

    def test_envelope_negative_amount_denied(self):
        env = BudgetEnvelope("test", limit=10.0)
        result = env.try_spend(-1.0)
        assert result["allowed"] is False


class TestRetryBackoff:
    def test_exponential_backoff_grows(self):
        policy = TimeoutRetryPolicy(
            max_retries=3,
            base_backoff_seconds=1.0,
            jitter_fn=lambda lo, hi: 0.0,
        )
        b1 = policy._compute_backoff(TimeoutKind.CONNECTION_TIMEOUT, 1, None)
        b3 = policy._compute_backoff(TimeoutKind.CONNECTION_TIMEOUT, 3, None)
        assert b1 > 0
        assert b3 >= b1

    def test_rate_limited_honors_retry_after(self):
        policy = TimeoutRetryPolicy(jitter_fn=lambda lo, hi: 0.0)
        wait = policy._compute_backoff(TimeoutKind.RATE_LIMITED, 1, 30.0)
        assert wait >= 30.0

    def test_non_retryable_kinds_bail_immediately(self):
        policy = TimeoutRetryPolicy()
        dec = policy.decide(TimeoutKind.AUTH_ERROR, 1)
        assert dec.should_retry is False

    def test_overload_kinds_higher_retry_cap(self):
        policy = TimeoutRetryPolicy(max_retries=3, overload_max_retries=10)
        dec = policy.decide(TimeoutKind.PROVIDER_ERROR, 5)
        assert dec.should_retry is True
        dec = policy.decide(TimeoutKind.CONNECTION_TIMEOUT, 5)
        assert dec.should_retry is False


class TestErrorPropagation:
    def test_model_paused_error_not_swallowed(self):
        p_primary = _make_profile("primary", fallback_profiles=["f1"])
        p_f1 = _make_profile("f1")
        pause = _FakePauseController()
        object.__setattr__(pause, "is_paused",
                           lambda scope, tid: scope == "model" and tid == "primary")
        gw = _make_gateway(
            profiles=[p_primary, p_f1],
            pause_controller=pause,
        )
        with pytest.raises(ModelPausedError):
            gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])

    def test_ssrf_rejection_propagates(self):
        class _FakeSecrets:
            def resolve(self, alias: str) -> str | None:
                return "http://blocked.internal/v1"

        patched = mock.patch(
            "general_ludd.security.auth.is_safe_fetch_url", return_value=False
        )
        with patched:
            gw = ModelGateway(
                profiles=[_make_profile("primary", fallback_profiles=["f1"]),
                           _make_profile("f1")],
                provider_registry=_FakeRegistry(),
                secrets_manager=_FakeSecrets(),
            )
            for p in gw.list_profiles():
                if p.model_profile_id == "primary":
                    p.api_base_alias = "blocked"
            with pytest.raises(SSRFRejectionError):
                gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])


class TestCircuitBreakerPrimary:
    def test_unhealthy_primary_skips_to_fallbacks(self):
        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=9999.0)
        now = time.monotonic()
        tracker.record_event(TimeoutEvent("primary", TimeoutKind.PROVIDER_ERROR, now - 0.2, 0.0))
        tracker.record_event(TimeoutEvent("primary", TimeoutKind.PROVIDER_ERROR, now - 0.1, 0.0))
        assert not tracker.is_healthy("primary")

        gw = _make_gateway(
            profiles=[
                _make_profile("primary", fallback_profiles=["f1"]),
                _make_profile("f1"),
            ],
            health_tracker=tracker,
        )
        _FakeChatModel.script = ["from fallback"]
        resp = gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])
        assert resp.content == "from fallback"
        assert _FakeChatModel.call_count == 1

    def test_unhealthy_primary_no_fallbacks_raises(self):
        tracker = ModelHealthTracker(failure_threshold=1, cooldown_seconds=60.0)
        tracker.record_event(TimeoutEvent("primary", TimeoutKind.PROVIDER_ERROR, 0.0, 0.0))
        gw = _make_gateway(
            profiles=[_make_profile("primary")],
            health_tracker=tracker,
        )
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model_with_fallback("primary", [{"role": "user", "content": "hi"}])


class TestCorrelationId:
    def test_correlation_id_propagated_on_fallback_success(self):
        p_primary = _make_profile("primary", fallback_profiles=["f1"])
        p_f1 = _make_profile("f1")
        gw = _make_gateway(profiles=[p_primary, p_f1])
        _FakeChatModel.script = ["ok"]

        resp = gw.call_model_with_fallback(
            "primary", [{"role": "user", "content": "hi"}]
        )
        assert resp.correlation_id is None

    def test_correlation_id_on_direct_success(self):
        p = _make_profile("primary")
        gw = _make_gateway(profiles=[p])
        _FakeChatModel.script = ["direct"]

        resp = gw.call_model("primary", [{"role": "user", "content": "hi"}])
        assert resp.content == "direct"
        assert resp.correlation_id is None


class TestEnrichAllDownMessage:
    def test_enriches_exception_with_all_attempts(self):
        exc = RuntimeError("original")
        attempts = [
            {"profile_id": "primary", "reason": "503"},
            {"profile_id": "f1", "reason": "timeout"},
        ]
        _enrich_all_down_message(exc, attempts)
        assert "all providers down" in str(exc)
        assert "primary" in str(exc)
        assert "f1" in str(exc)
        assert "503" in str(exc)
        assert "timeout" in str(exc)

    def test_enrich_empty_attempts_noop(self):
        exc = RuntimeError("original")
        _enrich_all_down_message(exc, [])
        assert str(exc) == "original"


class TestModelFailoverChain:
    def test_chain_order_preserved(self):
        chain = ModelFailoverChain("p", ["f1", "f2", "f3"])
        assert chain.get_chain() == ["p", "f1", "f2", "f3"]

    def test_record_failover_appends_event(self):
        chain = ModelFailoverChain("p", ["f1"])
        ok = chain.record_failover("p", "f1", "timeout", exception_type="httpx.TimeoutException")
        assert ok is True
        events = chain.get_failover_events()
        assert len(events) == 1
        assert events[0]["from"] == "p"
        assert events[0]["to"] == "f1"
        assert events[0]["error"] == "timeout"
        assert events[0]["exception_type"] == "httpx.TimeoutException"

    def test_should_retry_status_codes(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(_server_error_503()) is True
        assert chain.should_retry(_rate_limited_429()) is True

    def test_should_retry_status_code_explicit(self):
        chain = ModelFailoverChain("p")
        class _ExplicitStatus(Exception):
            status_code = 503
        assert chain.should_retry(_ExplicitStatus()) is True

        class _Explicit429(Exception):
            status_code = 429
        assert chain.should_retry(_Explicit429()) is True

    def test_should_retry_keywords(self):
        chain = ModelFailoverChain("p")
        class _FakeTimeout(BaseException):
            def __str__(self):
                return "connection timeout occurred"
        assert chain.should_retry(_FakeTimeout()) is True

    def test_should_not_retry_auth_error(self):
        chain = ModelFailoverChain("p")
        request = httpx.Request("POST", "https://provider/v1/chat")
        response = httpx.Response(401, request=request, text="unauthorized")
        auth_err = httpx.HTTPStatusError("401", request=request, response=response)
        assert chain.should_retry(auth_err) is False

    def test_concurrent_failover_events_threadsafe(self):
        chain = ModelFailoverChain("p", ["f1", "f2"])
        num_threads = 10
        calls_per = 100
        expected = num_threads * calls_per

        errors: list[Exception] = []
        def worker(tid: int) -> None:
            try:
                for i in range(calls_per):
                    chain.record_failover(f"p-{tid}", f"f-{tid}-{i}", f"err-{tid}-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(chain.get_failover_events()) == expected


class TestBudgetManagerLayered:
    def test_check_all_tool_layer_blocks_first(self):
        from general_ludd.budget.envelope import (
            BudgetManager,
            PerToolEnvelope,
        )
        tools = PerToolEnvelope()
        tools.set_limit("bash", 5.0)
        mgr = BudgetManager(per_tool=tools)
        result = mgr.check_all(tool_type="bash", amount=10.0)
        assert result.allowed is False
        assert result.details["layer"] == "tool"

    def test_check_all_task_layer_blocks_second(self):
        from general_ludd.budget.envelope import (
            BudgetManager,
            PerTaskEnvelope,
        )
        tasks = PerTaskEnvelope(default_limit=3.0)
        mgr = BudgetManager(per_task=tasks)
        result = mgr.check_all(task_id="task-1", amount=5.0)
        assert result.allowed is False
        assert result.details["layer"] == "task"

    def test_check_all_no_limits_allows(self):
        mgr = BudgetManager()
        result = mgr.check_all(agent_type="sonnet", amount=500.0)
        assert result.allowed is True

    def test_envelope_is_exhausted_boundary(self):
        env = BudgetEnvelope("test", limit=1.0)
        assert env.is_exhausted is False
        env.record_spend(1.0)
        assert env.is_exhausted is True
