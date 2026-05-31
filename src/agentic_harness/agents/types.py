"""Agent type definitions, configuration, and permission models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_harness.agents.behavior import AgentBehavior


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
