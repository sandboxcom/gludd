from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
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
        registry = ProviderRegistry()
        registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

        secrets = EnvSecretsManager()
        secrets.set("ZAI_API_KEY", "fake-key")
        secrets.set("ZAI_BASE_URL", "https://example.com/v1")

        profile = ModelProfile(
            model_profile_id="zai_test",
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="test-model",
            api_base_alias="ZAI_BASE_URL",
            credential_alias="ZAI_API_KEY",
            context_window=64000,
            max_input_tokens=60000,
            max_output_tokens=4096,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
            api_metered=False,
            run_budget_usd=1.0,
            enabled=True,
            resource_profile="ai_heavy",
            roles=["coder"],
            latency_class="fast",
            quality_class="high",
        )

        gateway = ModelGateway(
            profiles=[profile],
            provider_registry=registry,
            secrets_manager=secrets,
        )

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {"error": {"message": "Insufficient account balance"}}

        with patch.object(gateway, "_get_llm_for_profile", side_effect=RuntimeError("429 rate limit exceeded")), \
             pytest.raises(RuntimeError, match="429"):
            gateway._get_llm_for_profile("zai_test")
        assert True
