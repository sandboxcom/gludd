"""Agent dispatcher for concurrent subagent task execution."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field

from general_ludd.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)


@dataclass
class AgentTask:
    task_id: str
    agent_name: str
    description: str
    prompt: str
    parent_task_id: str | None = None
    created_at: float = field(default_factory=time.time)


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
    ) -> None:
        self._registry = registry
        self._executor: ExecutorFn = executor or _noop_executor
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._active_count = 0
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return self._active_count

    def _get_semaphore(self, agent_name: str) -> asyncio.Semaphore:
        if agent_name not in self._semaphores:
            config = self._registry.get(agent_name)
            limit = config.max_concurrent if config else 1
            self._semaphores[agent_name] = asyncio.Semaphore(limit)
        return self._semaphores[agent_name]

    async def dispatch_one(self, task: AgentTask) -> AgentTaskResult:
        config = self._registry.get(task.agent_name)
        if config is None:
            return AgentTaskResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status="failed",
                output=f"Agent '{task.agent_name}' not found in registry",
            )

        semaphore = self._get_semaphore(task.agent_name)
        start = time.monotonic()

        async with semaphore:
            async with self._lock:
                self._active_count += 1
            try:
                output = await self._executor(task)
                duration = time.monotonic() - start
                return AgentTaskResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status="completed",
                    output=output,
                    duration_seconds=duration,
                )
            except Exception as exc:
                duration = time.monotonic() - start
                logger.exception("Task %s failed", task.task_id)
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

    async def dispatch_many(self, tasks: list[AgentTask]) -> list[AgentTaskResult]:
        coros = [self.dispatch_one(t) for t in tasks]
        results = await asyncio.gather(*coros)
        return list(results)
