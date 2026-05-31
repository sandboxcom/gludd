from __future__ import annotations

import os

import pytest
import yaml

from agentic_harness.models.gateway import ModelGateway, ModelProfile
from agentic_harness.models.provider_registry import ProviderRegistry
from agentic_harness.models.router import ModelRouter


class TestModelGatewayE2E:
    def test_openai_provider_installed_and_importable(self):
        registry = ProviderRegistry()
        registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
        assert registry.is_installed("openai")
        cls = registry.get_provider_class("openai")
        assert cls is not None
        assert cls.__name__ == "ChatOpenAI"

    def test_missing_provider_creates_dependency_todo(self):
        registry = ProviderRegistry()
        registry.register_provider("hypothetical_provider", "langchain_hypothetical", "ChatHypothetical")
        assert not registry.is_installed("hypothetical_provider")
        todo = registry.install_provider("hypothetical_provider")
        assert todo is not None
        assert "hypothetical" in todo.title.lower() or "install" in todo.title.lower()

    def test_gateway_budget_check_rejects_expensive_call(self):
        profile = ModelProfile(
            model_profile_id="expensive_model",
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-expensive",
            api_metered=True,
            run_budget_usd=10.0,
            enabled=True,
        )
        gw = ModelGateway(profiles=[profile])
        with pytest.raises(ValueError, match=r"budget|over budget"):
            gw.call_model(
                "expensive_model",
                messages=[{"role": "user", "content": "test"}],
                estimated_cost=15.0,
                budget_remaining=5.0,
            )

    def test_model_router_role_mapping(self):
        router = ModelRouter()
        router.add_role("return_review", "strong_model")
        router.add_role("implementation", "code_model")
        assert router.resolve_role("return_review") == "strong_model"
        assert router.resolve_role("implementation") == "code_model"
        assert router.resolve_role("unknown_role") is None

    def test_model_router_init_with_mapping(self):
        router = ModelRouter(role_mapping={"review": "profile_a", "code": "profile_b"})
        assert router.resolve_role("review") == "profile_a"
        assert router.resolve_role("code") == "profile_b"
        assert router.list_roles() == ["review", "code"]

    def test_example_model_configs_are_valid_yaml(self):
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config",
            "model_profiles",
        )
        configs = [f for f in os.listdir(config_dir) if f.endswith(".yml")]
        assert len(configs) >= 5, f"Expected >=5 model configs, found {len(configs)}"
        for cfg_file in configs:
            with open(os.path.join(config_dir, cfg_file)) as f:
                data = yaml.safe_load(f)
            assert "model_profile_id" in data
            assert "provider" in data
            assert "provider_package" in data

    def test_local_model_profile_ignored_unless_enabled(self):
        disabled = ModelProfile(
            model_profile_id="local_llm",
            provider="openai_compatible",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="local-model",
            enabled=False,
        )
        gw = ModelGateway(profiles=[disabled])
        assert not gw.is_available("local_llm")

        enabled = ModelProfile(
            model_profile_id="local_llm_enabled",
            provider="openai_compatible",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="local-model",
            enabled=True,
        )
        gw2 = ModelGateway(profiles=[enabled])
        assert gw2.is_available("local_llm_enabled")
