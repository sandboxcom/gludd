"""S.3: caller-side health gate + budget threading in call_model_with_fallback.

Pins that call_model_with_fallback checks is_healthy before dispatching the
primary through _try_call_model (which has its own built-in health gate), and
that estimated_cost / budget_remaining survive the _try_call_model → call_model
path for every hop.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import (
    BudgetExceededError,
    CircuitBreakerOpenError,
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
    fake.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": "sk-test"}}}
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


class TestPrimaryHealthGateBeforeTryCallModel:
    """call_model_with_fallback gates primary on is_healthy before _try_call_model."""

    def test_healthy_primary_calls_try_call_model(self):
        pri = _profile("pri")
        gw = _gateway(pri)
        gw._health_tracker = None
        expected = _fake_resp("ok")

        with patch.object(gw, "_try_call_model", return_value=expected) as spy:
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result is expected
        spy.assert_called_once()
        assert spy.call_args[0][0] == "pri"
        assert spy.call_args[0][1] == _MSG

    def test_unhealthy_primary_skips_try_call_model(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = _FakeHealthTracker(unhealthy={"pri"})

        with (
            patch.object(gw, "_try_call_model") as spy,
            patch.object(gw, "_walk_fallbacks", return_value=(_fake_resp("fb"), None, [])),
        ):
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result is not None
        assert spy.call_count == 0
        assert result.content == "fb"

    def test_unhealthy_primary_no_fallbacks_raises_circuit_breaker(self):
        pri = _profile("pri", fallback=[])
        gw = _gateway(pri)
        gw._health_tracker = _FakeHealthTracker(unhealthy={"pri"})

        with pytest.raises(CircuitBreakerOpenError, match="All circuits open"):
            gw.call_model_with_fallback("pri", _MSG)

    def test_no_health_tracker_allows_primary(self):
        pri = _profile("pri")
        gw = _gateway(pri)
        gw._health_tracker = None
        expected = _fake_resp("direct")

        with patch.object(gw, "_try_call_model", return_value=expected) as spy:
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result is expected
        spy.assert_called_once()

    def test_try_call_model_returns_none_triggers_fallback(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        with (
            patch.object(gw, "_try_call_model", return_value=None),
            patch.object(gw, "_walk_fallbacks", return_value=(_fake_resp("fb ok"), None, [])),
        ):
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result.content == "fb ok"


class TestBudgetThreadedThroughTryCallModel:
    """estimated_cost and budget_remaining survive _try_call_model → call_model."""

    def test_budget_params_reach_call_model_via_try(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with patch.object(gw, "call_model", return_value=_fake_resp("ok")) as spy:
            gw.call_model_with_fallback(
                "pri",
                _MSG,
                estimated_cost=1.23,
                budget_remaining=45.0,
            )

        _, kwargs = spy.call_args
        assert kwargs["estimated_cost"] == 1.23
        assert kwargs["budget_remaining"] == 45.0

    def test_budget_params_reach_call_model_with_extras(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with patch.object(gw, "call_model", return_value=_fake_resp("ok")) as spy:
            gw.call_model_with_fallback(
                "pri",
                _MSG,
                estimated_cost=5.0,
                budget_remaining=100.0,
                extra_kwarg="val",
            )

        _, kwargs = spy.call_args
        assert kwargs["estimated_cost"] == 5.0
        assert kwargs["budget_remaining"] == 100.0
        assert kwargs["extra_kwarg"] == "val"

    def test_budget_exceeded_from_try_propagates(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with (
            patch.object(
                gw,
                "call_model",
                side_effect=BudgetExceededError("over budget"),
            ),
            pytest.raises(BudgetExceededError, match="over budget"),
        ):
            gw.call_model_with_fallback(
                "pri",
                _MSG,
                estimated_cost=200.0,
                budget_remaining=1.0,
            )

    def test_budget_reaches_walk_fallbacks(self):
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        with patch.object(gw, "_try_call_model", return_value=None), patch.object(gw, "_walk_fallbacks") as walk_spy:
            walk_spy.return_value = (_fake_resp("fb"), None, [])
            gw.call_model_with_fallback(
                "pri",
                _MSG,
                estimated_cost=7.77,
                budget_remaining=42.0,
            )

        _, kwargs = walk_spy.call_args
        assert kwargs["estimated_cost"] == 7.77
        assert kwargs["budget_remaining"] == 42.0

    def test_default_budget_params_are_inf(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with patch.object(gw, "call_model", return_value=_fake_resp("ok")) as spy:
            gw.call_model_with_fallback("pri", _MSG)

        _, kwargs = spy.call_args
        assert kwargs["estimated_cost"] == 0.0
        assert kwargs["budget_remaining"] == float("inf")


class TestTryCallModelHealthGate:
    """_try_call_model itself has a health gate (returns None for unhealthy)."""

    def test_unhealthy_profile_returns_none(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = _FakeHealthTracker(unhealthy={"pri"})

        with patch.object(gw, "call_model") as spy:
            result = gw._try_call_model("pri", _MSG)

        assert result is None
        spy.assert_not_called()

    def test_healthy_profile_calls_model(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = _FakeHealthTracker(unhealthy=set())

        expected = _fake_resp("healthy")
        with patch.object(gw, "call_model", return_value=expected) as spy:
            result = gw._try_call_model("pri", _MSG)

        assert result is expected
        spy.assert_called_once()

    def test_no_health_tracker_calls_model(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        expected = _fake_resp("no tracker")
        with patch.object(gw, "call_model", return_value=expected) as spy:
            result = gw._try_call_model("pri", _MSG)

        assert result is expected
        spy.assert_called_once()

    def test_generic_exception_returns_none(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with patch.object(gw, "call_model", side_effect=ValueError("boom")):
            result = gw._try_call_model("pri", _MSG)

        assert result is None

    def test_budget_exceeded_not_swallowed(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with (
            patch.object(
                gw,
                "call_model",
                side_effect=BudgetExceededError("no money"),
            ),
            pytest.raises(BudgetExceededError, match="no money"),
        ):
            gw._try_call_model("pri", _MSG)


class TestHealthGateConsistency:
    """The health gate in call_model_with_fallback, _try_call_model, and
    call_model are consistent in their is_healthy semantics."""

    def test_call_model_gates_on_health(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = _FakeHealthTracker(unhealthy={"pri"})

        with (
            patch.object(gw._health_tracker, "is_healthy", return_value=False) as spy,
            pytest.raises(CircuitBreakerOpenError),
        ):
            gw.call_model("pri", _MSG)

        spy.assert_called_once_with("pri", admit_probe=False)

    def test_call_model_skip_health_check(self):
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = _FakeHealthTracker(unhealthy={"pri"})

        expected = _fake_resp("bypass")
        with patch.object(gw, "_invoke_and_bill", return_value=expected) as spy:
            result = gw.call_model("pri", _MSG, _skip_health_check=True)

        assert result is expected
        spy.assert_called_once()

    def test_primary_health_uses_admit_probe_default(self):
        pri = _profile("pri")
        gw = _gateway(pri)
        gw._health_tracker = _FakeHealthTracker(unhealthy=set())

        with (
            patch.object(gw._health_tracker, "is_healthy", wraps=gw._health_tracker.is_healthy) as spy,
            patch.object(gw, "_try_call_model", return_value=_fake_resp("ok")),
        ):
            gw.call_model_with_fallback("pri", _MSG)

        spy.assert_called()
        args, _kwargs = spy.call_args
        assert args[0] == "pri"
