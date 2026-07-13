"""S.3: gateway.py call_model_with_fallback health gate + budget threading.

Covers four gaps in _walk_fallbacks:
1. Fallback skips over-budget provider (BudgetExceededError doesn't abort chain)
2. Budget pre-check before each fallback attempt
3. Health check timeout doesn't block indefinitely
4. Budget params flow to call_model for each fallback hop
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from general_ludd.models.gateway import (
    BudgetExceededError,
    ModelGateway,
    ModelProfile,
)
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.manager import SecretAlias, SecretsManager

_MSG = [{"role": "user", "content": "hi"}]


def _profile(
    pid: str,
    fallback: list[str] | None = None,
    budget: float = 200.0,
) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        enabled=True,
        provider="openai",
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name=f"model-{pid}",
        credential_alias="openai_key",
        run_budget_usd=budget,
        fallback_profiles=fallback or [],
    )


def _gateway(*profiles: ModelProfile) -> ModelGateway:
    reg = ProviderRegistry()
    reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
    fake = MagicMock()
    fake.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"value": "sk-test"}}
    }
    secrets = SecretsManager(
        client=fake,
        aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
    )
    return ModelGateway(
        profiles=list(profiles),
        provider_registry=reg,
        secrets_manager=secrets,
    )


class _FakeHealthTracker:
    def __init__(self, unhealthy: set[str]) -> None:
        self._unhealthy = unhealthy

    def is_healthy(self, profile_id: str, *, admit_probe: bool = True) -> bool:
        return profile_id not in self._unhealthy

    def record_success(self, profile_id: str) -> None:
        self._unhealthy.discard(profile_id)

    def record_failure(self, profile_id: str, kind: object = None) -> None:
        self._unhealthy.add(profile_id)


def _fake_resp(content: str) -> MagicMock:
    r = MagicMock()
    r.content = content
    r.usage_metadata = {}
    return r


class TestFallbackSkipsOverBudgetProvider:
    """When a fallback exceeds budget, skip it and try the next one."""

    def test_budget_exceeded_on_fallback_skips_to_next(self):
        pri = _profile("pri", fallback=["over", "ok"])
        over_budget = _profile("over", budget=200.0)
        ok_fb = _profile("ok", budget=200.0)
        gw = _gateway(pri, over_budget, ok_fb)
        gw._health_tracker = None

        def fake_try(profile_id, _messages, **_kwargs):
            if profile_id == "over":
                raise BudgetExceededError("over budget for over")
            return _fake_resp(f"from {profile_id}")

        with patch.object(gw, "call_model", side_effect=fake_try) as spy:
            result = gw._walk_fallbacks(
                ["over", "ok"], _MSG,
                estimated_cost=5.0,
                budget_remaining=50.0,
            )

        resp, _exc, _attempts = result
        assert resp is not None
        assert resp.content == "from ok"
        attempted = [c.args[0] for c in spy.call_args_list]
        assert "over" in attempted
        assert "ok" in attempted

    def test_all_fallbacks_over_budget_returns_none(self):
        pri = _profile("pri", fallback=["fb1", "fb2"])
        fb1 = _profile("fb1", budget=0.01)
        fb2 = _profile("fb2", budget=0.01)
        gw = _gateway(pri, fb1, fb2)
        gw._health_tracker = None

        def fake_try(_profile_id, _messages, **_kwargs):
            raise BudgetExceededError("over budget")

        with patch.object(gw, "call_model", side_effect=fake_try):
            result = gw._walk_fallbacks(
                ["fb1", "fb2"], _MSG,
                estimated_cost=5.0,
                budget_remaining=50.0,
            )

        resp, _exc, attempts = result
        assert resp is None
        assert len(attempts) >= 2


class TestBudgetPrecheckBeforeFallbackAttempt:
    """check_budget is called before each fallback; over-budget ones are skipped
    without ever calling call_model."""

    def test_precheck_skips_over_budget_fallback(self):
        pri = _profile("pri", fallback=["expensive", "cheap"])
        expensive = _profile("expensive", budget=0.001)
        cheap = _profile("cheap", budget=200.0)
        gw = _gateway(pri, expensive, cheap)
        gw._health_tracker = None

        fake_resp_cheap = _fake_resp("cheap win")

        def fake_call(profile_id, _messages, **_kwargs):
            if profile_id == "expensive":
                raise AssertionError("expensive should have been skipped by pre-check")
            return fake_resp_cheap

        with patch.object(gw, "call_model", side_effect=fake_call) as spy:
            result = gw._walk_fallbacks(
                ["expensive", "cheap"], _MSG,
                estimated_cost=5.0,
                budget_remaining=50.0,
            )

        resp, _exc, _attempts = result
        assert resp is not None
        assert resp.content == "cheap win"
        attempted = [c.args[0] for c in spy.call_args_list]
        assert "expensive" not in attempted
        assert "cheap" in attempted

    def test_precheck_skips_when_estimated_exceeds_remaining(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb", budget=200.0)
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        with patch.object(gw, "call_model") as spy:
            result = gw._walk_fallbacks(
                ["fb"], _MSG,
                estimated_cost=100.0,
                budget_remaining=5.0,
            )

        resp, _exc, _attempts = result
        assert resp is None
        spy.assert_not_called()

    def test_precheck_passes_when_within_budget(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb", budget=200.0)
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        expected = _fake_resp("within budget")
        with patch.object(gw, "call_model", return_value=expected) as spy:
            result = gw._walk_fallbacks(
                ["fb"], _MSG,
                estimated_cost=5.0,
                budget_remaining=50.0,
            )

        resp, _exc, _attempts = result
        assert resp is not None
        assert resp.content == "within budget"
        spy.assert_called_once()
        assert spy.call_args[0][0] == "fb"


class TestHealthCheckTimeoutDoesNotBlock:
    """is_healthy is guarded by a timeout; a hung health check is treated as
    unhealthy so the fallback chain makes progress."""

    def test_hung_health_check_treated_as_unhealthy(self):
        pri = _profile("pri", fallback=["hung", "ok"])
        hung_fb = _profile("hung", budget=200.0)
        ok_fb = _profile("ok", budget=200.0)
        gw = _gateway(pri, hung_fb, ok_fb)

        class _SlowHealthTracker:
            def __init__(self) -> None:
                self._check_called: list[str] = []

            def is_healthy(self, profile_id: str, *, admit_probe: bool = True) -> bool:
                self._check_called.append(profile_id)
                if profile_id == "hung":
                    time.sleep(10.0)
                return True

            def record_success(self, profile_id: str) -> None:
                pass

            def record_failure(self, profile_id: str, kind: object = None) -> None:
                pass

        tracker = _SlowHealthTracker()
        gw._health_tracker = tracker

        fake_resp_ok = _fake_resp("ok after timeout")

        with patch.object(gw, "call_model", return_value=fake_resp_ok) as spy:
            result = gw._walk_fallbacks(
                ["hung", "ok"], _MSG,
                estimated_cost=5.0,
                budget_remaining=50.0,
            )

        resp, _exc, _attempts = result
        assert resp is not None
        assert resp.content == "ok after timeout"
        attempted = [c.args[0] for c in spy.call_args_list]
        assert "hung" not in attempted
        assert "ok" in attempted

    def test_healthy_check_returns_quickly(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb", budget=200.0)
        gw = _gateway(pri, fb)
        gw._health_tracker = _FakeHealthTracker(unhealthy=set())

        expected = _fake_resp("healthy fast")
        with patch.object(gw, "call_model", return_value=expected) as spy:
            result = gw._walk_fallbacks(
                ["fb"], _MSG,
                estimated_cost=5.0,
                budget_remaining=50.0,
            )

        resp, _exc, _attempts = result
        assert resp is not None
        assert resp.content == "healthy fast"
        spy.assert_called_once()


class TestBudgetParamsFlowToFallbackCalls:
    """estimated_cost and budget_remaining reach call_model for each fallback hop."""

    def test_budget_params_in_call_model_for_fallback(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb", budget=200.0)
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        expected = _fake_resp("budget threaded")

        with patch.object(gw, "call_model", return_value=expected) as spy:
            gw._walk_fallbacks(
                ["fb"], _MSG,
                estimated_cost=7.77,
                budget_remaining=42.0,
            )

        _, kwargs = spy.call_args
        assert kwargs.get("estimated_cost") == 7.77
        assert kwargs.get("budget_remaining") == 42.0

    def test_call_model_with_fallback_threads_budget_to_walk(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb", budget=200.0)
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        with patch.object(gw, "_try_call_model", return_value=None), \
             patch.object(gw, "_walk_fallbacks") as walk_spy:
            walk_spy.return_value = (_fake_resp("ok"), None, [])
            gw.call_model_with_fallback(
                "pri", _MSG,
                estimated_cost=3.33,
                budget_remaining=66.6,
            )

        _, kwargs = walk_spy.call_args
        assert kwargs["estimated_cost"] == 3.33
        assert kwargs["budget_remaining"] == 66.6
