"""Multitasking agent system — registry, dispatcher, behavior, context, research."""

__all__ = (
    "AgentBehavior",
    "AgentConfig",
    "AgentDispatcher",
    "AgentPermission",
    "AgentRegistry",
    "AgentTask",
    "AgentTaskResult",
    "AgentToolAdapter",
    "AgentType",
    "BehaviorRenderer",
    "Citation",
    "ContextCompactor",
    "ContextMessage",
    "GuardrailConfig",
    "ResearchFinding",
    "ResearchQuery",
    "ResearchReport",
    "ResearcherAgent",
    "SkillContext",
    "SkillContextProvider",
    "TokenWindowManager",
    "default_primary_behavior",
    "default_registry",
    "default_subagent_behavior",
)

from general_ludd.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    GuardrailConfig,
    default_primary_behavior,
    default_subagent_behavior,
)
from general_ludd.agents.context import ContextCompactor, ContextMessage
from general_ludd.agents.dispatcher import AgentDispatcher, AgentTaskResult
from general_ludd.agents.registry import AgentRegistry, default_registry
from general_ludd.agents.researcher import (
    Citation,
    ResearcherAgent,
    ResearchFinding,
    ResearchQuery,
    ResearchReport,
)
from general_ludd.agents.skill_context import SkillContext, SkillContextProvider
from general_ludd.agents.token_window import TokenWindowManager
from general_ludd.agents.tool_adapter import AgentToolAdapter
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType
