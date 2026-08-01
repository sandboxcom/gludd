"""S.3: ``_try_call_model`` health gate + budget threading through call chain.

Verify:
(a) Unhealthy backends are skipped — ``_try_call_model`` returns None.
(b) Budget is preserved across fallback — ``estimated_cost`` and
    ``budget_remaining`` reach ``call_model`` for every hop.
(c) Healthy backends are used normally — ``_try_call_model`` succeeds.
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

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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
    r.usage = {}
    return r


# ---------------------------------------------------------------------------
# (a) unhealthy backends are skipped
# ---------------------------------------------------------------------------


class TestUnhealthyBackendsSkipped:
    def test_try_call_model_returns_none_when_unhealthy(self):
        """_try_call_model checks is_healthy and returns None for unhealthy."""
        p = _profile("sick")
        gw = _gateway(p)
        gw._health_tracker = _FakeHealthTracker(unhealthy={"sick"})

        with patch.object(gw, "call_model") as spy:
            result = gw._try_call_model("sick", _MSG)

        assert result is None
        assert spy.call_count == 0

    def test_try_call_model_calls_healthy_profile(self):
        """_try_call_model calls call_model when profile is healthy."""
        p = _profile("ok")
        gw = _gateway(p)
        gw._health_tracker = _FakeHealthTracker(unhealthy=set())
        expected = _fake_resp("hello")

        with patch.object(gw, "call_model", return_value=expected) as spy:
            result = gw._try_call_model("ok", _MSG)

        assert result is expected
        spy.assert_called_once_with("ok", _MSG)

    def test_try_call_model_calls_when_no_health_tracker(self):
        """_try_call_model calls call_model when no health tracker is set."""
        p = _profile("no_tracker")
        gw = _gateway(p)
        gw._health_tracker = None
        expected = _fake_resp("no tracker")

        with patch.object(gw, "call_model", return_value=expected) as spy:
            result = gw._try_call_model("no_tracker", _MSG)

        assert result is expected
        spy.assert_called_once_with("no_tracker", _MSG)


# ---------------------------------------------------------------------------
# (b) budget is preserved across fallback
# ---------------------------------------------------------------------------


class TestBudgetPreservedAcrossFallback:
    def test_budget_reaches_call_model_on_primary(self):
        """estimated_cost and budget_remaining reach call_model for primary."""
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None

        with patch.object(gw, "call_model", return_value=_fake_resp("ok")) as spy:
            gw.call_model_with_fallback(
                "pri",
                _MSG,
                estimated_cost=3.14,
                budget_remaining=99.0,
            )

        _, kwargs = spy.call_args
        assert kwargs["estimated_cost"] == 3.14
        assert kwargs["budget_remaining"] == 99.0

    def test_budget_reaches_call_model_on_fallback(self):
        """estimated_cost and budget_remaining reach call_model on fallback hop."""
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        def _fail_pri_then_ok(profile_id, messages, **kwargs):
            if profile_id == "pri":
                raise RuntimeError("primary down")
            return _fake_resp("fb ok")

        with patch.object(gw, "call_model", side_effect=_fail_pri_then_ok) as spy:
            gw.call_model_with_fallback(
                "pri",
                _MSG,
                estimated_cost=7.5,
                budget_remaining=42.0,
            )

        # last call was to fb
        _, kwargs = spy.call_args_list[-1]
        assert kwargs["estimated_cost"] == 7.5
        assert kwargs["budget_remaining"] == 42.0

    def test_budget_reaches_try_call_model_via_kwargs(self):
        """_try_call_model threads estimated_cost and budget_remaining."""
        p = _profile("p")
        gw = _gateway(p)
        gw._health_tracker = None

        with patch.object(gw, "call_model", return_value=_fake_resp("ok")) as spy:
            gw._try_call_model(
                "p",
                _MSG,
                estimated_cost=1.23,
                budget_remaining=45.6,
            )

        _, kwargs = spy.call_args
        assert kwargs["estimated_cost"] == 1.23
        assert kwargs["budget_remaining"] == 45.6

    def test_budget_exceeded_propagates_through_fallback(self):
        """BudgetExceededError on fallback propagates, not swallowed."""
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb", budget=0.01)
        gw = _gateway(pri, fb)
        gw._health_tracker = None

        def _fail(profile_id, messages, **kwargs):
            if profile_id == "pri":
                raise RuntimeError("primary down")
            raise BudgetExceededError("fb broke")

        with patch.object(gw, "call_model", side_effect=_fail), pytest.raises(BudgetExceededError):
            gw.call_model_with_fallback("pri", _MSG)

    def test_budget_reaches_multiple_fallback_hops(self):
        """Budget params survive through multiple fallback hops."""
        pri = _profile("pri", fallback=["fb1"])
        fb1 = _profile("fb1", fallback=["fb2"])
        fb2 = _profile("fb2")
        gw = _gateway(pri, fb1, fb2)
        gw._health_tracker = None

        def _fail_first_two(profile_id, messages, **kwargs):
            if profile_id in ("pri", "fb1"):
                raise RuntimeError(f"{profile_id} down")
            return _fake_resp("fb2 ok")

        with patch.object(gw, "call_model", side_effect=_fail_first_two) as spy:
            gw.call_model_with_fallback(
                "pri",
                _MSG,
                estimated_cost=2.0,
                budget_remaining=30.0,
            )

        _, kwargs = spy.call_args_list[-1]
        assert kwargs["estimated_cost"] == 2.0
        assert kwargs["budget_remaining"] == 30.0


# ---------------------------------------------------------------------------
# (c) healthy backends are used normally
# ---------------------------------------------------------------------------


class TestHealthyBackendsUsedNormally:
    def test_primary_healthy_returns_directly(self):
        """call_model_with_fallback returns primary response when healthy."""
        p = _profile("pri")
        gw = _gateway(p)
        gw._health_tracker = None
        expected = _fake_resp("direct")

        with patch.object(gw, "call_model", return_value=expected):
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result.content == "direct"

    def test_primary_unhealthy_fallthrough_to_healthy_fallback(self):
        """Primary circuit-open, fallback healthy → fallback used."""
        pri = _profile("pri", fallback=["fb"])
        fb = _profile("fb")
        gw = _gateway(pri, fb)
        gw._health_tracker = _FakeHealthTracker(unhealthy={"pri"})

        with patch.object(gw, "call_model", return_value=_fake_resp("fb ok")) as spy:
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result.content == "fb ok"
        # primary must NOT have been attempted (circuit open)
        attempted = [c.args[0] for c in spy.call_args_list]
        assert "pri" not in attempted
        assert "fb" in attempted

    def test_all_healthy_all_used_on_primary_failure(self):
        """When primary fails (not circuit-open), healthy fallbacks are tried."""
        pri = _profile("pri", fallback=["fb_a", "fb_b"])
        fb_a = _profile("fb_a")
        fb_b = _profile("fb_b")
        gw = _gateway(pri, fb_a, fb_b)
        gw._health_tracker = None

        def _primary_fails(profile_id, messages, **kwargs):
            if profile_id == "pri":
                raise RuntimeError("primary down")
            return _fake_resp(f"{profile_id} ok")

        with patch.object(gw, "call_model", side_effect=_primary_fails) as spy:
            result = gw.call_model_with_fallback("pri", _MSG)

        assert result.content == "fb_a ok"
        attempted = [c.args[0] for c in spy.call_args_list]
        assert "pri" in attempted
        assert "fb_a" in attempted
        assert "fb_b" not in attempted

    def test_all_unhealthy_raises_circuit_breaker(self):
        """All profiles circuit-open → CircuitBreakerOpenError."""
        pri = _profile("pri", fallback=["fb1", "fb2"])
        fb1 = _profile("fb1")
        fb2 = _profile("fb2")
        gw = _gateway(pri, fb1, fb2)
        gw._health_tracker = _FakeHealthTracker(
            unhealthy={"pri", "fb1", "fb2"}
        )

        with patch.object(gw, "_try_call_model") as spy, \
             pytest.raises(CircuitBreakerOpenError, match="All circuits open"):
            gw.call_model_with_fallback("pri", _MSG)

        assert spy.call_count == 0
