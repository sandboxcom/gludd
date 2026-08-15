from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.manager import SecretAlias, SecretsManager


def _make_profile(
    pid: str,
    fallback: list[str] | None = None,
    budget: float = 200.0,
    enabled: bool = True,
) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        enabled=enabled,
        provider="openai",
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name=f"model-{pid}",
        credential_alias="openai_key",
        run_budget_usd=budget,
        cost_per_input_token=0.000005,
        cost_per_output_token=0.000015,
        fallback_profiles=fallback or [],
    )


def _make_gateway(profiles: list[ModelProfile]) -> tuple[ModelGateway, ProviderRegistry]:
    reg = ProviderRegistry()
    reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

    fake_secret_client = MagicMock()
    fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": "sk-test"}}}
    secrets = SecretsManager(
        client=fake_secret_client,
        aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
    )

    gw = ModelGateway(
        profiles=profiles,
        provider_registry=reg,
        secrets_manager=secrets,
        # Peak-time billing clock (multiplier 1.0) keeps cost-tracking pins
        # deterministic against peak/off-peak pricing variance.
        billing_clock=lambda: datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC),
    )
    return gw, reg


def _fake_response(content: str, cost_input: float = 0.0, cost_output: float = 0.0) -> MagicMock:
    return MagicMock(
        content=content,
        usage_metadata={"input_tokens": cost_input, "output_tokens": cost_output},
    )


class TestFallbackOnProfileNotFound:
    def test_fallback_on_profile_not_found(self):
        fallback_prof = _make_profile("fallback_prof")
        gw, reg = _make_gateway([fallback_prof])

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = _fake_response("from fallback")
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model_with_fallback(
                "missing_primary",
                [{"role": "user", "content": "hi"}],
                fallback_profiles=["fallback_prof"],
            )

        assert resp.content == "from fallback"
        assert resp.model_name == "model-fallback_prof"


class TestFallbackOnBudgetExceeded:
    def test_budget_exceeded_propagates_d24(self):
        # D-24: a per-profile budget rejection MUST propagate as a
        # BudgetExceededError (a ValueError subclass) instead of being silently
        # swallowed and routed around via the fallback chain. Previously
        # _try_call_model caught *all* ValueErrors and returned None, so
        # call_model_with_fallback treated the rejection as a soft failure and
        # tried the next profile — bypassing the per-profile spending cap.
        primary = _make_profile("expensive", budget=0.001, fallback=["cheap"])
        cheap = _make_profile("cheap", budget=999.0)
        gw, reg = _make_gateway([primary, cheap])

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = _fake_response("from cheap")
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            pytest.raises(ValueError, match="over budget"),
        ):
            gw.call_model_with_fallback(
                "expensive",
                [{"role": "user", "content": "hi"}],
                estimated_cost=5.0,
                budget_remaining=100.0,
            )

        # The fallback must NOT have been invoked: the per-profile cap on the
        # primary is a hard rejection, not a soft hint.
        assert not fake_instance.invoke.called


class TestFallbackChainExhausted:
    def test_fallback_chain_exhausted_raises(self):
        # D-24: when the primary is rejected for being over budget, that
        # BudgetExceededError propagates immediately — the fallback chain is
        # not walked, because the per-profile cap is a hard rejection.
        primary = _make_profile("bad1", budget=0.001, fallback=["bad2"])
        bad2 = _make_profile("bad2", budget=0.001)
        gw, reg = _make_gateway([primary, bad2])

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=MagicMock()),
            pytest.raises(ValueError, match="over budget"),
        ):
            gw.call_model_with_fallback(
                "bad1",
                [{"role": "user", "content": "hi"}],
                estimated_cost=5.0,
                budget_remaining=1.0,
            )


class TestFallbackPreservesCostTracking:
    def test_fallback_preserves_cost_tracking(self):
        # D-24: per-profile budget rejections now propagate, so to exercise the
        # fallback cost-tracking path we make the PRIMARY fail with a non-budget
        # provider error (RuntimeError) and let the cheap fallback serve.
        primary = _make_profile("expensive_primary", budget=999.0, fallback=["cheap"])
        cheap = _make_profile(
            "cheap",
            budget=999.0,
        )
        # The fake provider reports 50 output tokens below; keep this cost-
        # tracking fixture at the new hard-limit boundary instead of asking the
        # gateway to accept a response five times over its configured ceiling.
        cheap.max_output_tokens = 50
        cheap.cost_per_input_token = 0.01
        cheap.cost_per_output_token = 0.03
        gw, reg = _make_gateway([primary, cheap])

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = _fake_response("from cheap", cost_input=100, cost_output=50)
        FakeChatModel.return_value = fake_instance

        # Primary provider raises a non-budget error so the fallback chain runs.
        primary_fail = MagicMock()
        primary_fail.invoke.side_effect = RuntimeError("primary provider down")

        def _get_provider_class(provider, package=None, class_hint=None):
            # The first registered profile (primary) gets the failing instance;
            # everything else gets the succeeding one.
            return primary_fail

        # Simpler: route by checking which profile is calling. We patch
        # call_model on the primary path instead.
        original_call_model = gw.call_model

        def _call_model_dispatch(profile_id, messages, **kwargs):
            if profile_id == "expensive_primary":
                raise RuntimeError("primary provider down")
            return original_call_model(profile_id, messages, **kwargs)

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch.object(gw, "call_model", side_effect=_call_model_dispatch),
        ):
            resp = gw.call_model_with_fallback(
                "expensive_primary",
                [{"role": "user", "content": "hi"}],
                estimated_cost=5.0,
                budget_remaining=100.0,
            )

        expected_cost = 100 * 0.01 + 50 * 0.03
        assert resp.cost_estimate == pytest.approx(expected_cost)


