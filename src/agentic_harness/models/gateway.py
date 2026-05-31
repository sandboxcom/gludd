"""Model gateway for LangChain provider management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agentic_harness.models.provider_registry import ProviderRegistry

logger = logging.getLogger(__name__)


class _SecretsResolver(Protocol):
    def resolve(self, alias_name: str) -> str | None: ...



class ModelProfile(BaseModel):
    model_profile_id: str
    role_names: list[str] = Field(default_factory=list)
    provider: str = "openai"
    provider_package: str = "langchain-openai"
    provider_class_hint: str = "ChatOpenAI"
    model_name: str = ""
    api_base_alias: str | None = None
    credential_alias: str | None = None
    context_window: int = 128000
    max_input_tokens: int = 120000
    max_output_tokens: int = 8000
    cost_per_input_token: float = 0.0
    cost_per_output_token: float = 0.0
    api_metered: bool = True
    run_budget_usd: float = 200.0
    enabled: bool = False
    resource_profile: str = "ai_heavy"
    roles: list[str] = Field(default_factory=list)
    latency_class: str | None = None
    quality_class: str | None = None
    fallback_profiles: list[str] = Field(default_factory=list)
    probe_enabled: bool = False


@dataclass
class ModelResponse:
    content: str
    usage_metadata: dict[str, Any] = field(default_factory=dict)
    cost_estimate: float = 0.0
    model_name: str = ""
    raw_response: Any = None


class ModelGateway:
    def __init__(
        self,
        profiles: list[ModelProfile] | None = None,
        provider_registry: ProviderRegistry | None = None,
        secrets_manager: _SecretsResolver | None = None,
    ) -> None:
        self._profiles: dict[str, ModelProfile] = {}
        if profiles:
            for p in profiles:
                self._profiles[p.model_profile_id] = p
        self._registry = provider_registry
        self._secrets = secrets_manager

    def get_profile(self, profile_id: str) -> ModelProfile | None:
        return self._profiles.get(profile_id)

    def is_available(self, profile_id: str) -> bool:
        profile = self._profiles.get(profile_id)
        return profile is not None and profile.enabled

    def check_budget(
        self, profile_id: str, estimated_cost: float, budget_remaining: float
    ) -> bool:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return False
        if estimated_cost > budget_remaining:
            return False
        return not (profile.api_metered and estimated_cost > profile.run_budget_usd)

    def list_profiles(self) -> list[ModelProfile]:
        return list(self._profiles.values())

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        **kwargs: Any,
    ) -> ModelResponse:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")

        if not self.check_budget(profile_id, estimated_cost, budget_remaining):
            raise ValueError(
                f"Call to '{profile_id}' rejected: over budget "
                f"(estimated={estimated_cost}, remaining={budget_remaining}, "
                f"profile_budget={profile.run_budget_usd})"
            )

        provider_name = profile.provider
        registry = self._registry

        if registry is not None and not registry.is_installed(provider_name):
            registry.install_provider(provider_name)
            raise ImportError(
                f"Provider '{provider_name}' is not installed. A dependency update todo has been created."
            )

        api_key: str | None = None
        if self._secrets and profile.credential_alias:
            api_key = self._secrets.resolve(profile.credential_alias)

        if registry is not None:
            provider_cls = registry.get_provider_class(provider_name)
        else:
            raise ValueError(f"No provider registry configured for '{profile_id}'")

        init_kwargs: dict[str, Any] = {"model": profile.model_name}
        if api_key:
            init_kwargs["api_key"] = api_key
        if profile.api_base_alias and self._secrets:
            base_url = self._secrets.resolve(profile.api_base_alias)
            if base_url:
                init_kwargs["base_url"] = base_url
        init_kwargs.update(kwargs)

        chat_model = provider_cls(**init_kwargs)

        logger.debug(
            "Calling model %s (profile=%s, provider=%s) with api_key=***REDACTED***",
            profile.model_name,
            profile_id,
            provider_name,
        )

        lc_messages = messages
        raw_response = chat_model.invoke(lc_messages)

        content = getattr(raw_response, "content", str(raw_response))
        usage = getattr(raw_response, "usage_metadata", {}) or {}

        cost = 0.0
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        if isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float)):
            cost = input_tokens * profile.cost_per_input_token + output_tokens * profile.cost_per_output_token

        logger.debug(
            "Model call complete: profile=%s, input_tokens=%s, output_tokens=%s, cost=%.6f",
            profile_id,
            input_tokens,
            output_tokens,
            cost,
        )

        return ModelResponse(
            content=str(content),
            usage_metadata=dict(usage),
            cost_estimate=cost,
            model_name=profile.model_name,
            raw_response=raw_response,
        )
