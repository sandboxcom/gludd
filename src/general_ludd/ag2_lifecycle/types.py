"""AG.2 Lifecycle Hook Type Definitions.

Mirrors the TypeScript plugin-api.d.ts interface from the design doc at
docs/LIFECYCLE_HOOK_EXPANSION.md Section 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── shared helper types ────────────────────────────────────────────────────────


@dataclass
class Message:
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


@dataclass
class ToolDef:
    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_call_id: str
    output: str
    is_error: bool = False


@dataclass
class MemoryEntry:
    content: str
    source: str = ""
    timestamp: str = ""


# ── Model Call Domain ──────────────────────────────────────────────────────────


@dataclass
class ModelCallBudget:
    max_tokens: int = 0
    thinking_budget: int | None = None


@dataclass
class ModelCallBeforeInput:
    model: str = ""
    messages: list[Message] = field(default_factory=list)
    tools: list[ToolDef] = field(default_factory=list)
    system_prompt: str = ""
    budget: ModelCallBudget | None = None


@dataclass
class ModelCallBeforeOutput:
    model: str | None = None
    messages: list[Message] | None = None
    skip: bool = False
    tools: list[ToolDef] | None = None
    budget: ModelCallBudget | None = None


@dataclass
class ModelCallUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ModelCallResponse:
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    usage: ModelCallUsage = field(default_factory=ModelCallUsage)
    finish_reason: str = ""
    latency_ms: int = 0


@dataclass
class ModelCallRequest:
    model: str = ""
    messages: list[Message] = field(default_factory=list)
    tools: list[ToolDef] = field(default_factory=list)


@dataclass
class ModelCallAfterInput:
    model: str = ""
    messages: list[Message] = field(default_factory=list)
    request: ModelCallRequest = field(default_factory=ModelCallRequest)
    response: ModelCallResponse = field(default_factory=ModelCallResponse)


@dataclass
class ModelCallAfterOutput:
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


# ── Agent Thinking Domain ──────────────────────────────────────────────────────


@dataclass
class ThinkingContext:
    current_task: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    conversation_length: int = 0
    memory_entries: list[MemoryEntry] = field(default_factory=list)


@dataclass
class ThinkingConstraints:
    max_thinking_tokens: int | None = None
    reasoning_style: str = ""


@dataclass
class AgentThinkBeforeInput:
    context: ThinkingContext = field(default_factory=ThinkingContext)
    constraints: ThinkingConstraints = field(default_factory=ThinkingConstraints)


@dataclass
class AgentThinkBeforeContextOverride:
    tool_results: list[ToolResult] | None = None
    memory_entries: list[MemoryEntry] | None = None


@dataclass
class AgentThinkBeforeConstraintsOverride:
    max_thinking_tokens: int | None = None
    reasoning_style: str | None = None


@dataclass
class AgentThinkBeforeOutput:
    context: AgentThinkBeforeContextOverride | None = None
    constraints: AgentThinkBeforeConstraintsOverride | None = None


@dataclass
class ThinkingDecision:
    next_action: str = ""
    plan: str | None = None
    confidence: float | None = None


@dataclass
class ThinkingContextSummary:
    current_task: str = ""
    conversation_length: int = 0


@dataclass
class AgentThinkAfterInput:
    reasoning: str = ""
    decision: ThinkingDecision = field(default_factory=ThinkingDecision)
    context: ThinkingContextSummary = field(default_factory=ThinkingContextSummary)


@dataclass
class AgentThinkAfterDecisionOverride:
    next_action: str | None = None
    plan: str | None = None
    confidence: float | None = None


@dataclass
class AgentThinkAfterOutput:
    reasoning: str | None = None
    decision: AgentThinkAfterDecisionOverride | None = None


# ── Task Lifecycle Domain ──────────────────────────────────────────────────────


@dataclass
class TaskInfo:
    description: str = ""
    prompt: str = ""
    model: str = ""
    isolation: str = "none"


@dataclass
class TaskBudget:
    max_steps: int | None = None
    max_tokens: int | None = None
    timeout_ms: int | None = None


@dataclass
class DispatcherInfo:
    current_task_count: int = 0
    floor: int = 0
    ceiling: int = 0


@dataclass
class TaskDispatchBeforeInput:
    task: TaskInfo = field(default_factory=TaskInfo)
    budget: TaskBudget = field(default_factory=TaskBudget)
    dispatcher: DispatcherInfo = field(default_factory=DispatcherInfo)


@dataclass
class TaskDispatchBeforeOutput:
    prompt: str | None = None
    model: str | None = None
    budget: TaskBudget | None = None
    skip: bool = False


@dataclass
class TaskCompleteInfo:
    id: str = ""
    description: str = ""
    prompt: str = ""
    model: str = ""


@dataclass
class TaskResult:
    summary: str = ""
    tool_call_count: int = 0
    latency_ms: int = 0
    status: str = ""


@dataclass
class TaskEvidence:
    commit_hash: str | None = None
    test_count: int | None = None
    files_modified: list[str] | None = None


@dataclass
class TaskCompleteAfterInput:
    task: TaskCompleteInfo = field(default_factory=TaskCompleteInfo)
    result: TaskResult = field(default_factory=TaskResult)
    evidence: TaskEvidence | None = None


@dataclass
class TaskCompleteResultOverride:
    summary: str | None = None
    status: str | None = None


@dataclass
class TaskCompleteCodification:
    update_tasks_md: bool = False
    record_commit: bool = False
    update_session_md: bool = False


@dataclass
class TaskCompleteAfterOutput:
    result: TaskCompleteResultOverride | None = None
    codification: TaskCompleteCodification | None = None


# ── Human Interaction Domain ───────────────────────────────────────────────────


@dataclass
class EscalationInfo:
    type: str = ""
    message: str = ""
    options: list[str] | None = None


@dataclass
class EscalationAlternatives:
    can_solve_locally: bool = False
    has_defaults: bool = False
    fallback_plan: str | None = None


@dataclass
class EscalationContext:
    task_in_progress: str = ""
    pending_work_count: int = 0


@dataclass
class HumanEscalationBeforeInput:
    escalation: EscalationInfo = field(default_factory=EscalationInfo)
    alternatives: EscalationAlternatives = field(default_factory=EscalationAlternatives)
    context: EscalationContext = field(default_factory=EscalationContext)


@dataclass
class HumanEscalationBeforeOutput:
    skip: bool = False
    message: str | None = None
    fallback: str | None = None


# ── Session Management Domain ──────────────────────────────────────────────────


@dataclass
class CompactionInfo:
    trigger: str = ""
    current_tokens: int = 0
    target_tokens: int = 0
    messages_to_remove: int = 0


@dataclass
class CriticalState:
    task_id: str | None = None
    pending_work: list[str] = field(default_factory=list)
    last_commit_hash: str | None = None
    enforcement_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionCompactBeforeInput:
    compaction: CompactionInfo = field(default_factory=CompactionInfo)
    critical_state: CriticalState = field(default_factory=CriticalState)


@dataclass
class SessionCompactBeforeOutput:
    preserve: list[str] | None = None
    inject: str | None = None
    critical_state: dict[str, Any] | None = None
