from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import yaml

from agentic_harness.controllers.budget import RunBudgetGuard
from agentic_harness.models.gateway import ModelGateway, ModelProfile
from agentic_harness.models.provider_registry import ProviderRegistry
from agentic_harness.models.router import ModelRouter
from agentic_harness.secrets.manager import SecretAlias, SecretsManager

CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config",
    "model_profiles",
)


def _load_all_yaml_profiles() -> list[ModelProfile]:
    profiles: list[ModelProfile] = []
    for fname in sorted(os.listdir(CONFIG_DIR)):
        if not fname.endswith(".yml"):
            continue
        with open(os.path.join(CONFIG_DIR, fname)) as fh:
            data = yaml.safe_load(fh)
        profiles.append(ModelProfile(**data))
    return profiles


def _make_gateway_with_mock(
    profiles: list[ModelProfile],
) -> tuple[ModelGateway, ProviderRegistry]:
    reg = ProviderRegistry()
    reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

    fake_secret_client = MagicMock()
    fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"value": "sk-test"}}
    }
    secrets = SecretsManager(
        client=fake_secret_client,
        aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
    )

    gw = ModelGateway(
        profiles=profiles,
        provider_registry=reg,
        secrets_manager=secrets,
    )
    return gw, reg


class TestModelRouterResolvesRolesFromProfiles:
    def test_model_router_resolves_roles_from_profiles(self):
        profiles = _load_all_yaml_profiles()
        router = ModelRouter.build_from_profiles(profiles)
        for p in profiles:
            for role_name in p.role_names:
                assert router.resolve_role(role_name) is not None


class TestModelRouterWeakModelFallback:
    def test_model_router_weak_model_fallback(self):
        router = ModelRouter(
            role_mapping={"coder": "strong_model"},
            weak_model_profile_id="tiny_model",
        )
        assert router.resolve_role("weak") == "tiny_model"
        assert router.resolve_role("coder") == "strong_model"


class TestModelRouterDefaultProfile:
    def test_model_router_default_profile(self):
        router = ModelRouter(
            role_mapping={"coder": "coder_model"},
            default_profile_id="default_prof",
        )
        assert router.resolve_role("unknown_role") == "default_prof"
        assert router.resolve_role("coder") == "coder_model"


class TestModelRouterBuildFromProfilesAuto:
    def test_model_router_build_from_profiles_auto(self):
        profiles = _load_all_yaml_profiles()
        router = ModelRouter.build_from_profiles(profiles)
        all_profiles = {p.model_profile_id for p in profiles}
        for p in profiles:
            if p.quality_class:
                resolved = router.resolve_by_quality(p.quality_class)
                assert resolved is not None
                assert resolved in all_profiles
            if p.latency_class:
                resolved = router.resolve_by_latency(p.latency_class)
                assert resolved is not None
                assert resolved in all_profiles


class TestGatewayFallbackChainIntegration:
    def test_gateway_fallback_chain_integration(self):
        primary = ModelProfile(
            model_profile_id="primary_prof",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            enabled=True,
            run_budget_usd=0.001,
            fallback_profiles=["fallback_prof"],
        )
        fallback = ModelProfile(
            model_profile_id="fallback_prof",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-3.5-turbo",
            enabled=True,
            run_budget_usd=999.0,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        gw, reg = _make_gateway_with_mock([primary, fallback])

        FakeChat = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="fallback response",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
        )
        FakeChat.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChat),
        ):
            resp = gw.call_model_with_fallback(
                "primary_prof",
                [{"role": "user", "content": "hello"}],
                estimated_cost=5.0,
                budget_remaining=100.0,
            )

        assert resp.content == "fallback response"
        assert resp.model_name == "gpt-3.5-turbo"


class TestGatewayFallbackAllExhausted:
    def test_gateway_fallback_all_exhausted(self):
        bad1 = ModelProfile(
            model_profile_id="bad1",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="bad1-model",
            enabled=True,
            run_budget_usd=0.001,
            fallback_profiles=["bad2"],
        )
        bad2 = ModelProfile(
            model_profile_id="bad2",
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="bad2-model",
            enabled=True,
            run_budget_usd=0.001,
        )
        gw, reg = _make_gateway_with_mock([bad1, bad2])

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=MagicMock()),
            pytest.raises(ValueError, match="All profiles in fallback chain failed"),
        ):
            gw.call_model_with_fallback(
                "bad1",
                [{"role": "user", "content": "hello"}],
                estimated_cost=5.0,
                budget_remaining=1.0,
            )


class TestRunBudgetGuardTracksSpend:
    def test_run_budget_guard_tracks_spend(self):
        guard = RunBudgetGuard(run_budget_usd=100.0)
        guard.record_spend(10.0)
        guard.record_spend(25.0)
        guard.record_spend(5.0)
        assert guard.get_total_spend() == pytest.approx(40.0)


class TestRunBudgetGuardEnforcesCap:
    def test_run_budget_guard_enforces_cap(self):
        guard = RunBudgetGuard(run_budget_usd=5.0)
        guard.record_spend(6.0)
        result = guard.check_run_budget()
        assert result["allowed"] is False
        assert "run budget" in result["reason"].lower()


class TestRunBudgetGuardWallClockTimeout:
    def test_run_budget_guard_wall_clock_timeout(self):
        guard = RunBudgetGuard(run_timeout_seconds=-1.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is False
        assert "timeout" in result["reason"].lower()


class TestRunBudgetGuardCheckAllLimits:
    def test_run_budget_guard_check_all_limits(self):
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=3600.0,
            per_call_budget_usd=10.0,
        )
        guard.record_spend(5.0)
        result = guard.check_all_limits(estimated_cost=3.0)
        assert result["allowed"] is True
        assert result["total_spend"] == pytest.approx(5.0)
        assert result["remaining_budget"] == pytest.approx(95.0)
        assert result["elapsed_seconds"] > 0


class TestBudgetGuardDefaultUnlimited:
    def test_budget_guard_default_unlimited(self):
        guard = RunBudgetGuard()
        guard.record_spend(999_999.0)
        assert guard.check_run_budget()["allowed"] is True
        assert guard.check_wall_clock()["allowed"] is True
        assert guard.check_per_call(999_999.0)["allowed"] is True
        assert guard.check_all_limits(estimated_cost=999_999.0)["allowed"] is True


class TestModelProfilesYamlAllValid:
    def test_model_profiles_yaml_all_valid(self):
        profiles = _load_all_yaml_profiles()
        assert len(profiles) >= 5
        for p in profiles:
            assert p.model_profile_id
            assert p.provider
            assert p.provider_package
