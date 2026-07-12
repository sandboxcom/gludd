"""Agent dispatcher for concurrent subagent task execution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentTask
from general_ludd.observability.timing import (
    DurationTracker,
    StallWatchdog,
    default_tracker,
)
from general_ludd.replay.recorder import RunRecorder

if TYPE_CHECKING:
    from general_ludd.config.user_config import OrchestrationGuardConfig

logger = logging.getLogger(__name__)

# Default wall-clock budget for a whole dispatch_many batch.
DEFAULT_DISPATCH_TIMEOUT = 1800.0  # 30 minutes


@dataclass
class AgentTaskResult:
    task_id: str
    agent_name: str
    status: str
    output: str
    artifacts: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


ExecutorFn = Callable[[AgentTask], Coroutine[None, None, str]]


async def _noop_executor(task: AgentTask) -> str:
    return ""


class AgentDispatcher:
    def __init__(
        self,
        registry: AgentRegistry,
        executor: ExecutorFn | None = None,
        *,
        tracker: DurationTracker | None = None,
        watchdog: StallWatchdog | None = None,
        pause_controller: object | None = None,
        hibernation: object | None = None,
        run_recorder: RunRecorder | None = None,
        mcp_tool_registry: object | None = None,
        orchestration_guard: OrchestrationGuardConfig | None = None,
    ) -> None:
        self._registry = registry
        self._executor: ExecutorFn = executor or _noop_executor
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._active_count = 0
        self._active_tasks: dict[str, AgentTask] = {}
        self._lock = asyncio.Lock()
        self._pause_controller = pause_controller
        self._hibernation = hibernation
        self._run_recorder = run_recorder
        self._tracker = tracker or default_tracker()
        self._watchdog = watchdog
        self._mcp_tool_registry = mcp_tool_registry
        # D11 orchestration guards
        self._orchestration_guard = orchestration_guard
        self._rate_limiter_timestamps: list[float] = []
        self._task_dispatch_counts: dict[str, int] = {}
        self._spiral_lock = asyncio.Lock()
        model_call_limit = (
            orchestration_guard.max_concurrent_model_calls
            if orchestration_guard is not None
            else 10
        )
        self._model_call_semaphore = asyncio.Semaphore(
            max(model_call_limit, 1)
        )

    @property
    def active_count(self) -> int:
        return self._active_count

    async def get_active_tasks_for_project(
        self, project_id: str
    ) -> list[AgentTask]:
        async with self._lock:
            return [
                t
                for t in self._active_tasks.values()
                if t.project_id == project_id
            ]

    def _get_semaphore(self, agent_name: str) -> asyncio.Semaphore:
        if agent_name not in self._semaphores:
            config = self._registry.get(agent_name)
            limit = config.max_concurrent if config else 1
            self._semaphores.setdefault(agent_name, asyncio.Semaphore(limit))
        return self._semaphores[agent_name]

    def _record_if_wired(self, run_id: str, event: dict[str, object]) -> None:
        if self._run_recorder is not None:
            with contextlib.suppress(Exception):
                self._run_recorder.record(run_id, event)

    # ------------------------------------------------------------------
    # D11 orchestration guards
    # ------------------------------------------------------------------

    def _check_nesting_depth(self, task: AgentTask) -> AgentTaskResult | None:
        if self._orchestration_guard is None:
            return None
        limit = self._orchestration_guard.max_nesting_depth
        if limit <= 0:
            return None
        if task.depth > limit:
            return AgentTaskResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status="failed",
                output=(
                    f"Max nesting depth exceeded: depth {task.depth} > limit {limit}. "
                    f"Subagents may not spawn deeper than {limit} levels."
                ),
            )
        return None

    def _check_capability_escalation(
        self, task: AgentTask, invoker: str
    ) -> AgentTaskResult | None:
        if self._orchestration_guard is None:
            return None
        if not self._orchestration_guard.enforce_capability_escalation:
            return None
        parent_cfg = self._registry.get(invoker)
        if parent_cfg is None:
            return None
        child_cfg = self._registry.get(task.agent_name)
        if child_cfg is None:
            return None

        parent = parent_cfg.permissions
        child = child_cfg.permissions

        violations: list[str] = []
        if child.can_edit and not parent.can_edit:
            violations.append("can_edit")
        if child.can_bash and not parent.can_bash:
            violations.append("can_bash")
        if child.can_read and not parent.can_read:
            violations.append("can_read")
        if child.can_dispatch_subagents and not parent.can_dispatch_subagents:
            violations.append("can_dispatch_subagents")
        parent_allows_all = "*" in parent.allowed_subagents
        for allowed in child.allowed_subagents:
            if not parent_allows_all and allowed not in parent.allowed_subagents:
                violations.append(f"allowed_subagent:{allowed}")

        if violations:
            return AgentTaskResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status="failed",
                output=(
                    f"Capability escalation denied: child '{task.agent_name}' "
                    f"has capabilities not held by parent '{invoker}': "
                    f"{', '.join(violations)}"
                ),
            )
        return None

    async def _check_rate_limiter(
        self, task: AgentTask
    ) -> AgentTaskResult | None:
        if self._orchestration_guard is None:
            return None
        max_per_window = self._orchestration_guard.max_dispatches_per_window
        if max_per_window <= 0:
            return None
        window_s = self._orchestration_guard.dispatch_rate_window_s
        now = time.monotonic()
        cutoff = now - window_s
        self._rate_limiter_timestamps.append(now)
        self._rate_limiter_timestamps[:] = [
            ts for ts in self._rate_limiter_timestamps if ts > cutoff
        ]
        if len(self._rate_limiter_timestamps) > max_per_window:
            return AgentTaskResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status="failed",
                output=(
                    f"Dispatch rate limited: {len(self._rate_limiter_timestamps)} "
                    f"dispatches in {window_s:.0f}s window exceeds limit of "
                    f"{max_per_window}"
                ),
            )
        return None

    async def _check_spiral(
        self, task: AgentTask
    ) -> AgentTaskResult | None:
        if self._orchestration_guard is None:
            return None
        max_redispatch = self._orchestration_guard.max_redispatch_count
        if max_redispatch <= 0:
            return None
        async with self._spiral_lock:
            count = self._task_dispatch_counts.get(task.task_id, 0) + 1
            self._task_dispatch_counts[task.task_id] = count
        if count > max_redispatch:
            return AgentTaskResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status="failed",
                output=(
                    f"Spiral detected: task '{task.task_id}' has been "
                    f"re-dispatched {count} times (limit: {max_redispatch}). "
                    f"Same-task redispatch loop cutoff."
                ),
            )
        return None

    # ------------------------------------------------------------------
    # dispatch_one
    # ------------------------------------------------------------------

    async def dispatch_one(self, task: AgentTask) -> AgentTaskResult:
        config = self._registry.get(task.agent_name)
        if config is None:
            self._record_if_wired(task.task_id, {
                "type": "task_failed",
                "timestamp": datetime.now(UTC).isoformat(),
                "agent_name": task.agent_name,
                "reason": f"Agent '{task.agent_name}' not found in registry",
            })
            return AgentTaskResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status="failed",
                output=f"Agent '{task.agent_name}' not found in registry",
            )

        if not config.enabled:
            self._record_if_wired(task.task_id, {
                "type": "task_failed",
                "timestamp": datetime.now(UTC).isoformat(),
                "agent_name": task.agent_name,
                "reason": f"Agent '{task.agent_name}' is disabled",
            })
            return AgentTaskResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status="failed",
                output=f"Agent '{task.agent_name}' is disabled",
            )

        if self._pause_controller is not None and task.project_id:
            is_paused = getattr(self._pause_controller, "is_paused", None)
            silenced = bool(is_paused and is_paused("project", task.project_id))
            if silenced:
                self._record_if_wired(task.task_id, {
                    "type": "task_blocked",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "agent_name": task.agent_name,
                    "reason": "Project is paused",
                })
                return AgentTaskResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status="blocked",
                    output="Project is paused",
                )

        invoker = (task.invoker_name or "").strip()
        if not invoker or not self._registry.can_invoke(invoker, task.agent_name):
            denied = invoker or "<empty>"
            self._record_if_wired(task.task_id, {
                "type": "task_failed",
                "timestamp": datetime.now(UTC).isoformat(),
                "agent_name": task.agent_name,
                "reason": f"Permission denied: '{denied}' is not permitted to dispatch '{task.agent_name}'",
            })
            return AgentTaskResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status="failed",
                output=(
                    f"Permission denied: '{denied}' is not permitted "
                    f"to dispatch '{task.agent_name}'"
                ),
            )

        # --- D11 orchestration guards ---

        nesting_result = self._check_nesting_depth(task)
        if nesting_result is not None:
            return nesting_result

        if invoker:
            escalation_result = self._check_capability_escalation(task, invoker)
            if escalation_result is not None:
                return escalation_result

        rate_result = await self._check_rate_limiter(task)
        if rate_result is not None:
            return rate_result

        spiral_result = await self._check_spiral(task)
        if spiral_result is not None:
            return spiral_result

        if (
            config.bind_tools_on_dispatch
            and self._mcp_tool_registry is not None
            and task.tools is None
        ):
            try:
                list_tools_fn = getattr(self._mcp_tool_registry, "list_tools", None)
                mcp_tools = list_tools_fn() if list_tools_fn is not None else []
                if mcp_tools:
                    task.tools = [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        }
                        for t in mcp_tools
                    ]
            except Exception:
                pass

        semaphore = self._get_semaphore(task.agent_name)
        start = time.monotonic()

        async with semaphore:
            async with self._lock:
                self._active_count += 1
                self._active_tasks[task.task_id] = task
            try:
                if self._run_recorder is not None:
                    with contextlib.suppress(Exception):
                        self._run_recorder.record(task.task_id, {
                            "type": "task_started",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "agent_name": task.agent_name,
                            "description": task.description,
                            "project_id": task.project_id,
                        })
                # Watch the in-flight task so the StallWatchdog's sweeper can flag
                # it if it hangs past its expected time; nullcontext when no
                # watchdog is injected. watch() auto-finishes on block exit.
                _watch = (
                    self._watchdog.watch(task.task_id, task.agent_name)
                    if self._watchdog is not None
                    else contextlib.nullcontext()
                )
                with _watch:
                    async with self._model_call_semaphore:
                        output = await self._executor(task)
                duration = time.monotonic() - start
                # Record the completed duration so the per-agent baseline learns
                # (and an anomalously-slow run is judged against the prior window).
                self._tracker.check_then_record(task.agent_name, duration)
                if self._run_recorder is not None:
                    with contextlib.suppress(Exception):
                        self._run_recorder.record(task.task_id, {
                            "type": "task_completed",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "agent_name": task.agent_name,
                            "output": output,
                            "duration_seconds": duration,
                        })
                return AgentTaskResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status="completed",
                    output=output,
                    duration_seconds=duration,
                )
            except asyncio.CancelledError:
                # Re-raise cancellation so it propagates instead of being
                # swallowed by the broad `except Exception` below. In Python
                # 3.11+ asyncio.CancelledError is a BaseException, but it is
                # still caught by `except Exception` in some interpreters /
                # code paths (and historically was an Exception subclass);
                # an explicit re-raise keeps graceful shutdown / dispatch_many
                # timeout cancellation distinguishable from genuine failures.
                raise
            except Exception as exc:
                duration = time.monotonic() - start
                # A normal failure still consumed wall-clock time, so learn its
                # duration too (only genuine cancellation/timeout, handled by the
                # CancelledError branch above which re-raises, is excluded).
                self._tracker.check_then_record(task.agent_name, duration)
                logger.exception("Task %s failed", task.task_id)
                if self._run_recorder is not None:
                    with contextlib.suppress(Exception):
                        self._run_recorder.record(task.task_id, {
                            "type": "task_failed",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "agent_name": task.agent_name,
                            "error": str(exc),
                            "duration_seconds": duration,
                        })
                return AgentTaskResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status="failed",
                    output=str(exc),
                    duration_seconds=duration,
                )
            finally:
                async with self._lock:
                    self._active_count -= 1
                    self._active_tasks.pop(task.task_id, None)

    async def dispatch_many(
        self,
        tasks: list[AgentTask],
        timeout: float = DEFAULT_DISPATCH_TIMEOUT,
    ) -> list[AgentTaskResult]:
        if not tasks:
            return []
        futures = [asyncio.ensure_future(self.dispatch_one(t)) for t in tasks]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*futures, return_exceptions=True),
                timeout=timeout,
            )
        except TimeoutError:
            logger.error(
                "dispatch_many timed out after %.0fs; cancelling %d pending task(s)",
                timeout,
                sum(1 for f in futures if not f.done()),
            )
            for f in futures:
                if not f.done():
                    f.cancel()
            await asyncio.gather(*futures, return_exceptions=True)
            return [
                self._result_from_future(task, fut)
                for task, fut in zip(tasks, futures, strict=True)
            ]
        out: list[AgentTaskResult] = []
        for task, res in zip(tasks, results, strict=True):
            if isinstance(res, AgentTaskResult):
                out.append(res)
            else:
                logger.error("Task %s raised in dispatch_many: %s", task.task_id, res)
                out.append(
                    AgentTaskResult(
                        task_id=task.task_id,
                        agent_name=task.agent_name,
                        status="failed",
                        output=str(res),
                    )
                )
        return out

    @staticmethod
    def _result_from_future(
        task: AgentTask, fut: asyncio.Future[AgentTaskResult]
    ) -> AgentTaskResult:
        if fut.done() and not fut.cancelled():
            exc = fut.exception()
            if exc is None:
                return fut.result()
        return AgentTaskResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status="failed",
            output="dispatch timed out",
        )
