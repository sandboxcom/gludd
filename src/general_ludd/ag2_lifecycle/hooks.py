"""AG.2 Lifecycle Hook System — Registration, dispatch, and chain execution.

Implements the hook dispatching API per docs/LIFECYCLE_HOOK_EXPANSION.md §4.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

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
from general_ludd.security.state import (
    SecureStateError,
    project_state,
    trusted_owned_file,
)

logger = logging.getLogger(__name__)

_LEGACY_SIGNAL_ROOT = Path(os.sep) / "tmp"


# ── Deny Error ─────────────────────────────────────────────────────────────────

class DenyError(Exception):
    """Throw with permissionDecision='deny' to block an operation.

    The framework catches this and refuses the operation. Any other exception
    is treated as a plugin crash (fail-open — the operation proceeds).
    """

    def __init__(
        self,
        reason: str,
        suggested_action: str = "",
        permission_decision: str = "deny",
    ) -> None:
        super().__init__(reason)
        self.permission_decision = permission_decision
        self.reason = reason
        self.suggested_action = suggested_action


# ── Hook Handler Protocol ──────────────────────────────────────────────────────

class HookHandler(Protocol):
    """(input, output) -> Awaitable[None] protocol for lifecycle hook handlers.

    Handlers receive an immutable input and a mutable output object. To block:
        raise DenyError("reason", suggested_action="...")

    Any other exception = fail-open (operation proceeds).
    """
    async def __call__(self, input: Any, output: Any) -> None: ...


# ── Subagent Guard ─────────────────────────────────────────────────────────────

class SubagentGuard:
    """Determines whether the current process is a subagent.

    Mirrors the TypeScript _isSubagent() guard from all 13 enforcement plugins.
    Subagents skip ALL enforcement hooks — the orchestrator enforces policy.
    """

    @staticmethod
    def is_subagent() -> bool:
        if os.environ.get("OPENCODE_SUBAGENT") == "1":
            return True
        try:
            state = project_state(create=False)
            namespaced = state.path("subagents", f"process-{os.getpid()}.json")
            legacy = _LEGACY_SIGNAL_ROOT / f"gludd-subagent-{os.getpid()}.json"
            return any(trusted_owned_file(path) for path in (namespaced, legacy))
        except (OSError, SecureStateError):
            return False


# ── Lifecycle Hook System ──────────────────────────────────────────────────────

class LifecycleHookSystem:
    """Registry + dispatcher for the 8 AG.2 lifecycle hooks.

    Hooks are registered per event name. Multiple plugins may register for the
    same hook; they execute in registration order (chain). Each plugin sees the
    output as modified by previous plugins in the chain. A plugin that throws
    DenyError short-circuits the chain.

    All hook invocations are wrapped in try/catch — unexpected exceptions
    (TypeError, ReferenceError) do NOT block the operation (fail-open).

    Subagents skip all enforcement hooks (SubagentGuard).
    """

    _HOOK_NAMES: tuple[str, ...] = (
        "model.call.before",
        "model.call.after",
        "agent.think.before",
        "agent.think.after",
        "task.dispatch.before",
        "task.complete.after",
        "human.escalation.before",
        "session.compact.before",
    )

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = {
            name: [] for name in self._HOOK_NAMES
        }
        self._hook_depth: dict[str, int] = {name: 0 for name in self._HOOK_NAMES}
        self._lock = threading.Lock()
        self._max_depth = 2

    # ── registration ───────────────────────────────────────────────────────

    def register(self, event_name: str, handler: HookHandler) -> None:
        if event_name not in self._HOOK_NAMES:
            raise ValueError(
                f"Unknown hook event: {event_name}. "
                f"Known hooks: {list(self._HOOK_NAMES)}"
            )
        with self._lock:
            self._handlers[event_name].append(handler)

    def unregister(self, event_name: str, handler: HookHandler) -> None:
        with self._lock:
            if event_name in self._handlers:
                self._handlers[event_name] = [
                    h for h in self._handlers[event_name] if h is not handler
                ]

    # ── dispatch ───────────────────────────────────────────────────────────

    async def dispatch(
        self,
        event_name: str,
        input: Any,
        output: Any,
    ) -> DispatchResult:
        """Run every handler registered for *event_name* in registration order.

        Returns a DispatchResult with the outcome. Handlers that throw DenyError
        short-circuit the chain. Unexpected exceptions are logged and the
        chain continues (fail-open).
        """
        if SubagentGuard.is_subagent():
            return DispatchResult(
                allowed=True,
                skipped=True,
                chain_results=[],
            )

        if event_name not in self._HOOK_NAMES:
            return DispatchResult(
                allowed=True,
                skipped=False,
                chain_results=[],
            )

        with self._lock:
            self._hook_depth[event_name] += 1
            depth = self._hook_depth[event_name]
            handlers = list(self._handlers[event_name])

        if depth > self._max_depth:
            # Infinite recursion guard
            with self._lock:
                self._hook_depth[event_name] -= 1
            logger.warning(
                "Hook %s reached depth %d (max %d) — possible recursion, denying",
                event_name, depth, self._max_depth,
            )
            return DispatchResult(allowed=False, skipped=False, chain_results=[])

        try:
            return await self._run_chain(event_name, input, output, handlers)
        finally:
            with self._lock:
                self._hook_depth[event_name] -= 1

    async def _run_chain(
        self,
        event_name: str,
        input: Any,
        output: Any,
        handlers: list[HookHandler],
    ) -> DispatchResult:
        chain_results: list[ChainResult] = []

        for i, handler in enumerate(handlers):
            try:
                await handler(input, output)
                chain_results.append(ChainResult(index=i, outcome="allow"))
            except DenyError as exc:
                chain_results.append(
                    ChainResult(
                        index=i,
                        outcome="deny",
                        reason=exc.reason,
                        suggested_action=exc.suggested_action,
                    )
                )
                return DispatchResult(
                    allowed=False,
                    skipped=False,
                    chain_results=chain_results,
                )
            except Exception as exc:
                logger.error(
                    "Hook %s handler %d crashed (fail-open): %s",
                    event_name, i, exc, exc_info=True,
                )
                chain_results.append(
                    ChainResult(
                        index=i,
                        outcome="fail_open",
                        reason=str(exc),
                    )
                )

        return DispatchResult(
            allowed=True,
            skipped=False,
            chain_results=chain_results,
        )

    # ── convenience dispatch wrappers ───────────────────────────────────────

    async def dispatch_model_call_before(
        self, input: ModelCallBeforeInput
    ) -> tuple[ModelCallBeforeOutput, DispatchResult]:
        output = ModelCallBeforeOutput()
        result = await self.dispatch("model.call.before", input, output)
        return output, result

    async def dispatch_model_call_after(
        self, input: ModelCallAfterInput
    ) -> tuple[ModelCallAfterOutput, DispatchResult]:
        output = ModelCallAfterOutput()
        result = await self.dispatch("model.call.after", input, output)
        return output, result

    async def dispatch_agent_think_before(
        self, input: AgentThinkBeforeInput
    ) -> tuple[AgentThinkBeforeOutput, DispatchResult]:
        output = AgentThinkBeforeOutput()
        result = await self.dispatch("agent.think.before", input, output)
        return output, result

    async def dispatch_agent_think_after(
        self, input: AgentThinkAfterInput
    ) -> tuple[AgentThinkAfterOutput, DispatchResult]:
        output = AgentThinkAfterOutput()
        result = await self.dispatch("agent.think.after", input, output)
        return output, result

    async def dispatch_task_dispatch_before(
        self, input: TaskDispatchBeforeInput
    ) -> tuple[TaskDispatchBeforeOutput, DispatchResult]:
        output = TaskDispatchBeforeOutput()
        result = await self.dispatch("task.dispatch.before", input, output)
        return output, result

    async def dispatch_task_complete_after(
        self, input: TaskCompleteAfterInput
    ) -> tuple[TaskCompleteAfterOutput, DispatchResult]:
        output = TaskCompleteAfterOutput()
        result = await self.dispatch("task.complete.after", input, output)
        return output, result

    async def dispatch_human_escalation_before(
        self, input: HumanEscalationBeforeInput
    ) -> tuple[HumanEscalationBeforeOutput, DispatchResult]:
        output = HumanEscalationBeforeOutput()
        result = await self.dispatch("human.escalation.before", input, output)
        return output, result

    async def dispatch_session_compact_before(
        self, input: SessionCompactBeforeInput
    ) -> tuple[SessionCompactBeforeOutput, DispatchResult]:
        output = SessionCompactBeforeOutput()
        result = await self.dispatch("session.compact.before", input, output)
        return output, result

    # ── introspection ───────────────────────────────────────────────────────

    def handler_count(self, event_name: str) -> int:
        with self._lock:
            return len(self._handlers.get(event_name, []))

    def list_hooks(self) -> dict[str, int]:
        with self._lock:
            return {name: len(handlers) for name, handlers in self._handlers.items()}


# ── Dispatch result types ──────────────────────────────────────────────────────


@dataclass
class ChainResult:
    """Outcome of a single handler in the chain."""
    index: int
    outcome: str
    reason: str = ""
    suggested_action: str = ""


@dataclass
class DispatchResult:
    """Aggregate outcome of a hook dispatch."""
    allowed: bool
    skipped: bool
    chain_results: list[ChainResult] = field(default_factory=list)


# ── Standalone chain dispatcher ────────────────────────────────────────────────

async def dispatch_chain(
    event_name: str,
    input: Any,
    output: Any,
    handlers: list[HookHandler],
) -> DispatchResult:
    """Run a list of handlers as a chain for *event_name*.

    Used without a LifecycleHookSystem instance. Same semantics: registration
    order, DenyError short-circuits, fail-open on unexpected exceptions.
    """
    if SubagentGuard.is_subagent():
        return DispatchResult(allowed=True, skipped=True, chain_results=[])

    chain_results: list[ChainResult] = []

    for i, handler in enumerate(handlers):
        try:
            await handler(input, output)
            chain_results.append(ChainResult(index=i, outcome="allow"))
        except DenyError as exc:
            chain_results.append(
                ChainResult(
                    index=i, outcome="deny",
                    reason=exc.reason,
                    suggested_action=exc.suggested_action,
                )
            )
            return DispatchResult(
                allowed=False, skipped=False, chain_results=chain_results,
            )
        except Exception as exc:
            logger.error(
                "Hook %s handler %d crashed (fail-open): %s",
                event_name, i, exc, exc_info=True,
            )
            chain_results.append(
                ChainResult(index=i, outcome="fail_open", reason=str(exc))
            )

    return DispatchResult(allowed=True, skipped=False, chain_results=chain_results)