class TestNoFallbackWhenPrimarySucceeds:
    def test_no_fallback_when_primary_succeeds(self):
        primary = _make_profile("primary_ok", fallback=["unused"])
        unused = _make_profile("unused")
        gw, reg = _make_gateway([primary, unused])

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = _fake_response("primary response")
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model_with_fallback(
                "primary_ok",
                [{"role": "user", "content": "hi"}],
            )

        assert resp.content == "primary response"
        assert resp.model_name == "model-primary_ok"


class TestCallModelWithFallbackMethod:
    def test_call_model_with_fallback_exists(self):
        gw = ModelGateway()
        assert hasattr(gw, "call_model_with_fallback")
        assert callable(gw.call_model_with_fallback)


class _FakeHealthTracker:
    """Minimal health tracker that reports a fixed set of profiles unhealthy."""

    def __init__(self, unhealthy: set[str]) -> None:
        self._unhealthy = unhealthy

    def is_healthy(self, profile_id: str, *, admit_probe: bool = True) -> bool:
        return profile_id not in self._unhealthy

    def record_success(self, profile_id: str) -> None:
        self._unhealthy.discard(profile_id)

    def record_failure(self, profile_id: str, kind: object = None) -> None:
        self._unhealthy.add(profile_id)


class TestFallbackHealthGate:
    def _gateway_with_tracker(
        self, profiles: list[ModelProfile], tracker: _FakeHealthTracker
    ) -> tuple[ModelGateway, ProviderRegistry]:
        gw, reg = _make_gateway(profiles)
        gw._health_tracker = tracker
        return gw, reg

    def test_circuit_open_primary_is_skipped(self):
        # Primary is circuit-open: the gateway must NOT attempt it and must fall
        # through to the healthy fallback instead.
        primary = _make_profile("primary_open", fallback=["healthy_fb"])
        fb = _make_profile("healthy_fb")
        tracker = _FakeHealthTracker(unhealthy={"primary_open"})
        gw, reg = self._gateway_with_tracker([primary, fb], tracker)

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = _fake_response("from healthy fallback")
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch.object(gw, "_try_call_model", wraps=gw._try_call_model) as spy,
            patch.object(gw, "_call_fallback", wraps=gw._call_fallback) as fb_spy,
        ):
            resp = gw.call_model_with_fallback(
                "primary_open",
                [{"role": "user", "content": "hi"}],
            )

        assert resp.content == "from healthy fallback"
        # The open primary must never have been attempted via _try_call_model.
        try_calls = [c.args[0] for c in spy.call_args_list]
        assert "primary_open" not in try_calls
        # The healthy fallback is routed through _call_fallback.
        fallback_calls = [c.args[0] for c in fb_spy.call_args_list]
        assert "healthy_fb" in fallback_calls

    def test_circuit_open_fallback_is_skipped(self):
        # A circuit-open fallback is skipped; a later healthy fallback serves.
        primary = _make_profile("primary_open", fallback=["fb_open", "fb_ok"])
        fb_open = _make_profile("fb_open")
        fb_ok = _make_profile("fb_ok")
        tracker = _FakeHealthTracker(unhealthy={"primary_open", "fb_open"})
        gw, reg = self._gateway_with_tracker([primary, fb_open, fb_ok], tracker)

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = _fake_response("from fb_ok")
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch.object(gw, "_try_call_model", wraps=gw._try_call_model) as spy,
            patch.object(gw, "_call_fallback", wraps=gw._call_fallback) as fb_spy,
        ):
            resp = gw.call_model_with_fallback(
                "primary_open",
                [{"role": "user", "content": "hi"}],
            )

        assert resp.content == "from fb_ok"
        # The open primary must never have been attempted via _try_call_model.
        try_calls = [c.args[0] for c in spy.call_args_list]
        assert "primary_open" not in try_calls
        # Fallbacks are routed through _call_fallback.
        fallback_calls = [c.args[0] for c in fb_spy.call_args_list]
        assert "fb_open" not in fallback_calls
        assert "fb_ok" in fallback_calls
