from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from general_ludd.config.model_routing import ModelRoutingConfig


class UserConfig(BaseModel):
    model_routing: ModelRoutingConfig | None = None
    model_profiles: dict[str, Any] = {}
    agents: dict[str, Any] = {}
    process_isolation: dict[str, Any] = {}
    budget: dict[str, Any] = {}
    database: dict[str, Any] = {}


class AgentConfig(BaseModel):
    model_routing: ModelRoutingConfig | None = None
    active_model_profile: str | None = None
    preferred_agents: dict[str, Any] = {}
    task_preferences: dict[str, Any] = {}
    session_notes: str = ""


class ConfigLayer(BaseModel):
    user: UserConfig = UserConfig()
    agent: AgentConfig = AgentConfig()
    defaults: dict[str, Any] = {}

    def resolve(self, key: str) -> Any:
        user_val = getattr(self.user, key, None)
        if user_val is not None:
            if isinstance(user_val, dict) and user_val:
                return user_val
            if not isinstance(user_val, dict):
                return user_val
        agent_val = getattr(self.agent, key, None)
        if agent_val is not None:
            if isinstance(agent_val, dict) and agent_val:
                return agent_val
            if not isinstance(agent_val, dict):
                return agent_val
        return self.defaults.get(key)

    def resolve_model_routing(self) -> ModelRoutingConfig:
        if self.user.model_routing is not None:
            return self.user.model_routing
        if self.agent.model_routing is not None:
            return self.agent.model_routing
        return ModelRoutingConfig()
