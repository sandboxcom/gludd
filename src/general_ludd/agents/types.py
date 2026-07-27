"""Agent type definitions, configuration, and permission models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.agents.behavior import AgentBehavior
    from general_ludd.sandbox.enforcer import SandboxConfig


class AgentType(Enum):
    PRIMARY = "primary"
    SUBAGENT = "subagent"


@dataclass
class AgentPermission:
    can_edit: bool = False
    can_bash: bool = False
    can_read: bool = True
    can_dispatch_subagents: bool = False
    allowed_subagents: list[str] = field(default_factory=list)


@dataclass
class AgentConfig:
    name: str
    description: str
    type: AgentType
    model_profile: str | None = None
    prompt_profile: str | None = None
    max_steps: int = 10
    permissions: AgentPermission = field(default_factory=AgentPermission)
    max_concurrent: int = 1
    enabled: bool = True
    behavior: AgentBehavior | None = None
    bind_tools_on_dispatch: bool = True


@dataclass
class AgentTask:
    task_id: str
    agent_name: str
    description: str
    prompt: str
    parent_task_id: str | None = None
    invoker_name: str = ""
    project_id: str | None = None  # #51: enables pause-gate on dispatch
    depth: int = 0  # D11: subagent nesting depth for max-depth guard
    messages: list[object] = field(default_factory=list)  # S8: conversation history for pause/resume
    tools: list[dict[str, object]] | None = None
    estimated_effort: str = "medium"  # small, medium, large — for task splitter gating
    env: dict[str, str] = field(default_factory=dict)
    sandbox_config: SandboxConfig | None = None
