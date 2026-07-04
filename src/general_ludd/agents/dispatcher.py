"""Agent dispatcher for concurrent subagent task execution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentTask
from general_ludd.observability.timing import (
    DurationTracker,
    StallWatchdog,
    default_tracker,
)
from general_ludd.replay.recorder import RunRecorder

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
        pause_controller: Any | None = None,
        run_recorder: RunRecorder | None = None,
    ) -> None:
        self._registry = registry
        self._executor: ExecutorFn = executor or _noop_executor
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._active_count = 0
        self._active_tasks: dict[str, AgentTask] = {}
        self._lock = asyncio.Lock()
        self._pause_controller = pause_controller
        self._run_recorder = run_recorder
        # Per-task duration-anomaly + hung-task detection. The tracker learns a
        # per-agent baseline from completed/failed runs; the (optional) watchdog
        # registers each in-flight task so the daemon's stall sweeper can flag a
        # task that hangs past its expected time. Defaults to the process-wide
        # shared tracker so histories accumulate across call sites.
        self._tracker = tracker or default_tracker()
        self._watchdog = watchdog

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

    def _record_if_wired(self, run_id: str, event: dict[str, Any]) -> None:
        if self._run_recorder is not None:
            with contextlib.suppress(Exception):
                self._run_recorder.record(run_id, event)

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
            silenced = self._pause_controller.is_paused("project", task.project_id)
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
