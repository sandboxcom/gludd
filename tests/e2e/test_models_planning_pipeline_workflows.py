"""E2E tests covering models, planning, and pipeline subsystems.

Exercises model gateway (dispatch, fallback, retry, error classification,
circuit breaker), model router (role resolution, profile matching, capability
filtering), provider registry (register, discover, health check),
planning (task decomposition, dependency graph, artifact persistence),
pipeline (lane assignment, state machine transitions, concurrency control),
and performance router (latency tracking, model selection optimisation).

Uses mocks and in-memory stores — no live providers, no network.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.gateway import (
    BudgetExceededError,
    CircuitBreakerOpenError,
    ModelGateway,
    ModelPausedError,
    ModelProfile,
    ModelResponse,
)
from general_ludd.models.langchain_router import LangChainModelRouter
from general_ludd.models.performance_router import (
    ModelPerformanceRouter,
    _scale,
)
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.router import ModelRouter
from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutClassifier,
    TimeoutEvent,
    TimeoutKind,
    TimeoutRetryPolicy,
)
from general_ludd.pipeline.controller import PipelineController
from general_ludd.pipeline.lanes import DispatchLane, GateLane, IntegrateLane
from general_ludd.pipeline.state import (
    CompletedUnit,
    LaneState,
    MergeOutcome,
    PipelineConfig,
)
from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.critique import PlanCritique
from general_ludd.planning.debt_applier import apply_debt_findings
from general_ludd.planning.debt_evaluator import (
    DebtEvaluator,
    DebtFinding,
    DebtFindings,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.e2e


def _profile(
    pid: str,
    model_name: str = "",
    role_names: list[str] | None = None,
    **overrides: Any,
) -> ModelProfile:
    kwargs: dict[str, Any] = {
        "model_profile_id": pid,
        "provider": "local",
        "model_name": model_name or pid,
        "enabled": True,
        "api_metered": False,
        "role_names": role_names or [],
    }
    kwargs.update(overrides)
    return ModelProfile(**kwargs)


def _make_gateway(
    profiles: list[ModelProfile] | None = None,
    **overrides: Any,
) -> ModelGateway:
    reg = ProviderRegistry()
    reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
    reg.register_provider("anthropic", "langchain-anthropic", "ChatAnthropic")
    reg.register_provider("google", "langchain-openai", "ChatOpenAI")
    kwargs = {
        "profiles": profiles or [],
        "provider_registry": reg,
    }
    kwargs.update(overrides)
    return cast(Any, ModelGateway)(**kwargs)


class _FakeChatModel:
    """Stand-in for a LangChain chat model that returns scripted content."""

    def __init__(self, content: str = "ok", *, tool_calls: list[dict[str, object]] | None = None) -> None:
        self._content = content
        self._tc = tool_calls
        self.invoke_count = 0

    def invoke(self, _messages: object) -> object:
        self.invoke_count += 1
        response = _FakeResponse(self._content, tool_calls=self._tc)
        return response

    def bind_tools(self, tools: Any) -> _FakeChatModel:
        return self


class _FakeResponse:
    def __init__(self, content: str, *, tool_calls: list[dict[str, object]] | None = None) -> None:
        self.content = content
        self.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        self.tool_calls = tool_calls


class _FakeHealthTracker:
    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy: dict[str, bool] = {}
        self._default = healthy
        self.failures_recorded: list[str] = []
        self.successes_recorded: list[str] = []

    def is_healthy(self, model_id: str, admit_probe: bool = True) -> bool:
        return self._healthy.get(model_id, self._default)

    def set_unhealthy(self, model_id: str) -> None:
        self._healthy[model_id] = False

    def record_success(self, model_id: str) -> None:
        self.successes_recorded.append(model_id)

    def record_event(self, event: object) -> None:
        self.failures_recorded.append(getattr(event, "model_id", ""))


class _FakeResponseCache:
    def __init__(self, ttl: float | None = None) -> None:
        self.store: dict[str, dict[str, object]] = {}
        self.ttl = ttl

    def get(self, cache_key: str) -> dict[str, object] | None:
        return self.store.get(cache_key)

    def set(self, cache_key: str, response: dict[str, object], *, expire: float | None = None) -> None:
        self.store[cache_key] = response


class _FakeBudgetGuard:
    def __init__(self, remaining: float = float("inf")) -> None:
        self.remaining = remaining
        self.spend_total = 0.0

    def record_spend(self, cost: float) -> None:
        self.spend_total += cost
        self.remaining = max(0.0, self.remaining - cost)


class _FakePauseController:
    def __init__(self, paused: set[str] | None = None) -> None:
        self._paused = paused or set()

    def is_paused(self, scope: str, target_id: str) -> bool:
        return target_id in self._paused


class _FakeMetricsCollector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.failovers: list[dict[str, Any]] = []

    def record_model_call(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def record_failover(self, from_profile: str, to_profile: str, error: str = "") -> None:
        self.failovers.append({"from": from_profile, "to": to_profile, "error": error})


class _FakePerformanceRepo:
    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, object]]] = {}
        self.calls: list[dict[str, Any]] = []

    async def record_call(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "rec-1"

    async def get_best_model(
        self, task_type: str, min_calls: int = 3, prefer_cost: bool = False,
    ) -> dict[str, object] | None:
        rankings = self._data.get(task_type, [])
        if not rankings:
            return None
        return rankings[0]

    async def get_ranking(self, task_type: str) -> list[dict[str, object]]:
        return self._data.get(task_type, [])

    async def get_summary(self, service: str | None = None, task_type: str | None = None) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for tt, entries in self._data.items():
            for e in entries:
                if task_type and tt != task_type:
                    continue
                if service and e.get("service") != service:
                    continue
                results.append(e)
        return results

    async def refresh_recent_stats(self) -> None:
        pass


def _make_todo_repo() -> MagicMock:
    repo = MagicMock()
    created = MagicMock()
    created.todo_id = "debt-todo-1"
    repo.create.return_value = created
    return repo


# ---------------------------------------------------------------------------
# 1. Model Gateway — dispatch, fallback, retry, error classification,
#    circuit breaker, budget, pause, cache, metrics
# ---------------------------------------------------------------------------


class TestModelGatewayProfiles:
    def test_list_profiles_returns_registered(self):
        p1 = _profile("p1", "gpt-4")
        p2 = _profile("p2", "claude-3")
        gw = _make_gateway([p1, p2])
        assert len(gw.list_profiles()) == 2

    def test_get_profile_found_and_missing(self):
        p1 = _profile("p1")
        gw = _make_gateway([p1])
        assert gw.get_profile("p1") is not None
        assert gw.get_profile("missing") is None

    def test_is_available_only_when_enabled(self):
        p1 = _profile("p1", enabled=True)
        p2 = _profile("p2", enabled=False)
        gw = _make_gateway([p1, p2])
        assert gw.is_available("p1") is True
        assert gw.is_available("p2") is False

    def test_call_model_unknown_profile_raises(self):
        gw = _make_gateway()
        with pytest.raises(ValueError, match="not found"):
            gw.call_model("no_such", [])

    def test_call_model_paused_raises(self):
        pc = _FakePauseController({"p1"})
        p1 = _profile("p1")
        gw = _make_gateway([p1], pause_controller=pc)
        with pytest.raises(ModelPausedError):
            gw.call_model("p1", [])

    def test_call_model_circuit_open_raises(self):
        ht = _FakeHealthTracker()
        ht.set_unhealthy("p1")
        p1 = _profile("p1")
        gw = _make_gateway([p1], health_tracker=ht)
        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model("p1", [])

    def test_call_model_skips_health_check_when_asked(self):
        ht = _FakeHealthTracker()
        ht.set_unhealthy("p1")
        p1 = _profile("p1")
        gw = _make_gateway([p1], health_tracker=ht)
        with patch.object(gw, "_invoke_and_bill", return_value=ModelResponse(content="ok")):
            resp = gw.call_model("p1", [], _skip_health_check=True)
        assert resp.content == "ok"

    def test_call_model_budget_exceeded_raises(self):
        p1 = _profile(
            "p1",
            provider="openai",
            api_metered=True,
            cost_per_input_token=0.01,
            cost_per_output_token=0.01,
            run_budget_usd=0.001,
        )
        gw = _make_gateway([p1], budget_guard=_FakeBudgetGuard(remaining=0.0))
        with pytest.raises(BudgetExceededError):
            gw.call_model("p1", [{"content": "hello"}], estimated_cost=0.01, budget_remaining=0.0)


class TestModelGatewayCache:
    def test_cache_hit_returns_cached_response(self):
        cache = _FakeResponseCache()
        p1 = _profile("p1", "gpt-4")
        gw = _make_gateway([p1], response_cache=cache)
        # Pre-populate
        from general_ludd.models.response_cache import _make_cache_key
        key = _make_cache_key("p1", [{"role": "user", "content": "hi"}], model_name="gpt-4")
        cache.set(key, {"content": "cached", "usage_metadata": {}, "model_name": "gpt-4", "cost_estimate": 0.0})
        resp = gw.call_model("p1", [{"role": "user", "content": "hi"}])
        assert resp.content == "cached"

    def test_cache_miss_invokes_provider(self):
        cache = _FakeResponseCache()
        p1 = _profile("p1", "gpt-4")
        gw = _make_gateway([p1], response_cache=cache)
        with patch.object(gw, "_invoke_and_bill", return_value=ModelResponse(content="fresh", model_name="gpt-4")):
            resp = gw.call_model("p1", [{"role": "user", "content": "hi"}])
        assert resp.content == "fresh"

    def test_no_cache_when_cache_not_configured(self):
        p1 = _profile("p1", "gpt-4")
        gw = _make_gateway([p1])
        with patch.object(gw, "_invoke_and_bill", return_value=ModelResponse(content="direct", model_name="gpt-4")):
            resp = gw.call_model("p1", [{"role": "user", "content": "hi"}])
        assert resp.content == "direct"


class TestModelGatewayBudget:
    def test_check_budget_absent_profile(self):
        gw = _make_gateway()
        assert gw.check_budget("no_such", 0.0, 10.0) is False

    def test_check_budget_metered_exceeded(self):
        p1 = _profile(
            "p1",
            "gpt-4",
            provider="openai",
            api_metered=True,
            cost_per_input_token=0.00003,
            cost_per_output_token=0.00006,
            run_budget_usd=0.50,
        )
        gw = _make_gateway([p1])
        assert gw.check_budget("p1", 1.0, 10.0) is False

    def test_check_budget_within_limits(self):
        p1 = _profile(
            "p1",
            "gpt-4",
            provider="openai",
            api_metered=True,
            cost_per_input_token=0.00003,
            cost_per_output_token=0.00006,
            run_budget_usd=100.0,
        )
        gw = _make_gateway([p1])
        assert gw.check_budget("p1", 0.01, 10.0) is True

    def test_estimate_cost_empty_messages(self):
        p1 = _profile("p1")
        assert ModelGateway.estimate_cost(p1, []) == 0.0

    def test_estimate_cost_with_messages(self):
        p1 = _profile("p1", cost_per_input_token=0.01, cost_per_output_token=0.02, max_output_tokens=100)
        # "hello world" = 11 chars -> 11//4 = 2 tokens input
        cost = ModelGateway.estimate_cost(p1, [{"content": "hello world"}])
        assert cost == pytest.approx(2 * 0.01 + 100 * 0.02)

    def test_estimate_cost_with_output_cap(self):
        p1 = _profile("p1", cost_per_input_token=0.01, cost_per_output_token=0.02, max_output_tokens=100)
        cost = ModelGateway.estimate_cost(p1, [{"content": "hello"}], requested_max_output_tokens=10)
        assert cost == pytest.approx(1 * 0.01 + 10 * 0.02)


class TestModelGatewayMetrics:
    def test_metrics_collector_receives_success(self):
        mc = _FakeMetricsCollector()
        p1 = _profile("p1", "gpt-4")
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain_openai", "ChatOpenAI")
        gw = _make_gateway([p1], metrics_collector=mc, metrics_agent_id="agent1", provider_registry=reg)
        fake_model = _FakeChatModel("ok")
        with patch.object(gw._registry, "is_installed", return_value=True), \
             patch.object(gw._registry, "get_provider_class", return_value=lambda **kw: fake_model):
            gw.call_model("p1", [{"role": "user", "content": "hi"}])
        assert len(mc.calls) >= 1
        assert mc.calls[0]["success"] is True

    def test_metrics_collector_receives_failover(self):
        chain = ModelFailoverChain("primary")
        chain.record_failover("primary", "secondary", "timeout", exception_type="TimeoutError")
        events = chain.get_failover_events()
        assert len(events) == 1
        assert events[0]["from"] == "primary"
        assert events[0]["to"] == "secondary"


# ---------------------------------------------------------------------------
# 2. Model Router — role resolution, profile matching, pattern mapping
# ---------------------------------------------------------------------------


class TestModelRouterRoleResolution:
    def test_resolve_role_direct_mapping(self):
        router = ModelRouter(role_mapping={"coder": "prof1", "reviewer": "prof2"})
        assert router.resolve_role("coder") == "prof1"
        assert router.resolve_role("reviewer") == "prof2"

    def test_resolve_role_falls_to_default(self):
        router = ModelRouter(role_mapping={"coder": "prof1"}, default_profile_id="default_prof")
        assert router.resolve_role("unknown") == "default_prof"

    def test_resolve_role_returns_none_when_not_found(self):
        router = ModelRouter()
        assert router.resolve_role("nothing") is None

    def test_resolve_role_strict_raises(self):
        router = ModelRouter(role_mapping={"coder": "prof1"})
        with pytest.raises(ValueError, match="Unrecognised role"):
            router.resolve_role("unknown", strict=True)

    def test_resolve_role_weak_sentinel(self):
        router = ModelRouter(weak_model_profile_id="cheap_model")
        assert router.resolve_role("weak") == "cheap_model"

    def test_add_role_and_resolve(self):
        router = ModelRouter()
        router.add_role("planner", "plan_model")
        assert router.resolve_role("planner") == "plan_model"

    def test_set_role_routing(self):
        router = ModelRouter()
        router.set_role_routing("planner", "plan_model")
        assert router.resolve_role("planner") == "plan_model"

    def test_list_roles(self):
        router = ModelRouter(role_mapping={"a": "x", "b": "y"})
        assert sorted(router.list_roles()) == ["a", "b"]

    def test_list_profiles_by_role(self):
        router = ModelRouter(role_mapping={"a": "prof1", "b": "prof1", "c": "prof2"})
        assert sorted(router.list_profiles_by_role("prof1")) == ["a", "b"]


class TestModelRouterQualityAndLatency:
    def test_quality_mapping(self):
        router = ModelRouter()
        router.add_quality_mapping("premium", "prof_premium")
        assert router.resolve_by_quality("premium") == "prof_premium"
        assert router.resolve_by_quality("unknown") is None

    def test_latency_mapping(self):
        router = ModelRouter()
        router.add_latency_mapping("fast", "prof_fast")
        assert router.resolve_by_latency("fast") == "prof_fast"
        assert router.resolve_by_latency("slow") is None


class TestModelRouterPatternMapping:
    def test_pattern_resolves_through_role(self):
        router = ModelRouter(role_mapping={"planner": "plan_model"})
        router.add_pattern_mapping("decompose", "planner")
        assert router.resolve_pattern("decompose") == "plan_model"

    def test_pattern_missing_returns_none(self):
        router = ModelRouter()
        assert router.resolve_pattern("no_such") is None

    def test_list_patterns(self):
        router = ModelRouter()
        router.add_pattern_mapping("p1", "r1")
        router.add_pattern_mapping("p2", "r2")
        assert sorted(router.list_patterns()) == ["p1", "p2"]


class TestModelRouterBuildFromProfiles:
    def test_build_from_profiles_populates_roles(self):
        p1 = _profile("p1", role_names=["coder", "reviewer"])
        p2 = _profile("p2", role_names=["planner"])
        router = ModelRouter.build_from_profiles([p1, p2])
        assert router.resolve_role("coder") == "p1"
        assert router.resolve_role("planner") == "p2"

    def test_build_from_profiles_populates_quality(self):
        p1 = _profile("p1", quality_class="premium")
        router = ModelRouter.build_from_profiles([p1])
        assert router.resolve_by_quality("premium") == "p1"

    def test_build_from_profiles_populates_latency(self):
        p1 = _profile("p1", latency_class="fast")
        router = ModelRouter.build_from_profiles([p1])
        assert router.resolve_by_latency("fast") == "p1"


class TestLangChainModelRouter:
    def test_resolve_matching_route(self):
        lcr = LangChainModelRouter()
        lcr.add_route(lambda d: d.get("role") == "coder", "strong_model")
        result = lcr.resolve({"role": "coder"})
        assert result == "strong_model"

    def test_resolve_falls_to_default(self):
        lcr = LangChainModelRouter()
        lcr.set_default("default_model")
        result = lcr.resolve({"role": "unknown"})
        assert result == "default_model"

    def test_resolve_returns_none_with_no_match_and_no_default(self):
        lcr = LangChainModelRouter()
        lcr.add_route(lambda d: d.get("role") == "coder", "strong_model")
        result = lcr.resolve({"role": "unknown"})
        assert result is None

    def test_first_matching_condition_wins(self):
        lcr = LangChainModelRouter()
        lcr.add_route(lambda d: d.get("role") == "coder", "first")
        lcr.add_route(lambda d: d.get("role") == "coder", "second")
        result = lcr.resolve({"role": "coder"})
        assert result == "first"


# ---------------------------------------------------------------------------
# 3. Provider Registry — register, discover, health check
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_register_and_get_info(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        info = reg.get_provider_info("openai")
        assert info is not None
        assert info.name == "openai"
        assert info.package_name == "langchain_openai"
        assert info.class_hint == "ChatOpenAI"

    def test_get_provider_info_missing(self) -> None:
        reg = ProviderRegistry()
        assert reg.get_provider_info("no_such") is None

    def test_get_provider_class_raises_for_missing(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="not registered"):
            reg.get_provider_class("no_such")

    def test_get_provider_class_raises_for_not_installed(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider(
            "huggingface", "langchain-huggingface", "HuggingFaceEndpoint"
        )
        with (
            patch("importlib.util.find_spec", return_value=None),
            pytest.raises(ImportError, match="not installed"),
        ):
            reg.get_provider_class("huggingface")

    def test_list_providers(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider("a", "langchain-openai", "ChatOpenAI")
        reg.register_provider("b", "langchain-anthropic", "ChatAnthropic")
        assert sorted(reg.list_providers()) == ["a", "b"]

    def test_from_presets_populates_registry(self) -> None:
        reg = ProviderRegistry.from_presets()
        providers = reg.list_providers()
        assert len(providers) > 0
        assert "openai" in providers

    def test_is_installed_false_when_not_installed(self) -> None:
        reg = ProviderRegistry()
        assert reg.is_installed("no_such") is False

    def test_install_provider_creates_todo_for_missing(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider(
            "huggingface", "langchain-huggingface", "HuggingFaceEndpoint"
        )
        with patch("importlib.util.find_spec", return_value=None):
            todo = reg.install_provider("huggingface")
        assert todo is not None
        assert "langchain_huggingface" in todo.title

    def test_install_provider_returns_none_for_installed(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        with patch("importlib.util.find_spec", return_value=MagicMock()):
            todo = reg.install_provider("openai")
        assert todo is None


# ---------------------------------------------------------------------------
# 4. Timeout Detection — classifier, health tracker, retry policy
# ---------------------------------------------------------------------------


class TestTimeoutClassifier:
    def test_classifies_connect_timeout(self):
        kind = TimeoutClassifier.classify(httpx.ConnectTimeout("connection failed"))
        assert kind == TimeoutKind.CONNECTION_TIMEOUT

    def test_classifies_connect_error(self):
        kind = TimeoutClassifier.classify(httpx.ConnectError("connect error"))
        assert kind == TimeoutKind.CONNECTION_TIMEOUT

    def test_classifies_read_timeout(self):
        kind = TimeoutClassifier.classify(httpx.ReadTimeout("read timeout"))
        assert kind == TimeoutKind.READ_TIMEOUT

    def test_classifies_rate_limited(self):
        resp = MagicMock()
        resp.status_code = 429
        err = httpx.HTTPStatusError("too many", request=MagicMock(), response=resp)
        kind = TimeoutClassifier.classify(err)
        assert kind == TimeoutKind.RATE_LIMITED

    def test_classifies_auth_error(self):
        resp = MagicMock()
        resp.status_code = 401
        err = httpx.HTTPStatusError("unauthorized", request=MagicMock(), response=resp)
        kind = TimeoutClassifier.classify(err)
        assert kind == TimeoutKind.AUTH_ERROR

    def test_classifies_provider_error_500(self):
        resp = MagicMock()
        resp.status_code = 500
        err = httpx.HTTPStatusError("server error", request=MagicMock(), response=resp)
        kind = TimeoutClassifier.classify(err)
        assert kind == TimeoutKind.PROVIDER_ERROR

    def test_classifies_context_length(self):
        resp = MagicMock()
        resp.status_code = 400
        err = httpx.HTTPStatusError("context_length_exceeded", request=MagicMock(), response=resp)
        kind = TimeoutClassifier.classify(err)
        assert kind == TimeoutKind.CONTEXT_LENGTH

    def test_classifies_unknown_for_plain_exception(self):
        kind = TimeoutClassifier.classify(ValueError("something else"))
        assert kind == TimeoutKind.UNKNOWN


class TestModelHealthTracker:
    def test_healthy_by_default(self):
        tracker = ModelHealthTracker()
        assert tracker.is_healthy("model1") is True

    def test_becomes_unhealthy_after_threshold(self):
        tracker = ModelHealthTracker(failure_threshold=3)
        for _ in range(3):
            tracker.record_event(TimeoutEvent("model1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        assert tracker.is_healthy("model1") is False

    def test_stays_healthy_below_threshold(self):
        tracker = ModelHealthTracker(failure_threshold=3)
        tracker.record_event(TimeoutEvent("model1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        tracker.record_event(TimeoutEvent("model1", TimeoutKind.READ_TIMEOUT, time.monotonic(), 1.0))
        assert tracker.is_healthy("model1") is True

    def test_record_success_resets_consecutive(self):
        tracker = ModelHealthTracker(failure_threshold=3)
        for _ in range(3):
            tracker.record_event(TimeoutEvent("model1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        tracker.record_success("model1")
        assert tracker.is_healthy("model1") is True

    def test_non_retryable_kinds_stay_healthy(self):
        tracker = ModelHealthTracker(failure_threshold=3)
        for _ in range(3):
            tracker.record_event(TimeoutEvent("model1", TimeoutKind.AUTH_ERROR, time.monotonic(), 1.0))
        assert tracker.is_healthy("model1") is True

    def test_get_health_returns_summary(self):
        tracker = ModelHealthTracker()
        tracker.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        health = tracker.get_health("m1")
        assert health["model_id"] == "m1"
        assert health["consecutive_failures"] == 1

    def test_get_health_admit_probe_false(self):
        tracker = ModelHealthTracker(failure_threshold=3)
        for _ in range(3):
            tracker.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        health = tracker.get_health("m1")
        assert health["healthy"] is False


class TestTimeoutRetryPolicy:
    def test_non_retryable_kinds(self):
        policy = TimeoutRetryPolicy()
        decision = policy.decide(TimeoutKind.AUTH_ERROR, 1)
        assert decision.should_retry is False

    def test_connection_timeout_retry(self):
        policy = TimeoutRetryPolicy(jitter_fn=lambda lo, hi: 0.0)
        decision = policy.decide(TimeoutKind.CONNECTION_TIMEOUT, 1)
        assert decision.should_retry is True

    def test_failover_after_max_retries(self):
        policy = TimeoutRetryPolicy(max_retries=2, failover_after_retries=2, jitter_fn=lambda lo, hi: 0.0)
        decision = policy.decide(TimeoutKind.CONNECTION_TIMEOUT, 3)
        assert decision.should_failover is True

    def test_rate_limited_gets_longer_backoff(self):
        policy = TimeoutRetryPolicy(jitter_fn=lambda lo, hi: 0.0)
        decision = policy.decide(TimeoutKind.RATE_LIMITED, 1, retry_after_seconds=5.0)
        assert decision.wait_seconds >= 1.0
        assert decision.should_retry is True

    def test_overload_kinds_exhaust(self):
        policy = TimeoutRetryPolicy(overload_max_retries=3, jitter_fn=lambda lo, hi: 0.0)
        decision = policy.decide(TimeoutKind.PROVIDER_ERROR, 4)
        assert decision.should_failover is True


# ---------------------------------------------------------------------------
# 5. Failover Chain — recording, should_retry, threading
# ---------------------------------------------------------------------------


class TestModelFailoverChain:
    def test_get_chain(self):
        chain = ModelFailoverChain("primary", ["s1", "s2"])
        assert chain.get_chain() == ["primary", "s1", "s2"]

    def test_record_failover(self):
        chain = ModelFailoverChain("primary")
        ok = chain.record_failover("primary", "secondary", "timeout", exception_type="TimeoutError")
        assert ok is True
        events = chain.get_failover_events()
        assert len(events) == 1
        assert events[0]["from"] == "primary"
        assert events[0]["to"] == "secondary"
        assert events[0]["exception_type"] == "TimeoutError"

    def test_should_retry_on_429(self):
        chain = ModelFailoverChain("primary")
        exc = httpx.HTTPStatusError("", request=MagicMock(), response=MagicMock(status_code=429))
        assert chain.should_retry(exc) is True

    def test_should_retry_on_503(self):
        chain = ModelFailoverChain("primary")
        exc = httpx.HTTPStatusError("", request=MagicMock(), response=MagicMock(status_code=503))
        assert chain.should_retry(exc) is True

    def test_should_retry_on_keyword_timeout(self):
        chain = ModelFailoverChain("primary")
        assert chain.should_retry(ValueError("connection timeout")) is True

    def test_should_retry_on_keyword_rate_limit(self):
        chain = ModelFailoverChain("primary")
        assert chain.should_retry(RuntimeError("rate limit exceeded")) is True

    def test_should_not_retry_400(self):
        chain = ModelFailoverChain("primary")
        exc = httpx.HTTPStatusError("", request=MagicMock(), response=MagicMock(status_code=400))
        assert chain.should_retry(exc) is False

    def test_concurrent_recording_is_safe(self):
        chain = ModelFailoverChain("primary", max_concurrent_failovers=100)
        results: list[bool] = []

        def record() -> None:
            results.append(chain.record_failover("p", "s", "err"))

        threads = [threading.Thread(target=record) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(results)
        assert len(chain.get_failover_events()) == 10


# ---------------------------------------------------------------------------
# 6. Performance Router — strategy selection, ranking, latency tracking
# ---------------------------------------------------------------------------


class TestScaleFunction:
    def test_scale_normal(self):
        result = _scale([1.0, 2.0, 3.0])
        assert result == [0.0, 0.5, 1.0]

    def test_scale_empty(self):
        assert _scale([]) == []

    def test_scale_uniform(self):
        result = _scale([5.0, 5.0, 5.0])
        assert result == [0.5, 0.5, 0.5]


class TestModelPerformanceRouter:
    def test_default_strategy_balanced(self):
        router = ModelPerformanceRouter()
        assert router.get_strategy("coding") == "balanced"

    def test_set_and_get_strategy(self):
        router = ModelPerformanceRouter()
        router.set_strategy("coding", "cheapest")
        assert router.get_strategy("coding") == "cheapest"

    def test_set_unknown_strategy_raises(self):
        router = ModelPerformanceRouter()
        with pytest.raises(ValueError, match="Unknown strategy"):
            router.set_strategy("coding", "nonesense")

    def test_get_config(self):
        router = ModelPerformanceRouter()
        config = router.get_config()
        assert "strategies" in config
        assert "defaults" in config

    async def test_select_model_no_repo_fallback(self):
        router = ModelPerformanceRouter(config={"default_fallback": "openai/gpt-4o"})
        result = await router.select_model("coding")
        assert result["service"] == "openai"
        assert result["model_name"] == "gpt-4o"
        assert result["fallback"] is True

    async def test_select_model_from_repo(self):
        repo = _FakePerformanceRepo()
        repo._data["coding"] = [
            {
                "service": "openai", "model_name": "gpt-4", "success_rate": 0.95,
                "avg_latency_ms": 200, "avg_cost_usd": 0.01, "sample_count": 100,
                "composite_score": 0.9,
            },
        ]
        router = ModelPerformanceRouter(perf_repo=repo)
        result = await router.select_model("coding")
        assert result["service"] == "openai"
        assert result["model_name"] == "gpt-4"
        assert result["fallback"] is False

    async def test_select_model_cheapest_strategy(self):
        repo = _FakePerformanceRepo()
        repo._data["coding"] = [
            {
                "service": "openai", "model_name": "gpt-4", "success_rate": 0.95,
                "avg_latency_ms": 200, "avg_cost_usd": 0.10, "sample_count": 100,
            },
        ]
        router = ModelPerformanceRouter(perf_repo=repo)
        result = await router.select_model("coding", strategy="cheapest")
        assert result["strategy"] == "cheapest"

    async def test_get_rankings_empty_when_no_repo(self):
        router = ModelPerformanceRouter()
        rankings = await router.get_rankings("coding")
        assert rankings == []

    async def test_get_rankings_returns_sorted(self):
        repo = _FakePerformanceRepo()
        repo._data["coding"] = [
            {
                "service": "a", "model_name": "slow", "success_rate": 0.5,
                "avg_latency_ms": 500, "avg_cost_usd": 0.10, "sample_count": 50,
            },
            {
                "service": "b", "model_name": "fast", "success_rate": 0.9,
                "avg_latency_ms": 100, "avg_cost_usd": 0.01, "sample_count": 200,
            },
        ]
        router = ModelPerformanceRouter(perf_repo=repo)
        rankings = await router.get_rankings("coding")
        assert len(rankings) == 2
        assert rankings[0]["score"] >= rankings[1]["score"]

    async def test_select_model_ranking_fallback_when_repo_empty(self):
        repo = _FakePerformanceRepo()
        repo._data["coding"] = [
            {
                "service": "x", "model_name": "m1", "success_rate": 0.8,
                "avg_latency_ms": 300, "avg_cost_usd": 0.05, "sample_count": 30,
            },
        ]
        router = ModelPerformanceRouter(perf_repo=repo, config={"default_fallback": "openai/gpt-4o"})
        router.set_strategy("coding", "fastest")
        result = await router.select_model("coding")
        assert result["service"] == "x"
        assert result["fallback"] is False


# ---------------------------------------------------------------------------
# 7. Planning — PlanArtifact, PlanCritique, Debt Applier/Evaluator
# ---------------------------------------------------------------------------


class TestPlanArtifact:
    def test_creation_defaults(self):
        artifact = PlanArtifact(todo_id="t1")
        assert artifact.todo_id == "t1"
        assert artifact.title == ""
        assert artifact.target_files == []

    def test_empty_todo_id_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            PlanArtifact(todo_id="")

    def test_to_markdown(self):
        artifact = PlanArtifact(
            todo_id="t1",
            title="My Plan",
            description="Do the thing",
            target_files=["a.py", "b.py"],
            contracts=["contract1"],
            dependencies=["dep1"],
            notes="important",
            content="some content",
        )
        md = artifact.to_markdown()
        assert "## Plan: My Plan" in md
        assert "**Todo ID:** t1" in md
        assert "a.py" in md
        assert "contract1" in md

    def test_round_trip_dict(self):
        artifact = PlanArtifact(todo_id="t1", title="Test", description="desc")
        data = artifact.to_dict()
        restored = PlanArtifact.from_dict(data)
        assert restored.todo_id == "t1"
        assert restored.title == "Test"

    def test_from_todo(self):
        todo = MagicMock()
        todo.todo_id = "t99"
        todo.title = "Refactor X"
        todo.description = "desc"
        todo.tags = ["urgent"]
        todo.test_commands = ["pytest -x"]
        artifact = PlanArtifact.from_todo(todo)
        assert artifact.todo_id == "t99"
        assert "urgent" in artifact.notes


class TestPlanCritique:
    def test_critique_empty_plan_finds_errors(self):
        pc = PlanCritique()
        findings = pc.critique_plan({})
        severities = {f["severity"] for f in findings}
        assert "error" in severities

    def test_critique_complete_plan_no_findings(self):
        pc = PlanCritique()
        plan = {
            "title": "My Plan",
            "description": "A complete plan with enough detail for review.",
            "steps": [{"name": "step1", "description": "First step with enough detail"}],
        }
        findings = pc.critique_plan(plan)
        assert findings == []

    def test_critique_missing_title(self):
        pc = PlanCritique()
        findings = pc.critique_plan({"steps": [{"name": "s1"}]})
        assert any("title" in f["field"] for f in findings)

    def test_critique_vague_description(self):
        pc = PlanCritique()
        plan = {
            "title": "P",
            "description": "plan desc long enough",
            "steps": [{"name": "s1", "description": "short"}],
        }
        findings = pc.critique_plan(plan)
        assert any("vague" in f.get("message", "").lower() for f in findings)

    def test_critique_broken_dependency(self):
        pc = PlanCritique()
        plan = {
            "title": "P",
            "description": "long enough description here",
            "steps": [{"name": "s1", "description": "step one is ten chars"}],
            "dependencies": {"ghost": ["s1"]},
        }
        findings = pc.critique_plan(plan)
        assert any("not a defined step" in f.get("message", "") for f in findings)

    def test_critique_unknown_tool(self):
        pc = PlanCritique()
        plan = {
            "title": "P",
            "description": "long enough description",
            "steps": [{"name": "s1", "description": "step with enough text here", "tool": "fabricator"}],
        }
        findings = pc.critique_plan(plan)
        assert any("unknown tool" in f.get("message", "").lower() for f in findings)

    def test_critique_known_tool_no_warning(self):
        pc = PlanCritique()
        plan = {
            "title": "P",
            "description": "long enough description",
            "steps": [{"name": "s1", "description": "step with bash tool here", "tool": "bash"}],
        }
        findings = pc.critique_plan(plan)
        tool_warnings = [f for f in findings if "tool" in f.get("field", "")]
        assert tool_warnings == []


class TestDebtApplier:
    @pytest.mark.anyio
    async def test_fold_in_augments_plan(self):
        plan = PlanArtifact(todo_id="t1", title="Feature", target_files=["a.py"])
        finding = DebtFinding(
            gap="Add error handling",
            kind="sharp_edge",
            recommendation="fold_in",
            why_it_matters="prevents crashes",
            feature_creep_rationale="close to existing code",
            touched_files=["a.py"],
            effort="small",
        )
        findings = DebtFindings(findings=[finding])
        todo = MagicMock()
        todo.todo_id = "t1"
        repo = _make_todo_repo()
        result = await apply_debt_findings(findings, plan, todo, repo, project_id="p1")
        assert result.folded_in == 1
        assert "Add error handling" in result.augmented_plan.contracts
        assert "a.py" in result.augmented_plan.target_files
        assert result.deferred_todo_ids == []

    @pytest.mark.anyio
    async def test_defer_creates_todo(self):
        plan = PlanArtifact(todo_id="t1")
        finding = DebtFinding(
            gap="Add metrics dashboard",
            kind="missing_feature",
            recommendation="defer",
            why_it_matters="visibility",
            feature_creep_rationale="separate feature",
            touched_files=["dashboard.py"],
            effort="large",
        )
        findings = DebtFindings(findings=[finding])
        todo = MagicMock()
        todo.todo_id = "t1"
        repo = _make_todo_repo()
        result = await apply_debt_findings(findings, plan, todo, repo, project_id="p1")
        assert result.folded_in == 0
        assert len(result.deferred_todo_ids) == 1
        repo.create.assert_called_once()

    @pytest.mark.anyio
    async def test_mixed_findings(self):
        plan = PlanArtifact(todo_id="t1", target_files=["x.py"])
        findings = DebtFindings(findings=[
            DebtFinding(
                gap="fold this", kind="sharp_edge", recommendation="fold_in",
                why_it_matters="w", feature_creep_rationale="r",
                touched_files=["x.py"], effort="small",
            ),
            DebtFinding(
                gap="new feature", kind="missing_feature", recommendation="defer",
                why_it_matters="w", feature_creep_rationale="r",
                touched_files=["y.py"], effort="large",
            ),
        ])
        todo = MagicMock()
        todo.todo_id = "t1"
        result = await apply_debt_findings(findings, plan, todo, _make_todo_repo(), project_id="p1")
        assert result.folded_in == 1
        assert len(result.deferred_todo_ids) == 1


class TestDebtEvaluator:
    def test_deterministic_classify_fold_in(self):
        finding = DebtFinding(
            gap="add retry", kind="sharp_edge", recommendation="defer",
            why_it_matters="w", feature_creep_rationale="r",
            touched_files=["a.py"], effort="small",
        )
        evaluator = DebtEvaluator()
        plan = PlanArtifact(todo_id="t1", target_files=["a.py"])
        classified = evaluator._classify(finding, plan, goal="fix bug")
        assert classified.recommendation == "fold_in"

    def test_deterministic_classify_defer_large_effort(self):
        finding = DebtFinding(
            gap="add retry", kind="sharp_edge", recommendation="fold_in",
            why_it_matters="w", feature_creep_rationale="r",
            touched_files=["a.py"], effort="large",
        )
        evaluator = DebtEvaluator()
        plan = PlanArtifact(todo_id="t1", target_files=["a.py"])
        classified = evaluator._classify(finding, plan, goal="fix bug")
        assert classified.recommendation == "defer"

    def test_deterministic_classify_defer_disjoint_files(self):
        finding = DebtFinding(
            gap="add retry", kind="sharp_edge", recommendation="fold_in",
            why_it_matters="w", feature_creep_rationale="r",
            touched_files=["unrelated.py", "other.py"], effort="small",
        )
        evaluator = DebtEvaluator()
        plan = PlanArtifact(todo_id="t1", target_files=["a.py"])
        classified = evaluator._classify(finding, plan, goal="fix bug")
        assert classified.recommendation == "defer"

    def test_evaluate_no_model_fn_uses_heuristics(self):
        evaluator = DebtEvaluator(evaluate_fn=None)
        plan = PlanArtifact(todo_id="t1", target_files=["a.py"])
        findings = evaluator.evaluate(plan, goal="refactor", repo_context={})
        assert isinstance(findings, DebtFindings)
        # Fallback heuristic: "a.py has no test" → 1 finding

    def test_evaluate_with_model_fn(self):
        evaluator = DebtEvaluator(
            evaluate_fn=lambda plan, goal, repo_context: [
                {"gap": "missing tests", "recommendation": "fold_in", "effort": "small"},
            ]
        )
        plan = PlanArtifact(todo_id="t1", target_files=["a.py"])
        findings = evaluator.evaluate(plan, goal="refactor", repo_context={})
        assert isinstance(findings, DebtFindings)
        assert len(findings.findings) >= 1

    def test_evaluate_fn_returns_malformed(self):
        evaluator = DebtEvaluator(
            evaluate_fn=lambda p, g, rc: "not a list"
        )
        plan = PlanArtifact(todo_id="t1")
        findings = evaluator.evaluate(plan, goal="test", repo_context={})
        assert isinstance(findings, DebtFindings)

    def test_evaluate_fn_raises(self):
        evaluator = DebtEvaluator(
            evaluate_fn=lambda p, g, rc: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        plan = PlanArtifact(todo_id="t1")
        findings = evaluator.evaluate(plan, goal="test", repo_context={})
        assert isinstance(findings, DebtFindings)
        assert findings.findings == []

    def test_findings_to_dict_and_back(self):
        f = DebtFinding(
            gap="missing tests", kind="sharp_edge", recommendation="fold_in",
            why_it_matters="quality", feature_creep_rationale="core",
            touched_files=["a.py"], effort="small",
        )
        d = f.model_dump()
        f2 = DebtFinding(**d)
        assert f2.gap == "missing tests"
        assert f2.recommendation == "fold_in"

    def test_debt_findings_emptyness(self):
        df = DebtFindings(findings=[])
        assert len(df.findings) == 0


# ---------------------------------------------------------------------------
# 8. Pipeline State — config validation, lane state mutations, heartbeat
# ---------------------------------------------------------------------------


class TestPipelineConfig:
    def test_valid_config(self):
        cfg = PipelineConfig(floor=2, target=5, max_worktrees=6)
        assert cfg.floor == 2

    def test_floor_negative_raises(self):
        with pytest.raises(ValueError, match="floor"):
            PipelineConfig(floor=-1)

    def test_target_below_floor_raises(self):
        with pytest.raises(ValueError, match="target"):
            PipelineConfig(floor=5, target=3)

    def test_max_worktrees_below_target_raises(self):
        with pytest.raises(ValueError, match="max_worktrees"):
            PipelineConfig(floor=1, target=3, max_worktrees=2)


class TestLaneState:
    def test_initial_state_empty(self):
        state = LaneState()
        assert len(state.running) == 0
        assert len(state.pending) == 0

    def test_worktree_count(self):
        state = LaneState()
        state.running.add("a")
        state.running.add("b")
        state.completed_awaiting_merge.append(CompletedUnit("c", "/tmp/wt"))
        assert state.worktree_count() == 3

    def test_snapshot_heartbeat(self):
        state = LaneState()
        state.running.add("a")
        state.pending.append("b")
        state.merged_awaiting_gate.append("c")
        hb = state.snapshot_heartbeat(backpressure=False)
        assert hb.running == 1
        assert hb.pending == 1
        assert hb.awaiting_gate == 1
        assert hb.backpressure is False


class TestMergeOutcome:
    def test_merged(self):
        mo = MergeOutcome("u1", merged=True, detail="clean merge")
        assert mo.merged is True
        assert mo.clobber_refused is False

    def test_clobber_refused(self):
        mo = MergeOutcome("u1", merged=False, clobber_refused=True, detail="conflict")
        assert mo.clobber_refused is True


# ---------------------------------------------------------------------------
# 9. Pipeline Lanes — Dispatch, Integrate, Gate step logic
# ---------------------------------------------------------------------------


class TestDispatchLane:
    def make_lane(self, state: LaneState, **overrides: Any) -> DispatchLane:
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6, dispatch_interval_s=0.01)
        cfg_dict = overrides.pop("config", {})
        for k, v in cfg_dict.items():
            setattr(cfg, k, v)
        lane = DispatchLane(cfg, state, asyncio.Lock(), dispatch_fn=overrides.pop("dispatch_fn", _async_noop))
        for k, v in overrides.items():
            setattr(lane, k, v)
        return lane

    async def test_step_dispatches_pending_units(self):
        dispatch_log: list[str] = []

        async def fn(uid: str) -> object:
            dispatch_log.append(uid)
            return None

        state = LaneState()
        state.pending.append("u1")
        state.pending.append("u2")
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6, dispatch_interval_s=0.01)

        lane = DispatchLane(cfg, state, asyncio.Lock(), dispatch_fn=fn)
        dispatched = await lane.step()
        assert len(dispatched) >= 1
        assert "u1" in dispatch_log
        assert state.total_dispatched >= 1

    async def test_step_no_pending_is_noop(self):
        state = LaneState()
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6)
        lane = DispatchLane(cfg, state, asyncio.Lock(), dispatch_fn=_async_noop)
        dispatched = await lane.step()
        assert dispatched == []

    async def test_backpressured_when_max_worktrees_reached(self):
        state = LaneState()
        for i in range(6):
            state.running.add(f"u{i}")
            state.completed_awaiting_merge.append(CompletedUnit(f"m{i}", f"/tmp/wt{i}"))
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6)
        lane = DispatchLane(cfg, state, asyncio.Lock(), dispatch_fn=_async_noop)
        assert lane.backpressured() is True

    async def test_backpressured_when_disk_ok_false(self):
        state = LaneState()
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=10)
        lane = DispatchLane(cfg, state, asyncio.Lock(), dispatch_fn=_async_noop, disk_ok=lambda: False)
        assert lane.backpressured() is True

    async def test_desired_target_with_pid_provider(self):
        outputs = MagicMock()
        outputs.desired_total_active_buckets = 8
        outputs.desired_active_buckets_by_queue = {"pipeline": 5}

        state = LaneState()
        cfg = PipelineConfig(floor=1, target=3, max_worktrees=10)
        lane = DispatchLane(
            cfg, state, asyncio.Lock(), dispatch_fn=_async_noop,
            pid_provider=lambda: outputs, pid_group="pipeline",
        )
        assert lane.desired_target() == 5

    async def test_desired_target_respects_floor(self):
        outputs = MagicMock()
        outputs.desired_total_active_buckets = 0

        state = LaneState()
        cfg = PipelineConfig(floor=10, target=10, max_worktrees=10)
        lane = DispatchLane(
            cfg, state, asyncio.Lock(), dispatch_fn=_async_noop,
            pid_provider=lambda: outputs,
        )
        assert lane.desired_target() >= 10


class TestIntegrateLane:
    def make_lane(self, state: LaneState, merge_fn: Any = None, **overrides: Any) -> IntegrateLane:
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6, integrate_interval_s=0.01)
        lane = IntegrateLane(cfg, state, asyncio.Lock(), merge_fn=merge_fn or _async_merge_ok)
        for k, v in overrides.items():
            setattr(lane, k, v)
        return lane

    async def test_step_idle_when_empty(self):
        state = LaneState()
        lane = self.make_lane(state)
        outcome = await lane.step()
        assert outcome is None

    async def test_step_merges_and_updates_state(self):
        state = LaneState()
        state.running.add("u1")
        unit = CompletedUnit("u1", "/tmp/wt")
        state.completed_awaiting_merge.append(unit)

        lane = self.make_lane(state)
        outcome = await lane.step()
        assert outcome is not None
        assert outcome.merged is True
        assert state.total_merged == 1
        assert "u1" not in state.running
        assert "u1" in state.merged_awaiting_gate

    async def test_step_requeues_on_clobber(self):
        state = LaneState()
        state.running.add("u1")
        unit = CompletedUnit("u1", "/tmp/wt")
        state.completed_awaiting_merge.append(unit)

        async def _refuse(_unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(_unit.unit_id, merged=False, clobber_refused=True, detail="conflict")

        lane = self.make_lane(state, merge_fn=_refuse)
        outcome = await lane.step()
        assert outcome is not None
        assert outcome.clobber_refused is True
        assert state.total_clobbers_refused == 1
        # Requeued
        assert len(state.completed_awaiting_merge) >= 1

    async def test_clobber_exhausted_retries_drops(self):
        state = LaneState()
        state.running.add("u1")
        unit = CompletedUnit("u1", "/tmp/wt")
        state.completed_awaiting_merge.append(unit)

        async def _refuse(_unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(_unit.unit_id, merged=False, clobber_refused=True, detail="conflict")

        lane = self.make_lane(state, merge_fn=_refuse)
        lane._max_clobber_retries = 0
        outcome = await lane.step()
        assert outcome.clobber_refused is True
        assert "u1" not in state.running  # dropped


class TestGateLane:
    def make_lane(self, state: LaneState, gate_fn: Any = None, **overrides: Any) -> GateLane:
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6, gate_debounce_s=0.0, gate_poll_interval_s=0.01)
        lane = GateLane(cfg, state, asyncio.Lock(), gate_fn=gate_fn or _async_gate_green, clock=time.time)
        for k, v in overrides.items():
            setattr(lane, k, v)
        return lane

    async def test_step_returns_none_when_no_work(self):
        state = LaneState()
        lane = self.make_lane(state)
        result = await lane.step()
        assert result is None

    async def test_step_runs_gate_and_returns_green(self):
        state = LaneState()
        state.merged_awaiting_gate.append("u1")
        lane = self.make_lane(state)
        result = await lane.step()
        assert result is True
        assert state.total_gates_run == 1
        assert state.total_gates_green == 1

    async def test_step_returns_red(self):
        state = LaneState()
        state.merged_awaiting_gate.append("u1")

        async def _fail() -> bool:
            return False

        lane = self.make_lane(state, gate_fn=_fail)
        result = await lane.step()
        assert result is False
        assert state.total_gates_green == 0

    async def test_debounce_blocks(self):
        state = LaneState()
        state.merged_awaiting_gate.append("u1")
        state.last_gate_epoch = time.time()
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6, gate_debounce_s=30.0)
        lane = GateLane(cfg, state, asyncio.Lock(), gate_fn=_async_gate_green, clock=time.time)
        result = await lane.step()
        assert result is None

    async def test_green_clears_covered_units(self):
        state = LaneState()
        state.merged_awaiting_gate.extend(["u1", "u2"])
        lane = self.make_lane(state)
        result = await lane.step()
        assert result is True
        assert state.merged_awaiting_gate == []

    async def test_red_keeps_units(self):
        state = LaneState()
        state.merged_awaiting_gate.append("u1")

        async def _fail() -> bool:
            return False

        lane = self.make_lane(state, gate_fn=_fail)
        await lane.step()
        assert "u1" in state.merged_awaiting_gate


# ---------------------------------------------------------------------------
# 10. Pipeline Controller — lifecycle, submit, status
# ---------------------------------------------------------------------------


class TestPipelineController:
    async def test_controller_lifecycle_start_stop(self):
        state = LaneState()
        cfg = PipelineConfig(
            enabled=True, floor=1, target=2, max_worktrees=6,
            dispatch_interval_s=0.01, integrate_interval_s=0.01,
            gate_poll_interval_s=0.01, heartbeat_interval_s=10.0,
        )

        ctrl = PipelineController(cfg, _async_noop, _async_merge_defer, _async_gate_green, state=state)
        await ctrl.start()
        assert ctrl._running is True
        await asyncio.sleep(0.05)
        await ctrl.stop()
        assert ctrl._running is False

    async def test_submit_adds_pending_units(self):
        state = LaneState()
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6)
        ctrl = PipelineController(cfg, _async_noop, _async_merge_defer, _async_gate_green, state=state)
        count = await ctrl.submit(["a", "b", "c"])
        assert count == 3
        assert list(state.pending) == ["a", "b", "c"]

    async def test_report_completed_enqueues(self):
        state = LaneState()
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6)
        ctrl = PipelineController(cfg, _async_noop, _async_merge_defer, _async_gate_green, state=state)
        unit = CompletedUnit("u1", "/tmp/wt")
        await ctrl.report_completed(unit)
        assert len(state.completed_awaiting_merge) == 1
        assert state.completed_awaiting_merge[0].unit_id == "u1"

    async def test_status_snapshot(self):
        state = LaneState()
        state.running.add("u1")
        state.pending.append("u2")
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6)
        ctrl = PipelineController(cfg, _async_noop, _async_merge_defer, _async_gate_green, state=state)
        status = await ctrl.status()
        assert status["running"] == ["u1"]
        assert status["pending"] == ["u2"]
        assert "config" in status
        assert "counters" in status

    async def test_heartbeat_sink_receives_snapshots(self):
        heartbeats: list[Any] = []

        def sink(hb: Any) -> None:
            heartbeats.append(hb)

        cfg = PipelineConfig(
            enabled=True, floor=1, target=2, max_worktrees=6,
            heartbeat_interval_s=0.02, dispatch_interval_s=0.01,
            integrate_interval_s=0.01, gate_poll_interval_s=0.01,
        )
        ctrl = PipelineController(cfg, _async_noop, _async_merge_defer, _async_gate_green,
                                  heartbeat_sink=sink)
        await ctrl.start()
        await asyncio.sleep(0.06)
        await ctrl.stop()
        assert len(heartbeats) >= 1

    async def test_emit_heartbeat_directly(self):
        cfg = PipelineConfig(floor=1, target=2, max_worktrees=6)
        ctrl = PipelineController(cfg, _async_noop, _async_merge_defer, _async_gate_green)
        hb = await ctrl.emit_heartbeat()
        assert hb.running == 0
        assert hb.pending == 0


# ---------------------------------------------------------------------------
# async helpers for lanes
# ---------------------------------------------------------------------------


async def _async_noop(*_args: object, **_kwargs: object) -> object:
    return None


async def _async_merge_ok(unit: CompletedUnit) -> MergeOutcome:
    return MergeOutcome(unit.unit_id, merged=True, detail="ok")


async def _async_merge_defer(_unit: CompletedUnit) -> MergeOutcome:
    return MergeOutcome("", merged=False, detail="deferred")


async def _async_gate_green() -> bool:
    return True
