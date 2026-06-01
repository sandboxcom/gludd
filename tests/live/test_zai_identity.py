"""Authenticated live test for Z.AI GLM model identity.

This test calls the real Z.AI/GLM API and expects a successful response.
Run with: make test-zai-identity

Requires:
- ZAI_API_KEY env var (make target extracts from opencode auth.json)
- The make target passes the key automatically — never commit the key.
"""

from __future__ import annotations

import os

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.env import EnvSecretsManager


def _get_zai_api_key() -> str | None:
    return os.environ.get("ZAI_API_KEY")


def _get_zai_base_url() -> str:
    return os.environ.get("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")


def _get_zai_model() -> str:
    return os.environ.get("ZAI_MODEL", "glm-5.1")


_SKIP_REASON = (
    "ZAI_API_KEY not set. "
    "Run: make test-zai-identity (extracts key from opencode auth.json)"
)


def _build_zai_gateway() -> ModelGateway:
    api_key = _get_zai_api_key()
    base_url = _get_zai_base_url()
    model_name = _get_zai_model()

    profile = ModelProfile(
        model_profile_id="zai_identity",
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

    return ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=secrets,
    )


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAIAuthenticatedIdentity:
    """Authenticated live test — expects a real model response."""

    def test_model_identifies_as_glm(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_identity",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Identify yourself. Tell me your exact model name, "
                        "version, model family, and provider. "
                        "Reply in a structured format."
                    ),
                },
            ],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )

        assert isinstance(response.content, str)
        assert len(response.content) > 20, (
            f"Response too short to be a proper identity reply: {response.content!r}"
        )

        content_lower = response.content.lower()
        assert any(
            token in content_lower
            for token in ["glm", "5.1", "zhipu", "bigmodel", "chatglm"]
        ), f"Model identity response did not contain GLM-identifying tokens: {response.content!r}"

        assert response.usage_metadata is not None, "Expected usage_metadata in response"
        assert response.usage_metadata.get("input_tokens", 0) > 0, (
            f"Expected non-zero input_tokens: {response.usage_metadata}"
        )
        assert response.usage_metadata.get("output_tokens", 0) > 0, (
            f"Expected non-zero output_tokens: {response.usage_metadata}"
        )

    def test_hello_world_response(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_identity",
            messages=[{"role": "user", "content": "Say exactly: HELLO general-ludd"}],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )

        assert isinstance(response.content, str)
        assert len(response.content) > 0
        content_lower = response.content.lower()
        assert "hello" in content_lower, (
            f"Expected 'hello' in response: {response.content!r}"
        )
        assert "general-ludd" in content_lower, (
            f"Expected 'general-ludd' in response: {response.content!r}"
        )
        assert response.usage_metadata.get("input_tokens", 0) > 0
        assert response.usage_metadata.get("output_tokens", 0) > 0
