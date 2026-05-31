"""Live integration test for Z.AI model gateway.

This test calls the real Z.AI/GLM API using the opencode environment credentials.
Run with: make test-live-zai

Requires:
- ~/.local/share/opencode/auth.json with zai-coding-plan key
- The make target extracts the key automatically
"""

from __future__ import annotations

import json
import os

import pytest

from agentic_harness.models.gateway import ModelGateway, ModelProfile
from agentic_harness.models.provider_registry import ProviderRegistry
from agentic_harness.secrets.env import EnvSecretsManager


def _get_zai_api_key() -> str | None:
    return os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _get_zai_base_url() -> str:
    return os.environ.get("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")


def _get_zai_model() -> str:
    return os.environ.get("ZAI_MODEL", "glm-5.1")


def _build_zai_gateway() -> ModelGateway:
    api_key = _get_zai_api_key()
    base_url = _get_zai_base_url()
    model_name = _get_zai_model()

    profile = ModelProfile(
        model_profile_id="zai_live",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=model_name,
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
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    if api_key:
        secrets.set("ZAI_API_KEY", api_key)
    if base_url:
        secrets.set("ZAI_BASE_URL", base_url)

    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=secrets,  # type: ignore[arg-type]
    )
    return gateway


_SKIP_REASON = (
    "ZAI_API_KEY not set. "
    "Run: make test-live-zai (extracts key from opencode auth.json)"
)


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAIConfigAndConnectivity:
    def test_zai_gateway_profile_exists(self):
        gw = _build_zai_gateway()
        profile = gw.get_profile("zai_live")
        assert profile is not None
        assert profile.enabled is True
        assert profile.provider == "openai"

    def test_zai_provider_is_installed(self):
        registry = ProviderRegistry()
        registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
        assert registry.is_installed("openai")

    def test_zai_config_file_is_valid_yaml(self):
        import yaml

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config",
            "model_profiles",
            "zai_example.yml",
        )
        if not os.path.exists(config_path):
            pytest.skip("zai_example.yml not found")
        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert data["provider"] == "zai"
        assert data["model_profile_id"] == "zai_coder"

    def test_zai_profile_matches_runtime_config(self):
        gw = _build_zai_gateway()
        profile = gw.get_profile("zai_live")
        assert profile is not None
        assert profile.provider_package == "langchain_openai"
        assert profile.provider_class_hint == "ChatOpenAI"
        assert profile.api_metered is False

    def test_zai_secrets_resolve(self):
        secrets = EnvSecretsManager()
        secrets.set("ZAI_API_KEY", "test-key")
        secrets.set("ZAI_BASE_URL", "https://example.com/v1")
        assert secrets.resolve("ZAI_API_KEY") == "test-key"
        assert secrets.resolve("ZAI_BASE_URL") == "https://example.com/v1"


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAILiveCompletions:
    """Live model completion tests. May xfail on rate-limit or balance errors."""

    @pytest.mark.xfail(
        reason="Z.AI direct API may hit rate limit or balance exhaustion (429)",
        raises=Exception,
    )
    def test_zai_simple_completion(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_live",
            messages=[{"role": "user", "content": "Say exactly: HELLO hottentot"}],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )
        assert isinstance(response.content, str)
        assert len(response.content) > 0
        assert response.model_name == _get_zai_model()

    @pytest.mark.xfail(
        reason="Z.AI direct API may hit rate limit or balance exhaustion (429)",
        raises=Exception,
    )
    def test_zai_structured_json_response(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_live",
            messages=[
                {
                    "role": "user",
                    "content": (
                        'Return ONLY valid JSON with keys "status" and "count". '
                        'Example: {"status": "ok", "count": 1}'
                    ),
                },
            ],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )
        parsed = json.loads(response.content.strip())
        assert "status" in parsed
        assert "count" in parsed

    @pytest.mark.xfail(
        reason="Z.AI direct API may hit rate limit or balance exhaustion (429)",
        raises=Exception,
    )
    def test_zai_code_generation(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_live",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a Python function that adds two numbers. "
                        "Return ONLY the function, no explanation."
                    ),
                },
            ],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )
        assert "def " in response.content
        assert "return" in response.content

    @pytest.mark.xfail(
        reason="Z.AI direct API may hit rate limit or balance exhaustion (429)",
        raises=Exception,
    )
    def test_zai_usage_metadata_returned(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_live",
            messages=[{"role": "user", "content": "Say OK"}],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )
        assert response.usage_metadata is not None
        assert isinstance(response.usage_metadata, dict)
