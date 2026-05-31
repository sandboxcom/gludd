from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agentic_harness.config.user_config import AgentConfig, ConfigLayer, UserConfig


def load_user_config(path: Path | None = None) -> UserConfig:
    if path is None:
        path = Path.home() / ".config" / "hottentot" / "user.yml"
    p = Path(path)
    if not p.exists():
        return UserConfig()
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return UserConfig(**data)


def load_agent_config(path: Path | None = None) -> AgentConfig:
    if path is None:
        path = Path(".hottentot") / "agent_config.yml"
    p = Path(path)
    if not p.exists():
        return AgentConfig()
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return AgentConfig(**data)


def build_config_layer(
    user_path: Path | None = None,
    agent_path: Path | None = None,
    defaults: dict[str, Any] | None = None,
) -> ConfigLayer:
    user = load_user_config(user_path)
    agent = load_agent_config(agent_path)
    return ConfigLayer(user=user, agent=agent, defaults=defaults or {})


def save_agent_config(config: AgentConfig, path: Path | None = None) -> None:
    if path is None:
        path = Path(".hottentot") / "agent_config.yml"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False)
