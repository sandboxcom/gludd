"""Event loop for the agentic harness."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.controllers.load_scrape import LoadSnapshot
from general_ludd.controllers.pid import LoadController
from general_ludd.db.models import TaskDecisionModel
from general_ludd.db.repository import (
    AuditEventRepository,
    ConcurrencyError,
    TaskReturnRepository,
    TodoRepository,
    VariableNamespaceRepository,
)
from general_ludd.event_loop.lease import reclaim_expired_leases
from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.models.job_invocation import (
    invoke_model_for_generation,
    is_generation_work_type,
)
from general_ludd.rules.engine import Rule, apply_rule_actions, evaluate_rules
from general_ludd.schemas.benchmark import TaskType
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.queue import Queue
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
    "self_improve",
    "emit_tick_metrics",
]


def _safe_str(obj: Any, attr: str, default: str | None = None) -> str | None:
    val = getattr(obj, attr, default)
    return val if isinstance(val, str) else default


def _resolve_prompt_text_static(
    prompt_registry: Any,
    prompt_profile: str | None,
    **kwargs: object,
) -> str | None:
    project_templates_dir: object = kwargs.pop("project_templates_dir", None)
    if not prompt_profile:
        return None
    if project_templates_dir is not None:
        from pathlib import Path as _Path
        tmpl_path = _Path(str(project_templates_dir)) / prompt_profile
        if tmpl_path.is_file():
            try:
                from jinja2 import Environment as _Env
                from jinja2 import FileSystemLoader as _FSL
                env = _Env(loader=_FSL(str(project_templates_dir)), autoescape=True)
                tmpl = env.get_template(prompt_profile)
                return tmpl.render(**kwargs)
            except Exception:
                pass
    if prompt_registry is None:
        return None
    try:
        result: str = prompt_registry.render(prompt_profile, **kwargs)
        return result
    except Exception:
        return None


_WORK_TYPE_TASK_TYPE_MAP: dict[str, str] = {
    "bug_fix": "bug_fix", "code": "feature", "test": "test_write",
    "review": "code_review", "refactor": "refactor", "docs": "documentation",
    "infra": "feature", "prompt": "feature", "analysis": "feature",
    "audit": "feature", "release": "feature", "dependency": "feature",
    "security": "security_fix", "model": "feature", "unknown": "feature",
}


def _work_type_to_task_type(work_type: str) -> Any:
    mapped = _WORK_TYPE_TASK_TYPE_MAP.get(work_type, "feature")
    try:
        return TaskType(mapped)
    except ValueError:
        return TaskType.FEATURE


_WORK_TYPE_PLAYBOOK_MAP: dict[str, str] = {
    "code": "validate_task.yml", "test": "molecule_test.yml",
    "analysis": "gap_analysis.yml", "audit": "log_audit.yml",
    "prompt": "prompt_eval.yml", "self_improvement": "self_improve_harness.yml",
    "dependency": "dependency_update.yml", "review": "return_review.yml",
    "docs": "noop.yml", "infra": "noop.yml", "security": "noop.yml",
    "model": "noop.yml", "release": "noop.yml",
}


def _playbook_for_work_type(
    work_type: str,
    default: str = "noop.yml",
    *,
    project_id: str | None = None,
    workspaces: dict[str, Any] | None = None,
) -> str:
    ws = workspaces.get(project_id) if workspaces and project_id else None
    if ws is not None and hasattr(ws, "playbooks_dir"):
        from pathlib import Path as _Path
        pb_path = _Path(ws.playbooks_dir) / f"{work_type}.yml"
        if pb_path.is_file():
            return str(pb_path)
    return _WORK_TYPE_PLAYBOOK_MAP.get(work_type, default)


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
        adaptive_router: Any | None = None,
        daemon_state: dict[str, Any] | None = None,
        project_secrets_manager: Any | None = None,
        project_workspace: Any | None = None,
        self_improve_interval: int = 0,
        reviewer: Any | None = None,
        model_gateway: Any | None = None,
    ) -> None:
        self.worker_base_url = worker_base_url
        self.config = config or {}
        self._daemon_state = daemon_state
        self._project_secrets_manager = project_secrets_manager
        self._project_workspace = project_workspace
        self._self_improve_interval = self_improve_interval
        self._reviewer = reviewer
        self._model_gateway = model_gateway
        self._stuck_timeout_minutes = 15
        self._max_retries = 3
        if isinstance(session, async_sessionmaker):
            self._session_factory: async_sessionmaker[AsyncSession] | None = session
            self.session: AsyncSession | None = None
        else:
            self._session_factory = None
            self.session = session
        self._http_client = http_client
        self._runner = runner
        self._active_session: AsyncSession | None = self.session
        live_session = self.session
        self._todo_repo = todo_repo or (TodoRepository(live_session) if live_session else None)
        self._task_return_repo = task_return_repo or (TaskReturnRepository(live_session) if live_session else None)
        self._budget_guard = budget_guard
        self._mcp_client = mcp_client
        self._mcp_tool_registry = mcp_tool_registry
        self._running = False
        self._total_ticks = 0
        self._tick_state: dict[str, Any] = {}
        self._active_traces: dict[str, Any] = {}
        self._benchmark_recorder: Any = None
        self._observability_enabled: bool = bool(adaptive_router)
        self._tick_metrics: dict[str, Any] = {}
        # Reconcile idempotency ledgers (defects F1/F2):
        #   _applied_decisions  — decision ids whose status transition has already
        #     been applied; re-applying the same decision on a later tick/re-run is
        #     a no-op (F1: non-idempotent decision re-apply).
        #   _pushed_work        — todo (work) ids whose completed work has already
        #     been pushed; guarantees the push fires exactly once and is never
        #     duplicated across ticks (F2: completed-work push lost/double).
        self._applied_decisions: set[str] = set()
        self._pushed_work: set[str] = set()
        self._config_snapshot: dict[str, Any] = {}
        self._event_bus = event_bus
        self._project_manager = project_manager
        self._prompt_registry = prompt_registry
        self._audit_repo = audit_repo or (AuditEventRepository(live_session) if live_session else None)
        self._skill_registry = skill_registry
        self._variable_repo = variable_repo or (VariableNamespaceRepository(live_session) if live_session else None)
        if event_bus is not None:
            event_bus.subscribe("config_reloaded", self._on_config_reloaded)
        self._adaptive_router = adaptive_router

    def _resolve_prompt_text(self, todo: Any) -> str | None:
        profile_name = _safe_str(todo, "prompt_profile")
        return _resolve_prompt_text_static(self._prompt_registry, profile_name)

    async def _append_message_queue_section(
        self, prompt_text: str | None, todo: Any, project_id: str | None
    ) -> str | None:
        """PART 4: tell a dispatched agent the MQ + facts are available.

        Gated behind config flag ``message_queue_prompt`` (default off) so prompts
        without MQ context are byte-for-byte unchanged. The agent "role" is the
        todo's assigned_agent, falling back to its work_type.
        """
        if not self.config.get("message_queue_prompt"):
            return prompt_text
        role = _safe_str(todo, "assigned_agent") or _safe_str(todo, "work_type") or "agent"
        unread = 0
        senders: list[str] = []
        factory = self._session_factory
        if factory is not None:
            try:
                from general_ludd.db.repository import AgentMessageRepository
                async with factory() as session:
                    repo = AgentMessageRepository(session)
                    msgs = await repo.inbox(role, unread_only=True, project_id=project_id)
                    unread = len(msgs)
                    senders = [m.sender for m in msgs]
            except Exception:
                unread = 0
                senders = []
        from general_ludd.prompts.registry import render_message_queue_section
        section = render_message_queue_section(
            role=role, unread_count=unread, senders=senders, enabled=True
        )
        if not section:
            return prompt_text
        return f"{prompt_text}\n\n{section}" if prompt_text else section

    async def _resolve_adaptive_prompt(
        self, todo: Any, default_model_profile: str = "default"
    ) -> tuple[str | None, str | None, Any | None]:
        if self._adaptive_router is None:
            return None, None, None
        work_type = _safe_str(todo, "work_type", "feature") or "feature"
        task_type = _work_type_to_task_type(work_type)
        default_prompt = _safe_str(todo, "prompt_profile")
        decision = await self._adaptive_router.route(
            task_type=task_type,
            default_prompt_profile=default_prompt,
            default_model_profile=default_model_profile,
        )
        return (decision.selected_prompt_profile_id, decision.selected_model_profile_id, decision)

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
        if self._variable_repo is None or self._active_session is None:
            return None
        return await self._variable_repo.load_vars_for_project(project_id)

    def _on_config_reloaded(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        scope = payload.get("scope", "")
        logger.info("EventLoop received config reload event, scope=%s", scope)
        self._config_snapshot = dict(self.config)

    async def _reap_stuck_todos(self) -> None:
        """Requeue ACTIVE todos whose worker is genuinely gone.

        A liveness signal is required before reaping: an ACTIVE todo is only
        "stuck" if its bucket lease has *expired* (or never existed). A todo that
        is still executing holds a live (unexpired) lease — its ``updated_at`` is
        frozen at claim time with no heartbeat, so ``updated_at`` ALONE is not a
        liveness clock and must never be used to reap live work.

        ``version`` is NOT a retry counter (it is bumped by every write), so it is
        no longer conflated with attempts. A genuinely stale todo is simply
        requeued for another attempt.
        """
        if self._active_session is None or self._todo_repo is None:
            return
        try:
            from general_ludd.db.models import BucketLeaseModel, TodoModel
            now = datetime.now(UTC)
            cutoff = now - timedelta(minutes=self._stuck_timeout_minutes)
            stmt = (
                select(TodoModel)
                .where(TodoModel.status == TodoStatus.ACTIVE.value)
                .where(TodoModel.updated_at < cutoff)
            )
            result = await self._active_session.execute(stmt)
            candidates = list(result.scalars().all())
            reaped = 0
            for todo in candidates:
                # Liveness gate: a still-running worker holds a live bucket lease.
                # Only reap when NO unexpired lease exists for this todo.
                queue = _safe_str(todo, "queue", "core") or "core"
                todo_id = _safe_str(todo, "todo_id", "") or ""
                bucket_key = f"{queue}:{todo_id}"
                live_lease = (
                    await self._active_session.execute(
                        select(BucketLeaseModel)
                        .where(BucketLeaseModel.bucket_key == bucket_key)
                        .where(BucketLeaseModel.expires_at > now)
                    )
                ).scalar_one_or_none()
                if live_lease is not None:
                    # Worker is still heartbeating (lease alive) -> do NOT reap.
                    continue
                todo.status = TodoStatus.QUEUED.value
                todo.updated_at = now
                reaped += 1
            if reaped:
                await self._active_session.flush()
                logger.info("Reaped %d stuck ACTIVE todos (no live lease)", reaped)
        except Exception as exc:
            logger.warning("Stuck-todo reaper failed: %s", exc)

    async def tick(self) -> dict[str, Any]:
        self._tick_state = {}
        self._total_ticks += 1
        self._tick_metrics = {
            "total_ticks": self._total_ticks, "phases_completed": 0,
            "tick_duration_ms": 0.0, "returns_reviewed": 0,
            "todos_dispatched": 0, "decisions_applied": 0,
            "leases_reclaimed": 0,
        }
        start = time.monotonic()
        needs_own_session = self.session is None and self._session_factory is not None
        if needs_own_session:
            assert self._session_factory is not None
            async with self._session_factory() as session:
                self._active_session = session
                self._todo_repo = TodoRepository(session)
                self._task_return_repo = TaskReturnRepository(session)
                self._audit_repo = AuditEventRepository(session)
                self._variable_repo = VariableNamespaceRepository(session)
                await self._run_phases()
                try:
                    await session.commit()
                except Exception as exc:
                    logger.warning("Failed to commit tick session: %s", exc)
                self._active_session = None
                self._todo_repo = None
                self._task_return_repo = None
                self._audit_repo = None
                self._variable_repo = None
        else:
            if self.session is not None:
                self._active_session = self.session
                self._todo_repo = self._todo_repo or TodoRepository(self.session)
                self._task_return_repo = self._task_return_repo or TaskReturnRepository(self.session)
                self._audit_repo = self._audit_repo or AuditEventRepository(self.session)
                self._variable_repo = self._variable_repo or VariableNamespaceRepository(self.session)
            await self._run_phases()
            self._active_session = None
        elapsed = time.monotonic() - start
        self._tick_metrics["tick_duration_ms"] = elapsed * 1000
        if self._daemon_state is not None:
            self._daemon_state["tick_metrics"] = dict(self._tick_metrics)
        return self._tick_metrics

    async def _run_phases(self) -> None:
        for phase_name in PHASE_ORDER:
            phase_fn = getattr(self, f"_phase_{phase_name}")
            try:
                logger.info("Phase started: %s", phase_name)
                await phase_fn()
                logger.info("Phase completed: %s", phase_name)
                self._tick_metrics["phases_completed"] += 1
            except Exception as exc:
                logger.error("Phase %s raised %s: %s", phase_name, type(exc).__name__, exc)

    async def run_forever(self, interval: float = 1.0) -> None:
        self._running = True
        try:
            while self._running:
                await self.tick()
                await asyncio.sleep(interval)
        except Exception as exc:
            logger.error("EventLoop run_forever exited with error: %s", exc)
            raise
        finally:
            logger.error("EventLoop run_forever stopped; no further ticks will occur")

    def stop(self) -> None:
        self._running = False

    def get_available_tools(self) -> list[str]:
        if self._mcp_tool_registry is None:
            return []
        return self._mcp_tool_registry.tool_names()

    async def _phase_load_config_snapshot(self) -> None:
        import copy
        self._config_snapshot = copy.deepcopy(self.config)
        if self._variable_repo is not None and self._active_session is not None:
            shared_vars = await self._variable_repo.load_vars_for_project(None)
            if shared_vars:
                self._config_snapshot["shared_vars"] = shared_vars

    def _select_tick_project(self) -> Any | None:
        """M14 (W3.14): select ONE project per tick and reuse it everywhere.

        The claim, review, and reconcile phases must all operate on the same
        project; previously each called select_project() independently.
        """
        if self._project_manager is None:
            return None
        if "selected_project" in self._tick_state:
            return self._tick_state["selected_project"]
        project = self._project_manager.select_project()
        self._tick_state["selected_project"] = project
        return project

    def _tick_project_id(self) -> str | None:
        project = self._select_tick_project()
        return project.project_id if project is not None else None

    def _estimated_dispatch_cost(self, item_count: int) -> float:
        budget_cfg = self.config.get("budget", {}) if isinstance(self.config, dict) else {}
        per_job = budget_cfg.get("per_dispatch_usd", 0.01) if isinstance(budget_cfg, dict) else 0.01
        try:
            return float(per_job) * max(0, int(item_count))
        except (TypeError, ValueError):
            return 0.0

    async def _phase_claim_unreviewed_task_returns(self) -> None:
        if self._task_return_repo is None:
            return
        project_id = self._tick_project_id()
        claimed = await self._task_return_repo.claim_unreviewed(project_id=project_id)
        self._tick_state["claimed_returns"] = claimed

    async def _phase_dispatch_return_review_jobs(self) -> None:
        claimed = self._tick_state.get("claimed_returns", [])
        if self._budget_guard is not None:
            check = self._budget_guard.check_all_limits(
                estimated_cost=self._estimated_dispatch_cost(len(claimed))
            )
            if not check["allowed"]:
                logger.warning("Budget exceeded, skipping return review dispatch: %s", check["reason"])
                self._tick_metrics["returns_reviewed"] = 0
                return
        for tr in claimed:
            await self._dispatch_review_job(tr)
        self._tick_metrics["returns_reviewed"] = len(claimed)

    async def _dispatch_review_job(self, tr: Any) -> None:
        # H4 (W3.2): when a gateway-backed reviewer is wired, review in-process
        # and route the decision through apply_decision. Failure escalates the
        # todo — it is never silently marked complete / passed through.
        if (
            self._reviewer is not None
            and self._active_session is not None
            and self._todo_repo is not None
        ):
            await self._review_in_process(tr)
            return
        project_id_val = getattr(tr, "project_id", None)
        if not isinstance(project_id_val, str):
            project_id_val = None
        job = JobSpec(
            job_id=f"REVIEW-{tr.return_id}", return_id=tr.return_id,
            todo_id=tr.todo_id, playbook="return_review.yml",
            queue=_safe_str(tr, "queue", "model") or "model",
            work_type="review", resource_profile="ai_heavy",
            plan_artifact=_safe_str(tr, "plan_artifact"),
            project_id=project_id_val,
        )
        if self._runner is not None:
            dirs = self._runner.prepare_job_dirs(job.job_id)
            self._runner.write_vars(job.job_id, job_vars={
                "job_id": job.job_id, "todo_id": job.todo_id,
                "return_id": job.return_id, "queue": job.queue,
                "work_type": job.work_type,
            }, shared_vars=None)
            await asyncio.to_thread(
                self._runner.run_playbook,
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

    async def _review_in_process(self, tr: Any) -> None:
        from general_ludd.review.decision_applier import apply_decision

        assert self._reviewer is not None
        return_id = getattr(tr, "return_id", "")
        todo_id = getattr(tr, "todo_id", None)
        task_return = TaskReturn(
            return_id=return_id,
            todo_id=todo_id,
            job_id=getattr(tr, "job_id", None) or f"JOB-{return_id}",
            playbook=getattr(tr, "playbook", None) or "noop.yml",
            queue=_safe_str(tr, "queue", "model") or "model",
            work_type=_safe_str(tr, "work_type", "review") or "review",
            exit_code=int(getattr(tr, "exit_code", 0) or 0),
            result_summary=_safe_str(tr, "result_summary", "") or "",
        )
        try:
            decision = self._reviewer.review_return(
                task_return, candidate_todos=[], artifacts=[]
            )
        except Exception as exc:
            # Reviewer itself failed — escalate, never silent pass/complete.
            logger.error("Reviewer raised for return %s: %s", return_id, exc)
            decision = TaskDecision(
                return_id=return_id,
                matched_todo_id=todo_id,
                decision="manual_hold",
                confidence=0.0,
                audit_notes=[f"Reviewer error: {exc}"],
            )
        assert self._todo_repo is not None
        assert self._active_session is not None
        try:
            await apply_decision(decision, self._todo_repo, self._active_session)
            await self._active_session.flush()
        except Exception as exc:
            logger.error(
                "apply_decision failed for return %s (decision=%s): %s",
                return_id,
                getattr(decision, "decision", "?"),
                exc,
            )
            return
        if self._audit_repo is not None:
            with contextlib.suppress(Exception):
                await self._audit_repo.create(
                    event_type="return_reviewed",
                    entity_type="task_return",
                    entity_id=return_id,
                    project_id=getattr(tr, "project_id", None),
                    details=json.dumps(
                        {
                            "decision": decision.decision,
                            "confidence": decision.confidence,
                            "matched_todo_id": decision.matched_todo_id,
                        }
                    ),
                )
        logger.info(
            "In-process review for return %s -> %s", return_id, decision.decision
        )

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
            if decision and self._active_session is not None:
                dm = TaskDecisionModel(
                    return_id=tr.return_id,
                    project_id=getattr(tr, "project_id", None),
                    matched_todo_id=getattr(tr, "todo_id", None),
                    decision=str(decision),
                    confidence=float(data.get("confidence", 0.0)),
                    evidence_refs=json.dumps(data.get("evidence_refs", [])),
                    audit_notes=json.dumps(data.get("audit_notes", [])),
                )
                self._active_session.add(dm)
                await self._active_session.flush()
                logger.info("Persisted decision for return %s: %s", tr.return_id, decision)
        except Exception as exc:
            logger.warning("Failed to persist review response for %s: %s", getattr(tr, "return_id", "?"), exc)

    async def _phase_evaluate_pid_controllers(self) -> None:
        queues_data = self._config_snapshot.get("queues", [])
        if not queues_data:
            return
        try:
            import psutil
            load_1, load_5, load_10 = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0.0, 0.0, 0.0)
            cpu_count = psutil.cpu_count(logical=True) or 1
            cpu_pct = psutil.cpu_percent(interval=0)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            disk_free_pct = 100 - (disk.used / disk.total * 100) if disk.total > 0 else 100.0
            controller = LoadController(cpu_count=cpu_count)
            queues = [Queue(**q) if isinstance(q, dict) else q for q in queues_data]
            active_jobs = 0
            if self._todo_repo is not None and self._active_session is not None:
                with contextlib.suppress(Exception):
                    active_jobs = await self._todo_repo.count_active()
            snapshot = LoadSnapshot(
                loadavg_1m=load_1, loadavg_5m=load_5, loadavg_10m=load_10,
                logical_cpu_count=cpu_count, cpu_percent=cpu_pct,
                memory_available_percent=mem.percent, disk_free_percent=disk_free_pct,
                active_jobs=active_jobs,
            )
            outputs = controller.evaluate_snapshot(snapshot, queues)
            self._tick_state["pid_outputs"] = outputs
        except Exception as exc:
            logger.debug("PID evaluation skipped: %s", exc)

    async def _phase_evaluate_rules(self) -> None:
        raw_rules = self.config.get("rules", [])
        rules = [r if isinstance(r, Rule) else Rule(**r) for r in raw_rules]
        todos_ctx = self.config.get("todos", [])
        all_results: list[dict[str, Any]] = []
        for todo_ctx in todos_ctx:
            context = {"todo": todo_ctx}
            actions = evaluate_rules(rules, context)
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
        if self._active_session is not None:
            reclaimed = await reclaim_expired_leases(self._active_session)
            self._tick_metrics["leases_reclaimed"] = reclaimed
        if self._todo_repo is not None and self._active_session is not None:
            await self._reap_stuck_todos()

    async def _phase_claim_runnable_todos(self) -> None:
        if self._todo_repo is None:
            return
        project_id = self._tick_project_id()

        # Derive the claim limit from the PID cap so we never set more rows
        # ACTIVE than we will actually dispatch.  Without this, the dispatch
        # phase truncates the Python list AFTER the DB write, leaving
        # ACTIVE-undispatched rows that accumulate until the 15-min reaper.
        hard_cap = 10  # matches claim_runnable default
        pid_outputs = self._tick_state.get("pid_outputs")
        if pid_outputs is not None and hasattr(pid_outputs, "desired_total_active_buckets"):
            pid_cap = pid_outputs.desired_total_active_buckets
            hard_cap = min(hard_cap, pid_cap)
        claim_limit = hard_cap

        if project_id is not None:
            claimed = await self._todo_repo.claim_runnable(limit=claim_limit, project_id=project_id)
        else:
            claimed = await self._todo_repo.claim_runnable(limit=claim_limit)
        self._tick_state["claimed_todos"] = claimed
        # H15 (W2.5): record a bucket lease per claimed todo so a crashed tick's
        # work can be reclaimed once the lease expires.
        if claimed and self._active_session is not None:
            from general_ludd.event_loop.lease import acquire_lease
            holder = f"tick-{self._total_ticks}"
            for todo in claimed:
                bucket_key = _safe_str(todo, "queue", "core") or "core"
                with contextlib.suppress(Exception):
                    await acquire_lease(
                        self._active_session,
                        bucket_key=f"{bucket_key}:{_safe_str(todo, 'todo_id', '')}",
                        holder_id=holder,
                        project_id=project_id,
                    )

    async def _phase_dispatch_execute_jobs(self) -> None:
        claimed = self._tick_state.get("claimed_todos", [])
        pid_outputs = self._tick_state.get("pid_outputs")
        cap = None
        if pid_outputs is not None and hasattr(pid_outputs, "desired_total_active_buckets"):
            cap = pid_outputs.desired_total_active_buckets
        if self._budget_guard is not None:
            check = self._budget_guard.check_all_limits(
                estimated_cost=self._estimated_dispatch_cost(len(claimed))
            )
            if not check["allowed"]:
                logger.warning("Budget exceeded, skipping execute dispatch: %s", check["reason"])
                self._tick_metrics["todos_dispatched"] = 0
                return

        # Apply PID cap.
        if cap is not None and len(claimed) > cap:
            logger.info(
                "PID cap reached: dispatching %d of %d claimed (cap=%d)",
                cap, len(claimed), cap,
            )
            claimed = list(claimed[:cap])

        # W(#23): wire Scheduler.plan() to determine concurrency-safe batches.
        # Each batch may run concurrently (asyncio.gather) when a session_factory
        # is available — each gathered coroutine opens its OWN async session so
        # SQLAlchemy's "no concurrent flush on one session" rule is never violated.
        # Falls back to sequential dispatch when no session_factory (e.g. tests
        # that pass a bare session=...).
        dispatch_count = await self._dispatch_jobs_via_scheduler(claimed)
        self._tick_metrics["todos_dispatched"] = dispatch_count

    async def _dispatch_jobs_via_scheduler(self, todos: list[Any]) -> int:
        """Dispatch todos ordered + grouped by Scheduler.plan().

        Concurrent batches: if self._session_factory is set, each job in a batch
        opens its own async session (session-per-coroutine) and is gathered with
        all other jobs in the same batch.  This is safe because:
          - load_shared_vars is a pure READ (no write contention)
          - persist_task_return writes to an independent row per job (no flush collision)

        Sequential fallback: if no session_factory (bare-session tests, no-DB mode),
        jobs run sequentially in scheduler-plan order.
        """
        from general_ludd.scheduling.scheduler import CycleError, Scheduler, WorkItem

        if not todos:
            return 0

        # Build WorkItems.  Default each todo to its own exclusive resource
        # (its todo_id) so nothing falsely parallelizes unless we can determine
        # otherwise.  Todos sharing the same queue share a resource label so the
        # scheduler correctly serializes queue-exclusive operations.
        items: list[WorkItem] = []
        for todo in todos:
            todo_id = str(_safe_str(todo, "todo_id", "") or id(todo))
            queue = _safe_str(todo, "queue", "core") or "core"
            # Use the todo_id as a unique resource so each job is self-exclusive
            # by default; add queue as a shared resource only when queue-exclusive
            # serialization is explicitly needed (opt-in via config).
            queue_exclusive = self._config_snapshot.get("scheduler_queue_exclusive", False)
            resources: frozenset[str] = (
                frozenset({f"queue:{queue}", f"todo:{todo_id}"})
                if queue_exclusive
                else frozenset({f"todo:{todo_id}"})
            )
            items.append(WorkItem(id=todo_id, resources=resources))

        # Build id→todo map for lookup.
        todo_map: dict[str, Any] = {}
        for todo in todos:
            tid = str(_safe_str(todo, "todo_id", "") or id(todo))
            todo_map[tid] = todo

        try:
            batches = Scheduler().plan(items)
        except (CycleError, ValueError) as exc:
            # Fail-closed on scheduler error: fall back to sequential.
            logger.warning("Scheduler.plan() failed (%s); falling back to sequential", exc)
            batches = [[str(_safe_str(t, "todo_id", "") or id(t))] for t in todos]

        dispatch_count = 0
        can_concurrent = self._session_factory is not None

        for batch_ids in batches:
            batch_todos = [todo_map[bid] for bid in batch_ids if bid in todo_map]
            if not batch_todos:
                continue

            if can_concurrent and len(batch_todos) > 1:
                # Concurrent: each coroutine opens its own session.
                logger.info(
                    "Scheduler batch: %d jobs concurrent (session-per-coroutine)",
                    len(batch_todos),
                )
                tasks = [
                    self._dispatch_execute_job_isolated(t)
                    for t in batch_todos
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, Exception):
                        logger.error("Concurrent job dispatch raised: %s", res)
                    else:
                        dispatch_count += 1
            elif can_concurrent and len(batch_todos) == 1:
                # Single job: still use isolated session for consistency.
                try:
                    await self._dispatch_execute_job_isolated(batch_todos[0])
                    dispatch_count += 1
                except Exception as exc:
                    logger.error("Job dispatch raised: %s", exc)
            else:
                # Sequential fallback (no session_factory).
                for todo in batch_todos:
                    try:
                        await self._dispatch_execute_job(todo)
                        dispatch_count += 1
                    except Exception as exc:
                        logger.error("Sequential job dispatch raised: %s", exc)

        return dispatch_count

    async def _dispatch_execute_job_isolated(self, todo: Any) -> None:
        """Dispatch a single execute job using its OWN async session.

        Opens a fresh session from self._session_factory for every DB interaction
        (load_shared_vars, persist_task_return) so this coroutine is safe to run
        concurrently with other _dispatch_execute_job_isolated calls in the same
        asyncio.gather() batch — no shared _active_session is touched.
        """
        assert self._session_factory is not None

        async with self._session_factory() as job_session:
            # Build a per-job variable_repo bound to this session.
            job_variable_repo = VariableNamespaceRepository(job_session)
            job_task_return_repo = TaskReturnRepository(job_session)
            await self._dispatch_execute_job(
                todo,
                _variable_repo_override=job_variable_repo,
                _task_return_repo_override=job_task_return_repo,
                _session_override=job_session,
            )
            try:
                await job_session.commit()
            except Exception as exc:
                logger.warning("Failed to commit isolated job session for %s: %s", getattr(todo, "todo_id", "?"), exc)

    def _get_rule_overrides_for_todo(self, todo: Any) -> dict[str, Any]:
        results = self._tick_state.get("rule_evaluation_results", [])
        for result in results:
            if not isinstance(result, dict):
                continue
            tid = result.get("todo_id", "")
            actual_tid = _safe_str(todo, "todo_id", "")
            if tid == actual_tid:
                actions = result.get("actions", [])
                if actions:
                    return apply_rule_actions(actions)
        return {}

    async def _dispatch_execute_job(
        self,
        todo: Any,
        *,
        _variable_repo_override: Any | None = None,
        _task_return_repo_override: Any | None = None,
        _session_override: AsyncSession | None = None,
    ) -> None:
        """Dispatch a single execute job.

        The ``_*_override`` keyword arguments are used by
        :meth:`_dispatch_execute_job_isolated` to inject per-coroutine repos/sessions
        so this method remains safe when called concurrently via asyncio.gather.
        When called without overrides (sequential path) it falls back to the shared
        instance attributes as before.
        """
        # Resolve which repos/session to use: per-job overrides (concurrent path)
        # or the shared tick-level ones (sequential fallback path).
        eff_variable_repo = (
            _variable_repo_override if _variable_repo_override is not None else self._variable_repo
        )
        eff_task_return_repo = (
            _task_return_repo_override if _task_return_repo_override is not None else self._task_return_repo
        )
        eff_session = _session_override if _session_override is not None else self._active_session

        budget_context: dict[str, Any] = {}
        if self._mcp_tool_registry is not None:
            budget_context["mcp_tools"] = self._mcp_tool_registry.tool_names()
        default_playbook = self._config_snapshot.get("default_playbook", "noop.yml")
        work_type = _safe_str(todo, "work_type", "code") or "code"
        project_id_val = (
            todo.project_id if hasattr(todo, "project_id") and isinstance(todo.project_id, str)
            else None
        )
        workspaces = self._project_workspace if isinstance(self._project_workspace, dict) else None
        ws = workspaces.get(project_id_val) if workspaces and project_id_val else None
        playbook = _playbook_for_work_type(
            work_type, default_playbook, project_id=project_id_val, workspaces=workspaces,
        )
        rule_overrides = self._get_rule_overrides_for_todo(todo)
        adaptive_prompt_id, adaptive_model_id, routing_decision = await self._resolve_adaptive_prompt(todo)
        if routing_decision is not None and not routing_decision.fallback:
            resolved_prompt_profile = adaptive_prompt_id or _safe_str(todo, "prompt_profile")
            resolved_model_profile = adaptive_model_id or _safe_str(todo, "model_profile")
        else:
            resolved_prompt_profile = _safe_str(todo, "prompt_profile")
            resolved_model_profile = _safe_str(todo, "model_profile")
        resolved_model_profile = rule_overrides.get("model_profile") or resolved_model_profile
        resolved_prompt_profile = rule_overrides.get("prompt_profile") or resolved_prompt_profile
        task_context = {
            "todo_title": _safe_str(todo, "title") or "",
            "todo_description": _safe_str(todo, "description") or "",
            "work_type": work_type,
            "queue": _safe_str(todo, "queue") or "core",
            "priority": str(getattr(todo, "priority", "medium") or "medium"),
        }
        project_templates_dir = (
            str(ws.templates_dir)
            if ws and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir()
            else None
        )
        prompt_text = _resolve_prompt_text_static(
            self._prompt_registry, resolved_prompt_profile,
            project_templates_dir=project_templates_dir, **task_context,
        )
        if prompt_text is None:
            prompt_text = self._resolve_prompt_text(todo)
        prompt_text = await self._append_message_queue_section(
            prompt_text, todo, project_id_val,
        )
        skill_body = self._resolve_skill_body(todo)
        # Load shared vars via the effective (possibly per-job) repo.
        shared_vars: dict[str, str] | None = None
        if eff_variable_repo is not None:
            try:
                shared_vars = await eff_variable_repo.load_vars_for_project(project_id_val)
            except Exception as exc:
                logger.warning("load_shared_vars failed for todo %s: %s", getattr(todo, "todo_id", "?"), exc)
        if self._runner is not None:
            job_id = f"EXEC-{todo.todo_id}"
            if ws is not None and hasattr(ws, "private_data_dir"):
                import os as _os
                job_dir = _os.path.join(str(ws.private_data_dir), job_id)
                _os.makedirs(job_dir, exist_ok=True)
                _os.makedirs(_os.path.join(job_dir, "env"), exist_ok=True)
                pdd = str(ws.private_data_dir)
            else:
                dirs = self._runner.prepare_job_dirs(job_id)
                pdd = dirs["root"]
            # C1 (W3.x): invoke the model for a generation work type the SAME
            # way the worker HTTP path does, then feed the generated text into
            # the playbook vars so the runner path is not a no-op generator.
            if self._model_gateway is not None and is_generation_work_type(
                _safe_str(todo, "work_type", "code") or "code"
            ):
                model_response = await asyncio.to_thread(
                    invoke_model_for_generation,
                    self._model_gateway,
                    job_id=job_id,
                    work_type=_safe_str(todo, "work_type", "code") or "code",
                    model_profile=resolved_model_profile,
                    prompt_text=prompt_text,
                    skill_body=skill_body,
                )
            else:
                model_response = None
            self._runner.write_vars(job_id, job_vars={
                "job_id": job_id, "todo_id": todo.todo_id,
                "queue": _safe_str(todo, "queue", "core"),
                "work_type": _safe_str(todo, "work_type", "unknown"),
                "model_profile": resolved_model_profile,
                "prompt_profile": resolved_prompt_profile,
                "prompt_text": prompt_text, "skill_body": skill_body,
                "model_response": model_response,
                "playbook": playbook, **budget_context,
            }, shared_vars=shared_vars)
            runner_env: dict[str, str] = {}
            if ws is not None and hasattr(ws, "roles_dir") and ws.roles_dir.is_dir():
                runner_env["ANSIBLE_ROLES_PATH"] = str(ws.roles_dir)
            if ws is not None and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir():
                runner_env["GLUDD_TEMPLATES_DIR"] = str(ws.templates_dir)
            # M9 (W3.3): run_playbook is a blocking I/O call; wrap in
            # asyncio.to_thread so the event loop stays responsive during
            # long playbook executions and CancelledError propagates cleanly.
            await asyncio.to_thread(
                self._runner.run_playbook,
                playbook_name=playbook,
                private_data_dir=pdd,
                env=runner_env,
            )
            return
        if self._http_client is None:
            return
        roles_path = (
            str(ws.roles_dir)
            if ws and hasattr(ws, "roles_dir") and ws.roles_dir.is_dir()
            else None
        )
        tpl_dir = (
            str(ws.templates_dir)
            if ws and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir()
            else None
        )
        job = JobSpec(
            job_id=f"EXEC-{todo.todo_id}", todo_id=todo.todo_id, playbook=playbook,
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type=_safe_str(todo, "work_type", "unknown") or "unknown",
            resource_profile=_safe_str(todo, "resource_profile", "low_resource") or "low_resource",
            model_profile=resolved_model_profile, prompt_profile=resolved_prompt_profile,
            plan_artifact=_safe_str(todo, "plan_artifact"),
            prompt_text=prompt_text, budget_context=budget_context,
            project_id=project_id_val,
            artifact_dir=str(ws.artifacts_dir) if ws and hasattr(ws, "artifacts_dir") else None,
            vars_namespace_refs=list(shared_vars.keys()) if shared_vars else [],
            ansible_roles_path=roles_path,
            templates_dir=tpl_dir,
        )
        resp = await self._http_client.post(
            f"{self.worker_base_url}/jobs/execute",
            json=job.model_dump(mode="json"),
        )
        await self._persist_task_return(
            todo, job, resp,
            _task_return_repo_override=eff_task_return_repo,
            _session_override=eff_session,
        )

    async def _dispatch_validate_job(self, todo: Any) -> None:
        if self._http_client is None:
            return
        job = JobSpec(
            job_id=f"VALIDATE-{todo.todo_id}", todo_id=todo.todo_id,
            playbook="validate_task.yml",
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type=_safe_str(todo, "work_type", "unknown") or "unknown",
            project_id=getattr(todo, "project_id", None),
        )
        resp = await self._http_client.post(
            f"{self.worker_base_url}/jobs/validate",
            json=job.model_dump(mode="json"),
        )
        logger.info("Validation dispatch for todo %s: status=%s", todo.todo_id, getattr(resp, "status_code", None))

    async def _persist_task_return(
        self,
        todo: Any,
        job: JobSpec,
        resp: Any,
        *,
        _task_return_repo_override: Any | None = None,
        _session_override: AsyncSession | None = None,
    ) -> None:
        eff_repo = _task_return_repo_override if _task_return_repo_override is not None else self._task_return_repo
        eff_session = _session_override if _session_override is not None else self._active_session
        if eff_repo is None:
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
            await eff_repo.create(data={
                "return_id": data.get("return_id", f"RET-{job.job_id}"),
                "todo_id": todo.todo_id, "job_id": job.job_id,
                "playbook": job.playbook, "queue": job.queue,
                "exit_code": data.get("exit_code", 0),
                "result_summary": data.get("result_summary", ""),
                "project_id": job.project_id,
            })
            if eff_session is not None:
                await eff_session.flush()
            logger.info("Persisted TaskReturn for todo %s", todo.todo_id)
        except Exception as exc:
            logger.warning("Failed to persist task return for %s: %s", todo.todo_id, exc)

    @staticmethod
    def _decision_id(d: Any) -> str:
        """Stable identity for a TaskDecision row.

        Prefer the persisted primary key; fall back to (return_id, matched_todo_id)
        so a decision with no surrogate id is still de-dupable. This id keys the
        F1 idempotency ledger so the same decision can never be applied twice.
        """
        raw_id = getattr(d, "id", None)
        if raw_id is not None:
            return f"id:{raw_id}"
        return f"ret:{getattr(d, 'return_id', '')}:todo:{getattr(d, 'matched_todo_id', '')}"

    async def _phase_reconcile_completed_decisions(self) -> None:
        if self._active_session is None or self._todo_repo is None:
            return
        stmt = select(TaskDecisionModel).order_by(TaskDecisionModel.created_at.desc()).limit(50)
        project_id = self._tick_project_id()
        if project_id is not None:
            stmt = stmt.where(TaskDecisionModel.project_id == project_id)
        result = await self._active_session.execute(stmt)
        decisions = list(result.scalars().all())
        reconciled = 0
        push_failures = 0
        for d in decisions:
            if not d.matched_todo_id:
                continue
            decision_id = self._decision_id(d)
            already_applied = decision_id in self._applied_decisions
            if already_applied:
                # F1: idempotent re-apply — never transition the same decision
                # twice. BUT a `complete` decision whose earlier push FAILED
                # (F3) is still status=COMPLETE-but-unpushed; that push must be
                # retried independently of the apply ledger (keyed on work id),
                # or the work would be silently lost.
                if (
                    d.decision == "complete"
                    and d.matched_todo_id not in self._pushed_work
                ):
                    todo = await self._todo_repo.get_by_id(d.matched_todo_id)
                    if todo is not None and await self._attempt_completed_push(todo):
                        push_failures += 1
                continue
            todo = await self._todo_repo.get_by_id(d.matched_todo_id)
            if todo is None or todo.status != TodoStatus.REVIEWING_RETURN.value:
                continue
            new_status = self._decision_to_status(d.decision)
            if new_status is None:
                continue
            # F6: version race. We transition with the version we just read; the
            # repository performs a guarded compare-and-set on (version, status).
            # If a CONCURRENT reconcile already moved the row, the CAS affects
            # zero rows and raises ConcurrencyError — we treat this stale
            # reconcile as a lost race: do NOT mark it applied, do NOT push, and
            # never overwrite the newer state. It simply skips this tick.
            try:
                await self._todo_repo.transition(todo.todo_id, new_status, todo.version)
            except ConcurrencyError as exc:
                logger.info(
                    "Reconcile lost version race for todo %s (decision %s): %s — "
                    "skipping stale reconcile",
                    todo.todo_id, decision_id, exc,
                )
                continue
            # Transition committed: this decision is now applied exactly once.
            self._applied_decisions.add(decision_id)
            reconciled += 1
            if (
                new_status == TodoStatus.COMPLETE
                and d.decision == "complete"
                and await self._attempt_completed_push(todo)
            ):
                push_failures += 1
            if self._audit_repo is not None:
                with contextlib.suppress(Exception):
                    from general_ludd.db.models import AuditEventType
                    await self._audit_repo.record_typed(
                        AuditEventType.TODO_STATUS_CHANGED,
                        entity_type="todo", entity_id=todo.todo_id,
                        project_id=todo.project_id,
                        details={
                            "old": todo.status, "new": new_status.value,
                            "decision": d.decision,
                        },
                    )
        self._tick_metrics["decisions_applied"] = reconciled
        self._tick_metrics["push_failures"] = push_failures

    async def _attempt_completed_push(self, todo: Any) -> bool:
        """Push completed work exactly once; return True if this attempt FAILED.

        F2: deduped by work (todo) id — a todo already pushed is a no-op, so the
        push fires exactly once across ticks (never twice).
        F3: a commit/push failure is surfaced (returned True + error log), and
        the work id is left OUT of the ledger so the push is RETRIED on a later
        tick rather than silently leaving status=COMPLETE-but-unpushed.
        """
        if todo.todo_id in self._pushed_work:
            logger.debug(
                "Completed work %s already pushed — skipping duplicate push",
                todo.todo_id,
            )
            return False
        try:
            await self._try_commit_completed_work(todo)
        except Exception as exc:
            logger.error(
                "Reconcile: completed-work push FAILED for %s "
                "(state diverged COMPLETE vs unpushed) — will retry: %s",
                todo.todo_id, exc,
            )
            return True
        self._pushed_work.add(todo.todo_id)
        return False

    def _decision_to_status(self, decision: str) -> TodoStatus | None:
        mapping: dict[str, TodoStatus] = {
            "complete": TodoStatus.COMPLETE,
            "needs_more_work": TodoStatus.NEEDS_MORE_WORK,
            "failed": TodoStatus.FAILED, "blocked": TodoStatus.BLOCKED,
            "manual_hold": TodoStatus.MANUAL_HOLD,
        }
        return mapping.get(decision)

    async def _try_commit_completed_work(self, todo: Any) -> None:
        """H6: commit/branch/push completed work via git automation."""
        branch_name = getattr(todo, "branch_name", None) or f"gludd-{todo.todo_id.lower()}"
        worktree = getattr(todo, "worktree", None)
        if worktree:
            try:
                from general_ludd.git_automation.repo import GitAutomation
                repo = GitAutomation(worktree)
                # M (LIVE stall fix): commit/push shell out to blocking git.
                # Even with a per-subprocess timeout, a 60s blocking call inside
                # the async tick would freeze every other coroutine. Offload to a
                # worker thread (mirrors the playbook dispatch's asyncio.to_thread)
                # so blocking git can never stall the event loop.
                await asyncio.to_thread(
                    repo.commit, f"[{todo.todo_id}] {todo.title}"
                )
                await asyncio.to_thread(repo.push, branch=branch_name)
                logger.info("H6: committed + pushed %s to %s", todo.todo_id, branch_name)
                self._maybe_open_pr(todo, worktree, branch_name)
            except Exception as exc:
                # F3: surface (don't swallow) so the caller can detect the
                # COMPLETE-but-unpushed split-brain and retry. The caller decides
                # whether to mark the work pushed — a raised failure leaves it
                # unmarked so the push is retried, never silently lost.
                logger.warning("H6: git automation failed for %s: %s", todo.todo_id, exc)
                raise

    def _maybe_open_pr(self, todo: Any, worktree: str, branch_name: str) -> None:
        """F1: open a PR for completed work when git_automation.open_pr is set.

        Disabled by default — only fires when config opts in, so the daemon
        never opens PRs unexpectedly. Uses PRDelivery (push branch + gh pr
        create).
        """
        ga_cfg = self.config.get("git_automation", {}) or {}
        if not ga_cfg.get("open_pr", False):
            return
        from general_ludd.git_automation.pr_delivery import PRDelivery

        delivery = PRDelivery(
            base_branch=str(ga_cfg.get("base_branch", "main")),
            draft=bool(ga_cfg.get("pr_draft", False)),
            labels=list(ga_cfg.get("pr_labels", [])),
        )
        result = delivery.push_and_create_pr(
            repo_path=worktree,
            branch_name=branch_name,
            todo_id=todo.todo_id,
            title=getattr(todo, "title", todo.todo_id),
        )
        if result.get("error"):
            logger.warning("F1: PR delivery for %s failed: %s", todo.todo_id, result["error"])
        else:
            logger.info("F1: opened PR for %s: %s", todo.todo_id, result.get("pr_url"))

    async def _phase_self_improve(self) -> None:
        interval = self._self_improve_interval
        if interval <= 0:
            return
        if self._total_ticks % interval != 0:
            return
        try:
            from general_ludd.self_improve.harness import SelfImprovementHarness
            harness = SelfImprovementHarness()
            findings = harness.run_gap_analysis()
            if not findings:
                self._tick_metrics["self_improve_gaps"] = 0
                return
            todos = harness.generate_fix_todos(findings)
            # H2 (W3.7): persist through TodoRepository so the generated work
            # survives the tick (the in-memory harness.enqueue_todos discarded
            # them). Fall back to nothing if no repo/session is available.
            enqueued = await self._persist_self_improve_todos(todos)
            if self._daemon_state is not None:
                self._daemon_state["self_improve_last_analysis"] = {
                    "findings": findings, "findings_count": len(findings),
                    "todos_enqueued": enqueued,
                }
            self._tick_metrics["self_improve_gaps"] = len(findings)
            self._tick_metrics["self_improve_todos_persisted"] = enqueued
            logger.info("Self-improve cycle: %d gaps found, %d todos persisted", len(findings), enqueued)
        except Exception as exc:
            logger.warning("Self-improve phase failed: %s", exc)
            self._tick_metrics["self_improve_gaps"] = 0

    _PRIORITY_MAP: ClassVar[dict[str, int]] = {
        "low": 0, "medium": 5, "high": 10, "critical": 20,
    }

    async def _persist_self_improve_todos(self, todos: list[dict[str, Any]]) -> int:
        if self._todo_repo is None or self._active_session is None:
            return 0
        # Admission gate (W3.7): cap how many self-improve todos may be open at
        # once and decide each admitted todo's initial status (default: a human
        # gate via approval_required rather than auto-queuing self-generated work).
        from general_ludd.self_improve.gate import SelfImproveGate

        si_cfg = self.config.get("self_improve", {}) if isinstance(self.config, dict) else {}
        if not isinstance(si_cfg, dict):
            si_cfg = {}
        gate = SelfImproveGate(
            max_open=si_cfg.get("max_open", 10),
            auto_queue=si_cfg.get("auto_queue", False),
        )
        terminal = {
            TodoStatus.COMPLETE.value,
            TodoStatus.FAILED.value,
            TodoStatus.CANCELLED.value,
        }
        existing = await self._todo_repo.list_by_work_type("self_improve")
        open_count = sum(
            1 for t in existing if _safe_str(t, "status") not in terminal
        )
        persisted = 0
        for todo in todos:
            decision = gate.evaluate(todo, open_count=open_count)
            if not decision.admitted:
                continue
            priority_raw = todo.get("priority", "high")
            if isinstance(priority_raw, int):
                priority = priority_raw
            else:
                priority = self._PRIORITY_MAP.get(str(priority_raw).lower(), 10)
            payload: dict[str, Any] = {
                "title": str(todo.get("title", "Self-improvement task"))[:512],
                "description": str(todo.get("description", "")),
                "status": decision.initial_status,
                "work_type": "self_improve",
                "priority": priority,
                "created_by": "self_improve_harness",
            }
            try:
                await self._todo_repo.create(payload)
                persisted += 1
                open_count += 1
            except Exception as exc:
                logger.warning("Failed to persist self-improve todo: %s", exc)
        if persisted:
            with contextlib.suppress(Exception):
                await self._active_session.flush()
        return persisted

    async def _phase_emit_tick_metrics(self) -> None:
        logger.info("Tick metrics: %s", self._tick_metrics)

    async def dispatch_return_review(self, task_return: TaskReturn) -> dict[str, Any]:
        if task_return.status != TaskReturnStatus.CREATED:
            return {"status": "skipped", "reason": "not_created"}
        job = JobSpec(
            job_id=f"REVIEW-{task_return.return_id}", return_id=task_return.return_id,
            todo_id=task_return.todo_id, playbook="return_review.yml",
            queue="model", work_type="review", resource_profile="ai_heavy",
        )
        logger.info("Dispatching return review for %s", task_return.return_id)
        return {"status": "dispatched", "job_id": job.job_id}

    async def claim_runnable_todos(self, todos: list[Todo]) -> list[Todo]:
        runnable = [t for t in todos if t.status == TodoStatus.QUEUED]
        return runnable

    async def reconcile_decision(self, decision: TaskDecision, todo: Todo) -> Todo:
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
