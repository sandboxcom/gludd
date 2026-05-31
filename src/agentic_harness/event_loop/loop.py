"""Event loop for the agentic harness."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentic_harness.db.models import TaskDecisionModel
from agentic_harness.db.repository import TaskReturnRepository, TodoRepository
from agentic_harness.event_loop.lease import reclaim_expired_leases
from agentic_harness.schemas.job import JobSpec
from agentic_harness.schemas.task_decision import TaskDecision
from agentic_harness.schemas.task_return import TaskReturn, TaskReturnStatus
from agentic_harness.schemas.todo import Todo, TodoStatus

logger = logging.getLogger(__name__)
PHASE_ORDER = [
    "load_config_snapshot",
    "claim_unreviewed_task_returns",
    "dispatch_return_review_jobs",
    "evaluate_pid_controllers",
    "evaluate_rules",
    "refill_task_buckets",
    "claim_runnable_todos",
    "dispatch_execute_jobs",
    "reconcile_completed_decisions",
    "emit_tick_metrics",
]


class EventLoop:
    def __init__(
        self,
        worker_base_url: str = "http://localhost:8000",
        config: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
        http_client: Any | None = None,
        todo_repo: TodoRepository | None = None,
        task_return_repo: TaskReturnRepository | None = None,
        budget_guard: Any | None = None,
    ) -> None:
        self.worker_base_url = worker_base_url
        self.config = config or {}
        self.session = session
        self._http_client = http_client
        self._todo_repo = todo_repo or (
            TodoRepository(session) if session else None
        )
        self._task_return_repo = task_return_repo or (
            TaskReturnRepository(session) if session else None
        )
        self._budget_guard = budget_guard
        self._running = False
        self._tick_state: dict[str, Any] = {}
        self._tick_metrics: dict[str, Any] = {}
        self._config_snapshot: dict[str, Any] = {}

    async def tick(self) -> dict[str, Any]:
        self._tick_state = {}
        self._tick_metrics = {
            "phases_completed": 0,
            "tick_duration_ms": 0.0,
            "returns_reviewed": 0,
            "todos_dispatched": 0,
            "decisions_applied": 0,
            "leases_reclaimed": 0,
        }
        start = time.monotonic()
        for phase_name in PHASE_ORDER:
            phase_fn = getattr(self, f"_phase_{phase_name}")
            logger.info("Phase started: %s", phase_name)
            await phase_fn()
            logger.info("Phase completed: %s", phase_name)
            self._tick_metrics["phases_completed"] += 1
        elapsed = time.monotonic() - start
        self._tick_metrics["tick_duration_ms"] = elapsed * 1000
        return self._tick_metrics

    async def run_forever(self, interval: float = 1.0) -> None:
        self._running = True
        while self._running:
            await self.tick()
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False

    async def _phase_load_config_snapshot(self) -> None:
        self._config_snapshot = dict(self.config)

    async def _phase_claim_unreviewed_task_returns(self) -> None:
        if self._task_return_repo is None:
            return
        claimed = await self._task_return_repo.claim_unreviewed()
        self._tick_state["claimed_returns"] = claimed

    async def _phase_dispatch_return_review_jobs(self) -> None:
        claimed = self._tick_state.get("claimed_returns", [])
        if self._budget_guard is not None:
            check = self._budget_guard.check_all_limits()
            if not check["allowed"]:
                logger.warning(
                    "Budget exceeded, skipping return review dispatch: %s",
                    check["reason"],
                )
                self._tick_metrics["returns_reviewed"] = 0
                return
        for tr in claimed:
            await self._dispatch_review_job(tr)
        self._tick_metrics["returns_reviewed"] = len(claimed)

    async def _dispatch_review_job(self, tr: Any) -> None:
        if self._http_client is None:
            return
        job = JobSpec(
            job_id=f"REVIEW-{tr.return_id}",
            return_id=tr.return_id,
            todo_id=tr.todo_id,
            playbook="return_review.yml",
            queue=getattr(tr, "queue", "model"),
            work_type="review",
            resource_profile="ai_heavy",
            plan_artifact=getattr(tr, "plan_artifact", None),
        )
        await self._http_client.post(
            f"{self.worker_base_url}/jobs/return-review",
            json=job.model_dump(mode="json"),
        )

    async def _phase_evaluate_pid_controllers(self) -> None:
        pass

    async def _phase_evaluate_rules(self) -> None:
        pass

    async def _phase_refill_task_buckets(self) -> None:
        if self.session is not None:
            reclaimed = await reclaim_expired_leases(self.session)
            self._tick_metrics["leases_reclaimed"] = reclaimed

    async def _phase_claim_runnable_todos(self) -> None:
        if self._todo_repo is None:
            return
        claimed = await self._todo_repo.claim_runnable()
        self._tick_state["claimed_todos"] = claimed

    async def _phase_dispatch_execute_jobs(self) -> None:
        claimed = self._tick_state.get("claimed_todos", [])
        if self._budget_guard is not None:
            check = self._budget_guard.check_all_limits()
            if not check["allowed"]:
                logger.warning(
                    "Budget exceeded, skipping execute dispatch: %s",
                    check["reason"],
                )
                self._tick_metrics["todos_dispatched"] = 0
                return
        for todo in claimed:
            await self._dispatch_execute_job(todo)
        self._tick_metrics["todos_dispatched"] = len(claimed)

    async def _dispatch_execute_job(self, todo: Any) -> None:
        if self._http_client is None:
            return
        job = JobSpec(
            job_id=f"EXEC-{todo.todo_id}",
            todo_id=todo.todo_id,
            playbook=self._config_snapshot.get("default_playbook", "noop.yml"),
            queue=getattr(todo, "queue", "core"),
            work_type=getattr(todo, "work_type", "unknown"),
            resource_profile=getattr(todo, "resource_profile", "low_resource"),
            plan_artifact=getattr(todo, "plan_artifact", None),
        )
        await self._http_client.post(
            f"{self.worker_base_url}/jobs/execute",
            json=job.model_dump(mode="json"),
        )

    async def _phase_reconcile_completed_decisions(self) -> None:
        if self.session is None or self._todo_repo is None:
            return
        stmt = (
            select(TaskDecisionModel)
            .order_by(TaskDecisionModel.created_at.desc())
            .limit(50)
        )
        result = await self.session.execute(stmt)
        decisions = list(result.scalars().all())
        reconciled = 0
        for d in decisions:
            if not d.matched_todo_id:
                continue
            todo = await self._todo_repo.get_by_id(d.matched_todo_id)
            if todo is None or todo.status != TodoStatus.REVIEWING_RETURN.value:
                continue
            new_status = self._decision_to_status(d.decision)
            if new_status is not None:
                await self._todo_repo.transition(
                    todo.todo_id, new_status, todo.version
                )
                reconciled += 1
        self._tick_metrics["decisions_applied"] = reconciled

    def _decision_to_status(self, decision: str) -> TodoStatus | None:
        mapping: dict[str, TodoStatus] = {
            "complete": TodoStatus.COMPLETE,
            "needs_more_work": TodoStatus.NEEDS_MORE_WORK,
            "failed": TodoStatus.FAILED,
            "blocked": TodoStatus.BLOCKED,
            "manual_hold": TodoStatus.MANUAL_HOLD,
        }
        return mapping.get(decision)

    async def _phase_emit_tick_metrics(self) -> None:
        logger.info("Tick metrics: %s", self._tick_metrics)

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

    async def claim_runnable_todos(self, todos: list[Todo]) -> list[Todo]:
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
