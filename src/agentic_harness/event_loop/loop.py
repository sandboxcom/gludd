"""Event loop for the agentic harness."""

from __future__ import annotations

import logging
from typing import Any

from agentic_harness.schemas.job import JobSpec
from agentic_harness.schemas.task_decision import TaskDecision
from agentic_harness.schemas.task_return import TaskReturn, TaskReturnStatus
from agentic_harness.schemas.todo import Todo, TodoStatus

logger = logging.getLogger(__name__)


class EventLoop:
    def __init__(
        self,
        worker_base_url: str = "http://localhost:8000",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.worker_base_url = worker_base_url
        self.config = config or {}
        self._running = False

    async def tick(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "returns_reviewed": 0,
            "todos_dispatched": 0,
            "decisions_applied": 0,
            "audits_scheduled": 0,
        }
        logger.info("Event loop tick started")
        logger.info("Event loop tick completed")
        return result

    async def dispatch_return_review(
        self, task_return: TaskReturn
    ) -> dict[str, Any]:
        if task_return.status != TaskReturnStatus.CREATED:
            return {"status": "skipped", "reason": "not_created"}
        job = JobSpec(
            job_id=f"REVIEW-{task_return.return_id}",
            return_id=task_return.return_id,
            todo_id=task_return.todo_id,
            playbook="return_review.yml",
            queue="model",
            work_type="review",
            resource_profile="ai_heavy",
        )
        logger.info("Dispatching return review for %s", task_return.return_id)
        return {"status": "dispatched", "job_id": job.job_id}

    async def claim_runnable_todos(
        self, todos: list[Todo]
    ) -> list[Todo]:
        runnable = [
            t for t in todos
            if t.status == TodoStatus.QUEUED
        ]
        return runnable

    async def reconcile_decision(
        self, decision: TaskDecision, todo: Todo
    ) -> Todo:
        if decision.decision == "complete":
            todo.transition_to(TodoStatus.COMPLETE)
        elif decision.decision == "needs_more_work":
            todo.transition_to(TodoStatus.NEEDS_MORE_WORK)
        elif decision.decision == "failed":
            todo.transition_to(TodoStatus.FAILED)
        elif decision.decision == "blocked":
            todo.transition_to(TodoStatus.BLOCKED)
        elif decision.decision == "manual_hold":
            todo.transition_to(TodoStatus.MANUAL_HOLD)
        return todo

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            await self.tick()

    def stop(self) -> None:
        self._running = False
