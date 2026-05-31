"""Agent registry with permission checking and default built-in agents."""

from __future__ import annotations

import fnmatch

from agentic_harness.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    default_primary_behavior,
    default_subagent_behavior,
)
from agentic_harness.agents.types import AgentConfig, AgentPermission, AgentType


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentConfig] = {}
        self._renderer = BehaviorRenderer()

    def register(self, config: AgentConfig) -> None:
        self._agents[config.name] = config

    def get(self, name: str) -> AgentConfig | None:
        return self._agents.get(name)

    def list_agents(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def list_subagents(self) -> list[AgentConfig]:
        return [a for a in self._agents.values() if a.type == AgentType.SUBAGENT]

    def can_invoke(self, invoker: str, target: str) -> bool:
        invoker_config = self._agents.get(invoker)
        if invoker_config is None:
            return False
        if not invoker_config.permissions.can_dispatch_subagents:
            return False
        target_config = self._agents.get(target)
        if target_config is None:
            return False
        return any(
            fnmatch.fnmatch(target, pattern)
            for pattern in invoker_config.permissions.allowed_subagents
        )

    def get_behavior(self, name: str) -> AgentBehavior:
        config = self._agents.get(name)
        if config is not None and config.behavior is not None:
            return config.behavior
        if config is not None and config.type == AgentType.PRIMARY:
            return default_primary_behavior()
        return default_subagent_behavior()

    def render_behavior_prompt(self, name: str, task: str) -> str | None:
        config = self._agents.get(name)
        if config is None:
            return None
        behavior = self.get_behavior(name)
        return self._renderer.render_as_prompt(behavior, agent_name=name, task=task)


def default_registry() -> AgentRegistry:
    registry = AgentRegistry()
    primary_behavior = default_primary_behavior()
    subagent_behavior = default_subagent_behavior()

    registry.register(AgentConfig(
        name="build",
        description="Primary build agent with all tool permissions",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_edit=True,
            can_bash=True,
            can_read=True,
            can_dispatch_subagents=True,
            allowed_subagents=["*"],
        ),
        max_concurrent=1,
        behavior=primary_behavior,
    ))

    registry.register(AgentConfig(
        name="plan",
        description="Primary planning agent with read-only access",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=True,
            allowed_subagents=["explore"],
        ),
        max_concurrent=1,
        behavior=primary_behavior,
    ))

    registry.register(AgentConfig(
        name="explore",
        description="Subagent for codebase exploration, read-only",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        ),
        max_concurrent=5,
        behavior=subagent_behavior,
    ))

    registry.register(AgentConfig(
        name="general",
        description="General-purpose subagent with all tool permissions",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(
            can_edit=True,
            can_bash=True,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        ),
        max_concurrent=3,
        behavior=subagent_behavior,
    ))

    return registry
