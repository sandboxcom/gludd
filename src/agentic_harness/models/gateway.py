"""Model gateway for LangChain provider management."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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


class ModelGateway:
    def __init__(self, profiles: list[ModelProfile] | None = None) -> None:
        self._profiles: dict[str, ModelProfile] = {}
        if profiles:
            for p in profiles:
                self._profiles[p.model_profile_id] = p

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
