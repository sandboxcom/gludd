"""Cloud deploy strategy — gateway factory for Azure GPU inference endpoints."""

from __future__ import annotations

import os

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.env import EnvSecretsManager


def _openai_base_url(endpoint: str) -> str:
    base_url = endpoint.rstrip("/")
    return base_url if base_url.endswith("/v1") else f"{base_url}/v1"


def build_azure_gateway(base_url: str | None = None) -> ModelGateway | None:
    """Construct a real ModelGateway backed by Azure GPU if available.

    Returns None when no Azure endpoint is configured.  Never returns a fake.
    """
    endpoint = (base_url or os.environ.get("AZURE_BASE_URL", "")).strip()
    if not endpoint:
        return None

    model = os.environ.get("AZURE_MODEL", "qwen2.5-coder-7b")
    profiles = [
        ModelProfile(
            model_profile_id=profile_id,
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name=model,
            api_base_alias="AZURE_BASE_URL",
            credential_alias="AZURE_API_KEY",
            enabled=True,
            api_metered=False,
        )
        for profile_id in ("default", "azure_self_improve")
    ]
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("AZURE_BASE_URL", _openai_base_url(endpoint))
    api_key = os.environ.get("AZURE_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
    if api_key:
        secrets.set("AZURE_API_KEY", api_key)
    return ModelGateway(
        profiles=profiles,
        provider_registry=registry,
        secrets_manager=secrets,
    )
