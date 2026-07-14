"""AG.2 — Lifecycle Hook Expansion (Strands-Style Hooks).

Ten lifecycle hooks organized into five domains, per docs/LIFECYCLE_HOOK_EXPANSION.md.
"""

from general_ludd.ag2_lifecycle.hooks import (
    DenyError,
    HookHandler,
    LifecycleHookSystem,
    SubagentGuard,
    dispatch_chain,
)
from general_ludd.ag2_lifecycle.types import (
    AgentThinkAfterInput,
    AgentThinkAfterOutput,
    AgentThinkBeforeInput,
    AgentThinkBeforeOutput,
    HumanEscalationBeforeInput,
    HumanEscalationBeforeOutput,
    ModelCallAfterInput,
    ModelCallAfterOutput,
    ModelCallBeforeInput,
    ModelCallBeforeOutput,
    SessionCompactBeforeInput,
    SessionCompactBeforeOutput,
    TaskCompleteAfterInput,
    TaskCompleteAfterOutput,
    TaskDispatchBeforeInput,
    TaskDispatchBeforeOutput,
)

__all__ = (
    "AgentThinkAfterInput",
    "AgentThinkAfterOutput",
    "AgentThinkBeforeInput",
    "AgentThinkBeforeOutput",
    "DenyError",
    "HookHandler",
    "HumanEscalationBeforeInput",
    "HumanEscalationBeforeOutput",
    "LifecycleHookSystem",
    "ModelCallAfterInput",
    "ModelCallAfterOutput",
    "ModelCallBeforeInput",
    "ModelCallBeforeOutput",
    "SessionCompactBeforeInput",
    "SessionCompactBeforeOutput",
    "SubagentGuard",
    "TaskCompleteAfterInput",
    "TaskCompleteAfterOutput",
    "TaskDispatchBeforeInput",
    "TaskDispatchBeforeOutput",
    "dispatch_chain",
)
