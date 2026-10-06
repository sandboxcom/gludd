"""Tick lifecycle, session boundaries, and inbound queue handling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue as _stdqueue
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from general_ludd.db.repository import (
    AuditEventRepository,
    TaskReturnRepository,
    TodoRepository,
    VariableNamespaceRepository,
)
from general_ludd.db.tenant import reset_tenant as _reset_tenant
from general_ludd.db.tenant import set_tenant as _set_tenant
from general_ludd.event_loop import task_routing as _task_routing
from general_ludd.event_loop.execution_supervision import ExecutionLeaseSupervisor
from general_ludd.event_loop.review_orchestration import is_managed_self_improve_todo as _is_managed_self_improve_todo
from general_ludd.event_loop.review_orchestration import safe_string_attribute as _safe_str
from general_ludd.schemas.benchmark import TaskType
from general_ludd.schemas.todo import TodoStatus

if TYPE_CHECKING:
    from general_ludd.decision_codification.service import DecisionCodificationAdapter
    # Mixin state is supplied by EventLoop, the sole mutable owner.
    _DynamicState = Any

logger = logging.getLogger(__name__)
_WORK_TYPE_TASK_TYPE_MAP = _task_routing.WORK_TYPE_TASK_TYPE_MAP
_ROUTING_TASK_TYPE = TaskType
def _work_type_to_task_type(work_type: str) -> TaskType:
    """Map through compatibility globals retained for existing patch points."""
    if (
        _WORK_TYPE_TASK_TYPE_MAP is _task_routing.WORK_TYPE_TASK_TYPE_MAP
        and TaskType is _ROUTING_TASK_TYPE
    ):
        return _task_routing.work_type_to_task_type(work_type)
    mapped = _WORK_TYPE_TASK_TYPE_MAP.get(work_type, "feature")
    try:
        return TaskType(mapped)
    except ValueError:
        return TaskType.FEATURE
PHASE_ORDER = [
    "load_config_snapshot",
    "evaluate_pid_controllers",
    "refill_task_buckets",
    "run_scheduler",
    "self_improve",
    "poll_issue_sources",
    "sdlc_gate",
    "claim_unreviewed_task_returns",
    "dispatch_return_review_jobs",
    "claim_runnable_todos",
    "evaluate_rules",
    "reconcile_compute_demand",
    "dispatch_execute_jobs",
    "reconcile_completed_decisions",
    "release_compute_demand",
    "refresh_model_performance",
    "check_compute_utilization",
    "check_service_credits",
    "flush_spend_ledger",
    "remediate_blocked_tasks",
    "consolidate_memory",
    "service_discovery",
    "reap_expired_sts_tokens",
    "purge_old_task_decisions",
    "emit_tick_metrics",
]
PROVISION_PHASE_INDEX = PHASE_ORDER.index("reconcile_compute_demand")
DISPATCH_PHASE_INDEX = PHASE_ORDER.index("dispatch_execute_jobs")
RELEASE_PHASE_INDEX = PHASE_ORDER.index("release_compute_demand")

class TickLifecycleMixin:
    """Tick lifecycle, session boundaries, and inbound queue handling."""

    _decision_codification: DecisionCodificationAdapter | None
    _todo_repo: TodoRepository | None
    _task_return_repo: TaskReturnRepository | None
    _audit_repo: AuditEventRepository | None
    _variable_repo: VariableNamespaceRepository | None
    _push_retry_count: dict[str, int]
    _total_ticks: int
    _last_ansible_env_project_id: str | None

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any:
            """Type-only access to state owned by the facade."""
            ...

    @property
    def decision_codification(self) -> DecisionCodificationAdapter | None:
        """Return the immutable opt-in decision-codification boundary."""
        return self._decision_codification

    def _track_background_task(self, task: asyncio.Task[None]) -> None:
        """Register a fire-and-forget task so its reference is held until done.

        A3: keeps a strong reference in ``self._background_tasks`` (so the task
        is never GC'd while running) and arms a done-callback that discards it on
        completion (so the set drains to empty and never leaks). ``set.add`` and
        ``set.discard`` are each atomic under the GIL, and ``add_done_callback``
        fires immediately if the task is already complete — so this is safe to
        call from any coroutine without a lock. The lock guards only the COMPOUND
        shutdown drain in :meth:`_drain_background_tasks`.
        """
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _drain_background_tasks(self, *, cancel: bool = True) -> None:
        """Cancel + await all tracked background tasks (used on shutdown).

        A3: iterates a SNAPSHOT (``list(self._background_tasks)``) taken under
        ``_bg_tasks_lock`` so a concurrent done-callback ``discard`` cannot mutate
        the set while we read it — the read-modify-await sequence here is the only
        compound access to the set and is the one that genuinely needs the lock.
        Cancellation is best-effort: each task's done-callback removes it from the
        live set as it settles, so after the gather the set drains to empty.
        """
        while True:
            async with self._bg_tasks_lock:
                pending = list(self._background_tasks)
            if not pending:
                return
            if cancel:
                for task in pending:
                    if not task.done():
                        task.cancel()
            # A cancelled task may schedule final cleanup work while settling.
            # Gather and remove this stable snapshot, then loop so that cleanup
            # tasks registered during cancellation cannot escape shutdown.
            await asyncio.gather(*pending, return_exceptions=True)
            async with self._bg_tasks_lock:
                self._background_tasks.difference_update(pending)

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
                logger.warning(
                    "MQ inbox lookup failed for role %r (project %s): falling back to empty inbox",
                    role,
                    project_id,
                    exc_info=True,
                )
                unread = 0
                senders = []
        from general_ludd.prompts.registry import render_message_queue_section

        section = render_message_queue_section(role=role, unread_count=unread, senders=senders, enabled=True)
        if not section:
            return prompt_text
        return f"{prompt_text}\n\n{section}" if prompt_text else section

    async def _build_memory_section(self, prompt_text: str | None, todo: Any) -> str | None:
        if self._memory_repo is None:
            return prompt_text
        assigned_agent = _safe_str(todo, "assigned_agent") or _safe_str(todo, "work_type") or "agent"
        try:
            records = await self._memory_repo.list_by_namespace(
                agent_id=assigned_agent,
                namespace="default",
                limit=50,
            )
        except Exception:
            logger.warning(
                "Memory lookup failed for agent %r; skipping memory section",
                assigned_agent,
                exc_info=True,
            )
            return prompt_text
        if not records:
            return prompt_text
        lines = ["## Agent Memory"]
        for r in records:
            lines.append(f"- **{r.key}**: {r.value}")
        section = "\n".join(lines)
        return f"{prompt_text}\n\n{section}" if prompt_text else section

    async def _resolve_adaptive_prompt(
        self, todo: Any, default_model_profile: str = "default"
    ) -> tuple[str | None, str | None, Any | None]:
        if self._adaptive_router is None:
            logger.warning("_adaptive_router not initialized; skipping adaptive prompt routing")
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
        """Classify stale ACTIVE todos without inferring process termination.

        ``updated_at`` and lease expiry are liveness hints, never terminal proof.
        The durable lease recovery handshake requests cancellation and performs
        the only safe requeue after exact-owner termination confirmation. This
        detector therefore emits state for unfenced legacy work but never starts
        a second execution merely because a timestamp went quiet.
        """
        if self._active_session is None or self._todo_repo is None:
            return
        try:
            from general_ludd.db.models import BucketLeaseModel, TodoModel

            cutoff = datetime.now(UTC) - timedelta(
                minutes=self._stuck_timeout_minutes
            )
            stmt = (
                select(TodoModel)
                .where(TodoModel.status == TodoStatus.ACTIVE.value)
                .where(TodoModel.updated_at < cutoff)
            )
            result = await self._active_session.execute(stmt)
            candidates = list(result.scalars().all())
            if not candidates:
                return

            bucket_keys = [
                f"{_safe_str(t, 'queue', 'core')}:{_safe_str(t, 'todo_id', '')}"
                for t in candidates
            ]
            lease_stmt = (
                select(BucketLeaseModel.bucket_key)
                .where(BucketLeaseModel.bucket_key.in_(bucket_keys))
            )
            lease_result = await self._active_session.execute(lease_stmt)
            leased_bucket_keys: set[str] = set(lease_result.scalars().all())
            unfenced: set[str] = set()
            recovery_pending: set[str] = set()
            for todo in candidates:
                queue = _safe_str(todo, "queue", "core") or "core"
                todo_id = _safe_str(todo, "todo_id", "") or ""
                bucket_key = f"{queue}:{todo_id}"
                if bucket_key in leased_bucket_keys:
                    recovery_pending.add(todo_id)
                elif (
                    getattr(todo, "work_type", None) == "self_improve"
                    and not _is_managed_self_improve_todo(todo)
                ):
                    await self._todo_repo.transition(
                        todo_id,
                        TodoStatus.FAILED,
                        todo.version,
                    )
                else:
                    unfenced.add(todo_id)
            if recovery_pending:
                self._tick_state["lease_recovery_pending_todo_ids"] = (
                    recovery_pending
                )
            if unfenced:
                self._tick_state["unfenced_stuck_todo_ids"] = unfenced
                logger.warning(
                    "Detected %d stale ACTIVE todos without terminal proof; requeue denied",
                    len(unfenced),
                )
        except Exception as exc:
            logger.warning("Stuck-todo reaper failed: %s", exc)

    async def tick(self) -> dict[str, Any]:
        """Run one complete tick with exclusive ownership of tick state."""
        async with self._tick_lock:
            return await self._tick_once()

    def _restore_tick_checkpoint(self) -> None:
        """Restore durable state before running a new tick, when available."""
        if self._checkpointer is None:
            return
        previous = self._checkpointer.get("last_tick")
        if not previous:
            return
        self._tick_state = previous.get("_tick_state", {})
        for key in previous.get("_applied_decision_keys", []):
            if key not in self._applied_decisions:
                self._applied_decisions[key] = None
        for key in previous.get("_pushed_work_keys", []):
            if key not in self._pushed_work:
                self._pushed_work[key] = None
        self._push_retry_count = previous.get(
            "_push_retry_count", self._push_retry_count
        )

    def _record_tick_completion(self, tick_id: str, started_at: float) -> None:
        """Publish final tick metrics and persist the durable checkpoint."""
        elapsed = time.monotonic() - started_at
        self._tick_metrics["tick_duration_ms"] = elapsed * 1000
        if self._daemon_state is not None:
            self._daemon_state["tick_metrics"] = dict(self._tick_metrics)
        if self._checkpointer is not None:
            state = {
                "_tick_state": dict(self._tick_state),
                "_applied_decision_keys": list(self._applied_decisions.keys()),
                "_pushed_work_keys": list(self._pushed_work.keys()),
                "_push_retry_count": dict(self._push_retry_count),
            }
            self._checkpointer.put(tick_id, state)
            self._checkpointer.put("last_tick", state)

    async def _tick_once(self) -> dict[str, Any]:
        self._tick_state = {}
        self._total_ticks += 1
        tick_id = f"tick_{self._total_ticks}"
        self._tick_metrics = {
            "total_ticks": self._total_ticks,
            "phases_completed": 0,
            "tick_duration_ms": 0.0,
            "returns_reviewed": 0,
            "todos_dispatched": 0,
            "decisions_applied": 0,
            "leases_reclaimed": 0,
        }
        self._restore_tick_checkpoint()
        # M14 (W3.14): select ONE project per tick before phases run; reset after.
        self._tick_project_id = self._select_tick_project_id()
        # C.3: propagate tenant context into thread-pool workers so sessions
        # created inside asyncio.to_thread carry the project_id filter.
        _tenant_token = _set_tenant(self._tick_project_id)
        start = time.monotonic()
        try:
            needs_own_session = self.session is None and self._session_factory is not None
            if needs_own_session:
                assert self._session_factory is not None
                async with self._session_factory() as session:
                    self._active_session = session
                    self._todo_repo = TodoRepository(session)
                    self._task_return_repo = TaskReturnRepository(session)
                    self._audit_repo = AuditEventRepository(session)
                    self._variable_repo = VariableNamespaceRepository(session)
                    # S83.158/E10: persist the claim and its execution leases,
                    # then close the transaction before provisioning.  External
                    # lifecycle work must never hold the database writer lock.
                    await self._run_phase_range(0, PROVISION_PHASE_INDEX)
                    claim_commit_succeeded = await self._commit_tick_session(session)
                    self._clear_repos()
                # Provision and dispatch with NO tick session held. Isolated
                # per-job sessions are opened inside the dispatcher.
                if claim_commit_succeeded is False:
                    # A rolled-back claim is not durable demand.  Discard the
                    # detached ORM batch so it can neither provision compute nor
                    # dispatch work that another tick is still free to claim.
                    self._tick_state["claimed_todos"] = []
                    self._tick_state["compute_ready"] = False
                    self._tick_state["compute_demand"] = {
                        "state": "claim_commit_failed",
                        "runnable_todos": 0,
                        "execution_environment": "unchanged",
                    }
                    self._tick_metrics["claim_commit_failures"] = 1
                    logger.error(
                        "Compute provisioning skipped because the claim transaction "
                        "did not commit"
                    )
                else:
                    await self._run_phase_range(
                        PROVISION_PHASE_INDEX,
                        DISPATCH_PHASE_INDEX,
                    )
                    await self._run_phase_range(
                        DISPATCH_PHASE_INDEX,
                        DISPATCH_PHASE_INDEX + 1,
                    )
                assert self._session_factory is not None
                for phase_idx in range(DISPATCH_PHASE_INDEX + 1, len(PHASE_ORDER)):
                    if phase_idx == RELEASE_PHASE_INDEX:
                        # The terminal-decision phase immediately before this
                        # one has already committed and closed.  The release
                        # phase performs its own short durable-demand read and
                        # closes that read before touching external compute.
                        self._clear_repos()
                        await self._run_phase_range(phase_idx, phase_idx + 1)
                        continue
                    async with self._session_factory() as session:
                        self._active_session = session
                        self._todo_repo = TodoRepository(session)
                        self._task_return_repo = TaskReturnRepository(session)
                        self._audit_repo = AuditEventRepository(session)
                        self._variable_repo = VariableNamespaceRepository(session)
                        await self._run_phase_range(phase_idx, phase_idx + 1)
                        await self._commit_tick_session(session)
                    self._clear_repos()
            else:
                if self.session is not None:
                    self._active_session = self.session
                    self._todo_repo = self._todo_repo or TodoRepository(self.session)
                    self._task_return_repo = self._task_return_repo or TaskReturnRepository(self.session)
                    self._audit_repo = self._audit_repo or AuditEventRepository(self.session)
                    self._variable_repo = self._variable_repo or VariableNamespaceRepository(self.session)
                await self._run_phases()
                self._active_session = None
        finally:
            # M14 (W3.14): always reset tick-scoped project selection after the tick.
            self._tick_project_id = None
            # C.3: clear tenant context after tick so thread workers in
            # subsequent ticks do not inherit a stale project_id.
            _reset_tenant(_tenant_token)
        self._record_tick_completion(tick_id, start)
        return self._tick_metrics

    async def _run_phases(self) -> None:
        await self._run_phase_range(0, len(PHASE_ORDER))

    async def _run_phase_range(self, start: int, end: int) -> None:
        for phase_name in PHASE_ORDER[start:end]:
            phase_fn = getattr(self, f"_phase_{phase_name}")
            try:
                logger.info("Phase started: %s", phase_name)
                await phase_fn()
                logger.info("Phase completed: %s", phase_name)
                self._tick_metrics["phases_completed"] += 1
            except Exception as exc:
                logger.error("Phase %s raised %s: %s", phase_name, type(exc).__name__, exc)

    def _clear_repos(self) -> None:
        self._active_session = None
        self._todo_repo = None
        self._task_return_repo = None
        self._audit_repo = None
        self._variable_repo = None

    async def _commit_tick_session(self, session: Any) -> bool:
        try:
            await session.commit()
            return True
        except Exception as exc:
            logger.error(
                "Failed to commit tick session (writes lost): %s",
                exc,
                exc_info=True,
            )
            with contextlib.suppress(Exception):
                await session.rollback()
            return False

    async def _bounded_to_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._to_thread_semaphore:
            return await asyncio.to_thread(fn, *args, **kwargs)

    @staticmethod
    async def _await_terminal_task(task: asyncio.Task[Any]) -> Any:
        """Drain a shielded cleanup task despite repeated caller cancellation."""
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.done():
                    return task.result()

    async def _run_playbook_with_lease_supervision(
        self,
        supervisor: ExecutionLeaseSupervisor,
        *,
        playbook: str,
        private_data_dir: str,
        env: dict[str, str],
    ) -> Any:
        """Run blocking Ansible work without abandoning it on task cancellation."""
        assert self._runner is not None
        runner_task = asyncio.create_task(
            self._bounded_to_thread(
                self._runner.run_playbook,
                playbook_name=playbook,
                private_data_dir=private_data_dir,
                env=env,
                cancel_requested=supervisor.is_cancellation_requested,
            )
        )
        try:
            return await asyncio.shield(runner_task)
        except asyncio.CancelledError as cancellation:
            request_task = asyncio.create_task(supervisor.request_cancellation())
            with contextlib.suppress(Exception):
                await self._await_terminal_task(request_task)

            runner_failure: BaseException | None = None
            try:
                await self._await_terminal_task(runner_task)
            except BaseException as exc:
                runner_failure = exc
                logger.error(
                    "OWNED_EXECUTION_REAP status=failed exception_type=%s",
                    type(exc).__name__,
                )

            confirmation_task = asyncio.create_task(supervisor.confirm_termination())
            with contextlib.suppress(Exception):
                await self._await_terminal_task(confirmation_task)
            if runner_failure is not None:
                raise cancellation from runner_failure
            raise

    async def run_forever(self, interval: float = 1.0) -> None:
        """Run observable ticks until :meth:`stop` is called."""
        self._running = True
        # B3.1.5: re-hydrate crash-interrupted dispatches before the tick
        # loop starts so a writer restart does not lose in-flight work.
        with contextlib.suppress(Exception):
            await self._resume_interrupted_dispatches()
        try:
            while self._running:
                await self.tick()
                if self._inbound_queue is not None:
                    await self._drain_inbound_queue()
                if not self._running:
                    break
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=interval)
                except TimeoutError:
                    pass
                finally:
                    self._wake_event.clear()
        except Exception as exc:
            logger.error("EventLoop run_forever exited with error: %s", exc)
            raise
        finally:
            if self._running:
                logger.error("EventLoop run_forever stopped unexpectedly")
            else:
                logger.info("EventLoop run_forever stopped gracefully")

    async def _drain_inbound_queue(self) -> None:
        """B3.1.3 Slice 5: drain the inbound :class:`WriteQueue` between ticks.

        Non-blocking: pulls every currently-buffered envelope via
        :meth:`WriteQueue.get_nowait` and returns the moment the queue is
        empty (``queue.Empty``). Each envelope is applied inside its own
        freshly-opened DB session so a failure in one envelope rolls back
        ONLY that envelope's writes; subsequent envelopes still commit.

        In no-DB mode (``self._session_factory is None``) envelopes are
        dropped (logged) — the queue still empties, so the producer is never
        blocked by a consumer that has no DB to apply to.
        """
        if self._inbound_queue is None:
            return
        while True:
            try:
                envelope = self._inbound_queue.get_nowait()
            except _stdqueue.Empty:
                return
            if self._session_factory is None:
                logger.warning(
                    "Dropping inbound envelope (no DB session factory): topic=%s payload_keys=%s",
                    envelope.topic,
                    list(envelope.payload.keys()),
                )
                continue
            try:
                async with self._session_factory() as session:
                    try:
                        await self._apply_envelope(envelope, session)
                        await session.commit()
                    except Exception:
                        logger.exception(
                            "Failed to apply inbound envelope: topic=%s",
                            envelope.topic,
                        )
                        with contextlib.suppress(Exception):
                            await session.rollback()
            except Exception:
                # The session factory itself blew up (rare); log + continue so
                # one bad envelope does not kill the drain loop.
                logger.exception(
                    "Session factory error during inbound drain: topic=%s",
                    envelope.topic,
                )

    async def _apply_envelope(self, envelope: Any, session: Any) -> None:
        """B3.1.3 Slice 5 STUB envelope handler.

        Routes by ``envelope.topic`` to the appropriate repository method.
        This is a deliberately thin stub — real per-topic handlers (full
        upsert semantics, validation, audit-event emission) land in later
        slices. Today only ``todo.upsert`` is routed; unknown topics are
        logged and otherwise ignored so the drain never crashes on a
        future-introduced topic it doesn't yet know about.
        """
        topic = envelope.topic
        payload = envelope.payload
        if topic == "todo.upsert":
            if self._todo_repo is not None:
                await self._todo_repo.create(payload)
        else:
            logger.info(
                "No stub handler for inbound envelope topic=%s; ignoring",
                topic,
            )
        logger.info("Applied inbound envelope: topic=%s", topic)

    def wake(self) -> None:
        """Interrupt the idle interval so newly available work is claimed now."""
        self._wake_event.set()

    def stop(self) -> None:
        """Request a graceful stop and wake a sleeping loop."""
        self._running = False
        self.wake()

    async def shutdown(self) -> None:
        """Stop ticking and drain all in-flight background tasks.

        A3: cancels + awaits the fire-and-forget benchmark/trace tasks so the
        process does not exit with running tasks (which would emit
        "Task was destroyed but it is pending" warnings and could lose telemetry
        writes mid-flush). Safe to call concurrently with task creation — the
        drain reads a snapshot under ``_bg_tasks_lock``.
        """
        self._running = False
        self.wake()
        await self._drain_background_tasks()

    def get_available_tools(self) -> list[str]:
        """Return the names of currently registered MCP tools."""
        if self._mcp_tool_registry is None:
            return []
        return cast(list[str], self._mcp_tool_registry.tool_names())

    async def _phase_load_config_snapshot(self) -> None:
        import copy

        self._config_snapshot = copy.deepcopy(self.config)
        if self._variable_repo is not None and self._active_session is not None:
            shared_vars = await self._variable_repo.load_vars_for_project(None)
            if shared_vars:
                self._config_snapshot["shared_vars"] = shared_vars

    def _select_tick_project_id(self) -> str | None:
        """M14 (W3.14): select ONE project per tick and return its id.

        Called once at the start of tick(); the result is stored in
        self._tick_project_id and shared across all phases within that tick.
        All phases must read self._tick_project_id directly — never call
        select_project() independently inside a phase.
        """
        if self._project_manager is None:
            return None
        project = self._project_manager.select_project()
        project_id = project.project_id if project is not None else None
        # Rebuild the Ansible collections env when the active project changes
        # so the next playbook run uses the new project's .gludd/collections/.
        if project_id != self._last_ansible_env_project_id:
            self._rebuild_ansible_env_for_project(project_id)
            self._last_ansible_env_project_id = project_id
        return project_id

    def _resolve_project_root_for_collections(self, project_id: str | None) -> str | None:
        """Resolve the root containing project-local Ansible collections.

        Looks up the per-project workspace (``self._project_workspace``) and
        returns its ``repo_dir`` (where the project repo — and its
        ``.gludd/`` — is cloned). Falls back to the workspace ``root`` when
        ``repo_dir`` is unavailable. Returns ``None`` when no project is
        selected or no workspace is registered — the resolver then yields
        bundled-only.
        """
        if project_id is None:
            return None
        workspaces = self._project_workspace if isinstance(self._project_workspace, dict) else None
        if not workspaces or project_id not in workspaces:
            return None
        ws = workspaces[project_id]
        # repo_dir is where the project repo (with its .gludd/) lives.
        repo_dir = getattr(ws, "repo_dir", None)
        if repo_dir is not None:
            try:
                return str(repo_dir)
            except Exception:
                pass
        # Fallback: workspace root (test/standalone setups may host .gludd/
        # directly at the workspace root rather than under repo/).
        root = getattr(ws, "root", None)
        return str(root) if root is not None else None

    def _rebuild_ansible_env_for_project(self, project_id: str | None) -> None:
        """Re-resolve Ansible collections paths for a new active project.

        Called when ``_select_tick_project_id`` detects the active project has
        changed. Resolves the new project's root, re-runs the 3-tier resolver,
        and invokes the daemon-supplied ``_ansible_env_updater`` callback so
        ``app.state._ansible_env`` / ``app.state._collections_paths`` and the
        adapter's ``_default_env`` are all rebound. Also updates the local
        adapter directly when no callback is wired (test/standalone use).
        """
        from general_ludd.ansible.paths import (
            resolve_collections_paths,
            to_ansible_env,
        )

        project_root = self._resolve_project_root_for_collections(project_id)
        entries = resolve_collections_paths(project_root)
        env = to_ansible_env(entries)
        if self._ansible_env_updater is not None:
            try:
                self._ansible_env_updater(entries, env)
            except Exception as exc:
                logger.warning(
                    "ansible_env_updater callback failed for project %s: %s",
                    project_id,
                    exc,
                )
            if self._runner is not None:
                with contextlib.suppress(Exception):
                    self._runner._default_env = dict(env)
        logger.info(
            "Rebuilt Ansible collections env for project %s (%d tier(s))",
            project_id,
            len(entries),
        )

    def _estimated_dispatch_cost(self, item_count: int) -> float:
        budget_cfg = self.config.get("budget", {}) if isinstance(self.config, dict) else {}
        per_job = budget_cfg.get("per_dispatch_usd", 0.01) if isinstance(budget_cfg, dict) else 0.01
        try:
            return float(per_job) * max(0, int(item_count))
        except (TypeError, ValueError):
            return 0.0
