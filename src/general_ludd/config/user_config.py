from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from general_ludd.config.model_routing import ModelRoutingConfig


class ObservabilityConfig(BaseModel):
    otel_endpoint: str | None = None
    service_name: str = "general-ludd"


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Custom settings source that reads from a YAML file.

    Lower priority than env vars (env vars override YAML).
    """

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path | None) -> None:
        super().__init__(settings_cls)
        self._path = yaml_path
        self._data: dict[str, Any] = {}
        if yaml_path and Path(yaml_path).exists():
            try:
                with open(yaml_path) as f:
                    self._data = yaml.safe_load(f) or {}
            except Exception:
                self._data = {}

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        value = self._data.get(field_name)
        return value, field_name, value is not None

    def field_is_complex(self, field: Any) -> bool:
        return False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items()}


class UserConfig(BaseSettings):
    """User configuration with pydantic-settings (W4.4).

    Loading priority (highest → lowest):
    1. GLUDD_<FIELD> environment variables (e.g. GLUDD_AGENTS='{"timeout": 99}')
    2. YAML file (loaded via load_user_config)
    3. Field defaults

    Use ``load_user_config(path)`` for file-based loading (env vars override YAML).
    ``UserConfig()`` returns defaults only (respects env vars but no YAML source).
    Existing consumers calling ``UserConfig(**data)`` continue to work, though
    for proper env-override semantics use ``load_user_config()``.
    """

    model_config = SettingsConfigDict(
        env_prefix="GLUDD_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Internal: YAML source path used by customise_sources.
    _yaml_path: Path | None = None

    model_routing: ModelRoutingConfig | None = None
    model_profiles: dict[str, Any] = {}
    agents: dict[str, Any] = {}
    process_isolation: dict[str, Any] = {}
    budget: dict[str, Any] = {}
    database: dict[str, Any] = {}
    observability: ObservabilityConfig = ObservabilityConfig()
    queues: list[dict[str, Any]] = []
    self_improve: dict[str, Any] = {}

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> UserConfig:
        """Load UserConfig from a YAML file, with env vars taking precedence."""
        # Read YAML data.
        p = Path(yaml_path)
        if not p.exists():
            return cls()
        with open(p) as f:
            data = yaml.safe_load(f) or {}

        # Merge: env vars (GLUDD_*) override YAML. We do this by building
        # a UserConfig with YAML as defaults, then env vars override via
        # pydantic-settings normal mechanism.
        # Strategy: construct with yaml data as _defaults_, then let
        # pydantic-settings env source override.
        import os

        merged: dict[str, Any] = dict(data)
        for field_name in cls.model_fields:
            env_key = f"GLUDD_{field_name.upper()}"
            if env_key in os.environ:
                import json as _json
                raw = os.environ[env_key]
                try:
                    merged[field_name] = _json.loads(raw)
                except (_json.JSONDecodeError, ValueError):
                    merged[field_name] = raw
        return cls.model_validate(merged)


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
