"""S.3: gateway.py call_model_with_fallback health gate + budget threading.

Verifies the two fixes:
(a) call_model_with_fallback uses _try_call_model (with health gate) for primary.
(b) Budget is threaded through _try_call_model → call_model for every hop.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
        api_metered=False,
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


class TestCallModelWithFallbackUsesTryCallModel:
    """call_model_with_fallback routes primary through _try_call_model."""

    def test_primary_healthy_uses_try_call_model(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = None
        expected = _fake_resp("direct")

        with patch.object(gw, "_try_call_model", return_value=expected) as spy:
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result is expected
        spy.assert_called_once()
        assert spy.call_args[0][0] == "pri"

    def test_primary_unhealthy_skips_try_call_model(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = _FakeHealthTracker(unhealthy={"pri"})

        with patch.object(gw, "_try_call_model") as spy, \
             patch.object(gw, "_walk_fallbacks",
                          return_value=(_fake_resp("fb ok"), None, [])):
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result is not None
        assert spy.call_count == 0

    def test_try_call_model_none_triggers_fallback(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        def _try_returns_none(profile_id, messages, **kwargs):
            if profile_id == "pri":
                return None
            return _fake_resp("fb ok")

        with patch.object(gw, "_try_call_model", side_effect=_try_returns_none), \
             patch.object(gw, "_walk_fallbacks",
                          return_value=(_fake_resp("fb walk"), None, [])):
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result.content == "fb walk"


class TestBudgetThreadedThroughTryCallModel:
    """Budget params survive _try_call_model → call_model path."""

    def test_estimated_cost_reaches_call_model_via_try(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with patch.object(gw, "call_model", return_value=_fake_resp("ok")) as spy:
            gw.call_model_with_fallback(
                "pri", _MSG,
                estimated_cost=3.14,
                budget_remaining=99.0,
            )

        _, kwargs = spy.call_args
        assert kwargs["estimated_cost"] == 3.14
        assert kwargs["budget_remaining"] == 99.0

    def test_budget_exceeded_propagates_from_try_call_model(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with patch.object(gw, "call_model",
                          side_effect=BudgetExceededError("over budget")), \
             pytest.raises(BudgetExceededError, match="over budget"):
            gw.call_model_with_fallback(
                "pri", _MSG,
                estimated_cost=100.0,
                budget_remaining=10.0,
            )

    def test_budget_reaches_walk_fallbacks_via_call_kwargs(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        with patch.object(gw, "_try_call_model", return_value=None), \
             patch.object(gw, "_walk_fallbacks") as walk_spy:
            walk_spy.return_value = (_fake_resp("fb ok"), None, [])
            gw.call_model_with_fallback(
                "pri", _MSG,
                estimated_cost=5.0,
                budget_remaining=50.0,
            )

        _, kwargs = walk_spy.call_args
        assert kwargs["estimated_cost"] == 5.0
        assert kwargs["budget_remaining"] == 50.0
