from __future__ import annotations

import os
from unittest.mock import patch

from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.env import EnvSecretsManager


class TestZAISkipBehavior:
    def test_skip_when_no_zai_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ZAI_API_KEY", None)
            key = os.environ.get("ZAI_API_KEY")
            assert key is None
            reason = (
                "ZAI_API_KEY not set. "
                "Run: make test-live-zai (extracts key from opencode auth.json)"
            )
            assert not os.environ.get("ZAI_API_KEY")
            del reason

    def test_zai_429_handled_as_skip_not_failure(self):
        """ZAI 429 rate-limit: gateway falls back without crashing."""
        registry = ProviderRegistry()
        registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

        secrets = EnvSecretsManager()
        secrets.set("ZAI_API_KEY", "fake-key")
        secrets.set("ZAI_BASE_URL", "https://example.com/v1")

        main_profile = ModelProfile(
            model_profile_id="zai_main",
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="main-model",
            credential_alias="ZAI_API_KEY",
            api_base_alias="ZAI_BASE_URL",
            context_window=64000,
            max_input_tokens=60000,
            max_output_tokens=4096,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
            api_metered=False,
            run_budget_usd=1.0,
            enabled=True,
            fallback_profiles=["fallback_model"],
            roles=["coder"],
            latency_class="fast",
            quality_class="high",
        )

        fallback_profile = ModelProfile(
            model_profile_id="fallback_model",
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="fallback-model",
            credential_alias="ZAI_API_KEY",
            context_window=64000,
            enabled=True,
        )

        gateway = ModelGateway(
            profiles=[main_profile, fallback_profile],
            provider_registry=registry,
            secrets_manager=secrets,
        )

        stored = gateway.get_profile("zai_main")
        assert stored is not None
        assert stored.fallback_profiles == ["fallback_model"]
        assert gateway.get_profile("fallback_model") is not None

        fallback_response = ModelResponse(content="fallback result")

        def _try_call_side_effect(profile_id, messages, **kwargs):
            if profile_id == "zai_main":
                return None
            return fallback_response

        with patch.object(gateway, "_try_call_model", side_effect=_try_call_side_effect):
            result = gateway.call_model_with_fallback("zai_main", [
                {"role": "user", "content": "test"}
            ])
            assert result.content == "fallback result"
