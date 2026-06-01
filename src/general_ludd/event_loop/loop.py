"""Event loop for the agentic harness."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import TaskDecisionModel
from general_ludd.db.repository import (
    AuditEventRepository,
    TaskReturnRepository,
    TodoRepository,
    VariableNamespaceRepository,
)
from general_ludd.event_loop.lease import reclaim_expired_leases
from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus
from general_ludd.schemas.todo import Todo, TodoStatus

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


def _safe_str(obj: Any, attr: str, default: str | None = None) -> str | None:
    val = getattr(obj, attr, default)
    return val if isinstance(val, str) else default


def _resolve_prompt_text_static(
    prompt_registry: Any, prompt_profile: str | None, **kwargs: object
) -> str | None:
    if prompt_registry is None or not prompt_profile:
        return None
    try:
        result: str = prompt_registry.render(prompt_profile, **kwargs)
        return result
    except Exception:
        return None


class EventLoop:
    def __init__(
        self,
        worker_base_url: str = "http://localhost:8000",
        config: dict[str, Any] | None = None,
        session: AsyncSession | async_sessionmaker[AsyncSession] | None = None,
        http_client: Any | None = None,
        todo_repo: TodoRepository | None = None,
        task_return_repo: TaskReturnRepository | None = None,
        budget_guard: Any | None = None,
        mcp_client: MCPClient | None = None,
        mcp_tool_registry: MCPToolRegistry | None = None,
        runner: Any | None = None,
        event_bus: Any | None = None,
        project_manager: Any | None = None,
        prompt_registry: Any | None = None,
        audit_repo: Any | None = None,
        skill_registry: Any | None = None,
        variable_repo: Any | None = None,
    ) -> None:
        self.worker_base_url = worker_base_url
        self.config = config or {}
        if isinstance(session, async_sessionmaker):
            self._session_factory: async_sessionmaker[AsyncSession] | None = session
            self.session: AsyncSession | None = None
        else:
            self._session_factory = None
            self.session = session
        self._http_client = http_client
        self._runner = runner
        live_session = self.session
        self._todo_repo = todo_repo or (
            TodoRepository(live_session) if live_session else None
        )
        self._task_return_repo = task_return_repo or (
            TaskReturnRepository(live_session) if live_session else None
        )
        self._budget_guard = budget_guard
        self._mcp_client = mcp_client
        self._mcp_tool_registry = mcp_tool_registry
        self._running = False
        self._tick_state: dict[str, Any] = {}
        self._tick_metrics: dict[str, Any] = {}
        self._config_snapshot: dict[str, Any] = {}
        self._event_bus = event_bus
        self._project_manager = project_manager
        self._prompt_registry = prompt_registry
        self._audit_repo = audit_repo or (
            AuditEventRepository(live_session) if live_session else None
        )
        self._skill_registry = skill_registry
        self._variable_repo = variable_repo or (
            VariableNamespaceRepository(live_session) if live_session else None
        )
        if event_bus is not None:
            event_bus.subscribe("config_reloaded", self._on_config_reloaded)

    def _resolve_prompt_text(self, todo: Any) -> str | None:
        profile_name = _safe_str(todo, "prompt_profile")
        return _resolve_prompt_text_static(self._prompt_registry, profile_name)

    def _resolve_skill_body(self, todo: Any) -> str | None:
        if self._skill_registry is None:
            return None
        title = _safe_str(todo, "title") or ""
        matched = self._skill_registry.match_trigger(title)
        if matched:
            body: str | None = matched[0].body
            return body
        return None

    async def _load_shared_vars(self, project_id: str | None) -> dict[str, str] | None:
        if self._variable_repo is None or self.session is None:
            return None
        return await self._variable_repo.load_vars_for_project(project_id)

    def _on_config_reloaded(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        scope = payload.get("scope", "")
        logger.info("EventLoop received config reload event, scope=%s", scope)
        self._config_snapshot = dict(self.config)

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

    def get_available_tools(self) -> list[str]:
        if self._mcp_tool_registry is None:
            return []
        return self._mcp_tool_registry.tool_names()

    async def _phase_load_config_snapshot(self) -> None:
        import copy

        self._config_snapshot = copy.deepcopy(self.config)
        if self._variable_repo is not None and self.session is not None:
            shared_vars = await self._variable_repo.load_vars_for_project(None)
            if shared_vars:
                self._config_snapshot["shared_vars"] = shared_vars

    async def _phase_claim_unreviewed_task_returns(self) -> None:
        if self._task_return_repo is None:
            return
        project_id: str | None = None
        if self._project_manager is not None:
            project = self._project_manager.select_project()
            if project is not None:
                project_id = project.project_id
        claimed = await self._task_return_repo.claim_unreviewed(project_id=project_id)
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
        project_id_val = getattr(tr, "project_id", None)
        if not isinstance(project_id_val, str):
            project_id_val = None
        job = JobSpec(
            job_id=f"REVIEW-{tr.return_id}",
            return_id=tr.return_id,
            todo_id=tr.todo_id,
            playbook="return_review.yml",
            queue=_safe_str(tr, "queue", "model") or "model",
            work_type="review",
            resource_profile="ai_heavy",
            plan_artifact=_safe_str(tr, "plan_artifact"),
            project_id=project_id_val,
        )
        if self._runner is not None:
            dirs = self._runner.prepare_job_dirs(job.job_id)
            self._runner.write_vars(
                job.job_id,
                job_vars={
                    "job_id": job.job_id,
                    "todo_id": job.todo_id,
                    "return_id": job.return_id,
                    "queue": job.queue,
                    "work_type": job.work_type,
                },
                shared_vars=None,
            )
            self._runner.run_playbook(
                playbook_name="return_review.yml",
                private_data_dir=dirs["root"],
            )
            return
        if self._http_client is None:
            return
        resp = await self._http_client.post(
            f"{self.worker_base_url}/jobs/return-review",
            json=job.model_dump(mode="json"),
        )
        await self._persist_review_response(tr, resp)

    async def _persist_review_response(self, tr: Any, resp: Any) -> None:
        if self._task_return_repo is None:
            return
        try:
            body = getattr(resp, "json", None)
            if callable(body):
                data = await body()
            elif isinstance(resp, dict):
                data = resp
            else:
                data = getattr(resp, "body", None)
                if data is not None:
                    import json as _json
                    data = _json.loads(data)
                else:
                    return
            if not isinstance(data, dict):
                return
            decision = data.get("decision")
            if decision and self.session is not None:
                from general_ludd.db.models import TaskDecisionModel
                dm = TaskDecisionModel(
                    return_id=tr.return_id,
                    project_id=getattr(tr, "project_id", None),
                    matched_todo_id=getattr(tr, "todo_id", None),
                    decision=str(decision),
                    confidence=float(data.get("confidence", 0.0)),
                    evidence_refs=json.dumps(data.get("evidence_refs", [])),
                    audit_notes=json.dumps(data.get("audit_notes", [])),
                )
                self.session.add(dm)
                await self.session.flush()
                logger.info("Persisted decision for return %s: %s", tr.return_id, decision)
        except Exception as exc:
            logger.warning("Failed to persist review response for %s: %s", getattr(tr, "return_id", "?"), exc)

    async def _phase_evaluate_pid_controllers(self) -> None:
        queues_data = self._config_snapshot.get("queues", [])
        if not queues_data:
            return
        try:
            import psutil

            from general_ludd.controllers.load_scrape import LoadSnapshot
            from general_ludd.controllers.pid import LoadController
            from general_ludd.schemas.queue import Queue

            load_1, load_5, load_10 = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0.0, 0.0, 0.0)
            cpu_count = psutil.cpu_count(logical=True) or 1
            cpu_pct = psutil.cpu_percent(interval=0)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            disk_free_pct = 100 - (disk.used / disk.total * 100) if disk.total > 0 else 100.0

            controller = LoadController(cpu_count=cpu_count)
            queues = [Queue(**q) if isinstance(q, dict) else q for q in queues_data]
            snapshot = LoadSnapshot(
                loadavg_1m=load_1,
                loadavg_5m=load_5,
                loadavg_10m=load_10,
                logical_cpu_count=cpu_count,
                cpu_percent=cpu_pct,
                memory_available_percent=mem.percent,
                disk_free_percent=disk_free_pct,
                active_jobs=0,
            )
            outputs = controller.evaluate_snapshot(snapshot, queues)
            self._tick_state["pid_outputs"] = outputs
        except Exception as exc:
            logger.debug("PID evaluation skipped: %s", exc)

    async def _phase_evaluate_rules(self) -> None:
        from general_ludd.rules.engine import Rule
        from general_ludd.rules.engine import evaluate_rules as _evaluate_rules

        raw_rules = self.config.get("rules", [])
        rules = [r if isinstance(r, Rule) else Rule(**r) for r in raw_rules]
        todos_ctx = self.config.get("todos", [])
        all_results: list[dict[str, Any]] = []
        for todo_ctx in todos_ctx:
            context = {"todo": todo_ctx}
            actions = _evaluate_rules(rules, context)
            if actions:
                all_results.append({
                    "todo_id": todo_ctx.get("todo_id", ""),
                    "actions": [
                        {"rule_id": a.rule_id, "action_type": a.action_type, "params": a.params}
                        for a in actions
                    ],
                })
        self._tick_state["rule_evaluation_results"] = all_results

    async def _phase_refill_task_buckets(self) -> None:
        if self.session is not None:
            reclaimed = await reclaim_expired_leases(self.session)
            self._tick_metrics["leases_reclaimed"] = reclaimed

    async def _phase_claim_runnable_todos(self) -> None:
        if self._todo_repo is None:
            return
        if self._project_manager is not None:
            project = self._project_manager.select_project()
            if project is not None:
                claimed = await self._todo_repo.claim_runnable(
                    project_id=project.project_id
                )
            else:
                claimed = await self._todo_repo.claim_runnable()
        else:
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
        budget_context: dict[str, Any] = {}
        if self._mcp_tool_registry is not None:
            budget_context["mcp_tools"] = self._mcp_tool_registry.tool_names()
        playbook = self._config_snapshot.get("default_playbook", "noop.yml")
        prompt_text = self._resolve_prompt_text(todo)
        skill_body = self._resolve_skill_body(todo)
        project_id_val = (
            todo.project_id
            if hasattr(todo, "project_id")
            and isinstance(todo.project_id, str)
            else None
        )
        shared_vars = await self._load_shared_vars(project_id_val)
        if self._runner is not None:
            job_id = f"EXEC-{todo.todo_id}"
            dirs = self._runner.prepare_job_dirs(job_id)
            self._runner.write_vars(
                job_id,
                job_vars={
                    "job_id": job_id,
                    "todo_id": todo.todo_id,
                    "queue": _safe_str(todo, "queue", "core"),
                    "work_type": _safe_str(todo, "work_type", "unknown"),
                    "model_profile": _safe_str(todo, "model_profile"),
                    "prompt_profile": _safe_str(todo, "prompt_profile"),
                    "prompt_text": prompt_text,
                    "skill_body": skill_body,
                    **budget_context,
                },
                shared_vars=shared_vars,
            )
            self._runner.run_playbook(
                playbook_name=playbook,
                private_data_dir=dirs["root"],
            )
            return
        if self._http_client is None:
            return
        job = JobSpec(
            job_id=f"EXEC-{todo.todo_id}",
            todo_id=todo.todo_id,
            playbook=playbook,
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type=_safe_str(todo, "work_type", "unknown") or "unknown",
            resource_profile=_safe_str(todo, "resource_profile", "low_resource") or "low_resource",
            model_profile=_safe_str(todo, "model_profile"),
            prompt_profile=_safe_str(todo, "prompt_profile"),
            plan_artifact=_safe_str(todo, "plan_artifact"),
            prompt_text=prompt_text,
            budget_context=budget_context,
            project_id=project_id_val,
        )
        resp = await self._http_client.post(
            f"{self.worker_base_url}/jobs/execute",
            json=job.model_dump(mode="json"),
        )
        await self._persist_task_return(todo, job, resp)

    async def _persist_task_return(self, todo: Any, job: JobSpec, resp: Any) -> None:
        if self._task_return_repo is None:
            return
        try:
            body = getattr(resp, "json", None)
            if callable(body):
                data = await body()
            elif isinstance(resp, dict):
                data = resp
            else:
                return
            if not isinstance(data, dict):
                return
            await self._task_return_repo.create(data={
                "return_id": data.get("return_id", f"RET-{job.job_id}"),
                "todo_id": todo.todo_id,
                "job_id": job.job_id,
                "playbook": job.playbook,
                "queue": job.queue,
                "exit_code": data.get("exit_code", 0),
                "result_summary": data.get("result_summary", ""),
                "project_id": job.project_id,
            })
            if self.session is not None:
                await self.session.flush()
            logger.info("Persisted TaskReturn for todo %s", todo.todo_id)
        except Exception as exc:
            logger.warning("Failed to persist task return for %s: %s", todo.todo_id, exc)

    async def _phase_reconcile_completed_decisions(self) -> None:
        if self.session is None or self._todo_repo is None:
            return
        stmt = (
            select(TaskDecisionModel)
            .order_by(TaskDecisionModel.created_at.desc())
            .limit(50)
        )
        if self._project_manager is not None:
            project = self._project_manager.select_project()
            if project is not None:
                stmt = stmt.where(TaskDecisionModel.project_id == project.project_id)
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
                if self._audit_repo is not None:
                    with contextlib.suppress(Exception):
                        await self._audit_repo.create(
                            event_type="todo_status_changed",
                            entity_type="todo",
                            entity_id=todo.todo_id,
                            project_id=todo.project_id,
                            details=json.dumps({
                                "old": todo.status,
                                "new": new_status.value,
                                "decision": d.decision,
                            }),
                        )
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
