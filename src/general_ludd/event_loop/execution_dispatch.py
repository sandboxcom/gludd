"""Concurrent execution dispatch and sandbox lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.agents.hibernation import AgentEnvironmentSnapshot, DispatchState
from general_ludd.compaction.aggressive import level_at as _level_at
from general_ludd.db.repository import TaskReturnRepository, TodoRepository, VariableNamespaceRepository
from general_ludd.event_loop import task_routing as _task_routing
from general_ludd.event_loop.execution_supervision import (
    ExecutionLeaseIdentity,
    ExecutionLeaseSupervisor,
    OwnedExecutionCancelled,
)
from general_ludd.event_loop.lease import LeaseRenewalStatus, release_lease
from general_ludd.event_loop.review_orchestration import safe_string_attribute as _safe_str
from general_ludd.event_loop.runtime_helpers import runtime_lease_bucket_keys as _runtime_lease_bucket_keys
from general_ludd.event_loop.runtime_helpers import runtime_work_identity as _runtime_work_identity
from general_ludd.event_loop.runtime_helpers import todo_dependency_ids as _todo_dependency_ids
from general_ludd.execution.situation_store import BadCallSituationStore
from general_ludd.execution.tool_auditor import ToolCallAuditor
from general_ludd.models.job_invocation import invoke_model_for_generation, is_generation_work_type
from general_ludd.observability.timing import default_tracker
from general_ludd.rules.engine import apply_rule_actions
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.project_identity import ProjectWorkIdentity
from general_ludd.schemas.todo import TodoStatus

if TYPE_CHECKING:
    # Mixin state is supplied by EventLoop, the sole mutable owner.
    _DynamicState = Any

logger = logging.getLogger(__name__)
_TOOL_USE_WORK_TYPES = _task_routing.TOOL_USE_WORK_TYPES
_format_acceptance_criteria = _task_routing.format_acceptance_criteria
_playbook_for_work_type = _task_routing.playbook_for_work_type
_resolve_prompt_text_static = _task_routing.resolve_prompt_text_static
_self_update_work_item_from_todo = _task_routing.self_update_work_item_from_todo

class ExecutionDispatchMixin:
    """Concurrent execution dispatch and sandbox lifecycle."""

    _compaction_level: int | None

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any:
            """Type-only access to state owned by the facade."""
            ...

    async def _phase_dispatch_execute_jobs(self) -> None:
        claimed = list(self._tick_state.get("claimed_todos", []))
        if claimed and self._tick_state.get("compute_ready") is False:
            logger.error(
                "Execute dispatch deferred: claimed work has no ready execution environment"
            )
            self._tick_metrics["todos_dispatched"] = 0
            return
        claimed = await self._trim_claimed_to_pid_cap(claimed)
        if self._budget_guard is not None:
            check = self._budget_guard.check_all_limits(estimated_cost=self._estimated_dispatch_cost(len(claimed)))
            if not check["allowed"]:
                logger.warning("Budget exceeded, skipping execute dispatch: %s", check["reason"])
                self._tick_metrics["todos_dispatched"] = 0
                return

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

        try:
            resolved: list[tuple[Any, ProjectWorkIdentity, bool, str]] = []
            todo_map: dict[str, Any] = {}
            scheduler_ids: dict[tuple[str, str], str] = {}
            for todo in todos:
                identity, project_owned = _runtime_work_identity(
                    todo,
                    self._tick_project_id,
                )
                scheduler_id = identity.scheduler_id if project_owned else identity.todo_id
                if scheduler_id in todo_map:
                    raise ValueError(f"duplicate runtime work identity {scheduler_id!r}")
                todo_map[scheduler_id] = todo
                scheduler_ids[(identity.project_id, identity.todo_id)] = scheduler_id
                resolved.append((todo, identity, project_owned, scheduler_id))

            dependencies_by_scheduler_id: dict[str, tuple[str, ...]] = {}
            for todo, identity, project_owned, scheduler_id in resolved:
                dependencies = _todo_dependency_ids(todo)
                dependencies_by_scheduler_id[scheduler_id] = dependencies
                missing = [
                    dependency_id
                    for dependency_id in dependencies
                    if (identity.project_id, dependency_id) not in scheduler_ids
                ]
                if not missing:
                    continue
                if self._todo_repo is None:
                    raise ValueError(
                        f"todo {identity.todo_id!r} has predecessors outside the "
                        "batch without a repository proof"
                    )
                predecessors = await self._todo_repo.get_by_ids(
                    missing,
                    project_id=identity.project_id if project_owned else None,
                )
                if not isinstance(predecessors, Mapping) or any(
                    dependency_id not in predecessors
                    or getattr(predecessors[dependency_id], "status", None)
                    != TodoStatus.COMPLETE.value
                    for dependency_id in missing
                ):
                    raise ValueError(
                        f"todo {identity.todo_id!r} has a missing or incomplete "
                        "predecessor"
                    )

            items: list[WorkItem] = []
            queue_exclusive = self._config_snapshot.get(
                "scheduler_queue_exclusive",
                False,
            )
            for todo, identity, project_owned, scheduler_id in resolved:
                depends_on = frozenset(
                    dependency_scheduler_id
                    for dependency_id in dependencies_by_scheduler_id[scheduler_id]
                    if (
                        dependency_scheduler_id := scheduler_ids.get(
                            (identity.project_id, dependency_id)
                        )
                    )
                    is not None
                )
                if identity.queue == "self_update":
                    base_item = _self_update_work_item_from_todo(todo, scheduler_id)
                    items.append(
                        WorkItem(
                            id=base_item.id,
                            resources=base_item.resources,
                            depends_on=depends_on,
                            is_greenfield=base_item.is_greenfield,
                        )
                    )
                    continue
                todo_resource = (
                    identity.todo_resource
                    if project_owned
                    else f"todo:{identity.todo_id}"
                )
                resources = {todo_resource}
                if queue_exclusive:
                    resources.add(
                        identity.queue_resource
                        if project_owned
                        else f"queue:{identity.queue}"
                    )
                items.append(
                    WorkItem(
                        id=scheduler_id,
                        resources=frozenset(resources),
                        depends_on=depends_on,
                    )
                )
            batches = Scheduler().plan(items)
        except (CycleError, ValueError) as exc:
            logger.error("Scheduler.plan() rejected runtime work: %s", exc)
            return 0

        dispatch_count = 0
        can_concurrent = self._session_factory is not None

        for batch_ids in batches:
            batch_todos = [todo_map[bid] for bid in batch_ids if bid in todo_map]
            dispatch_count += await self._dispatch_scheduler_batch(
                batch_todos,
                can_concurrent=can_concurrent,
            )

        return dispatch_count

    async def _dispatch_scheduler_batch(
        self,
        batch_todos: list[Any],
        *,
        can_concurrent: bool,
    ) -> int:
        if not batch_todos:
            return 0
        if not can_concurrent:
            return await self._dispatch_sequential_batch(batch_todos)
        if len(batch_todos) == 1:
            try:
                async with self._dispatch_semaphore:
                    await self._dispatch_execute_job_isolated(batch_todos[0])
                return 1
            except Exception as exc:
                logger.error("Job dispatch raised: %s", exc)
                return 0

        logger.info(
            "Scheduler batch: %d jobs concurrent "
            "(session-per-coroutine, max_concurrent=%d)",
            len(batch_todos),
            self._dispatch_semaphore._value,
        )
        tasks = [
            asyncio.ensure_future(self._dispatch_with_semaphore(todo))
            for todo in batch_todos
        ]
        batch_timeout = min(300.0 * len(batch_todos), 1800.0)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=batch_timeout,
            )
        except TimeoutError:
            logger.error(
                "Concurrent dispatch batch timed out after %.0fs; "
                "cancelling %d pending job(s)",
                batch_timeout,
                sum(1 for task in tasks if not task.done()),
            )
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return 0
        dispatched = 0
        for result in results:
            if isinstance(result, Exception):
                logger.error("Concurrent job dispatch raised: %s", result)
            else:
                dispatched += 1
        return dispatched

    async def _dispatch_sequential_batch(self, batch_todos: list[Any]) -> int:
        dispatched = 0
        for todo in batch_todos:
            try:
                await self._dispatch_execute_job(todo)
                if self._active_session is not None:
                    await self._release_completed_execution_lease(
                        self._active_session,
                        todo,
                    )
                dispatched += 1
            except Exception as exc:
                logger.error("Sequential job dispatch raised: %s", exc)
        return dispatched

    async def _dispatch_with_semaphore(self, todo: Any) -> None:
        async with self._dispatch_semaphore:
            await self._dispatch_execute_job_isolated(todo)

    async def _dispatch_execute_job_isolated(self, todo: Any) -> None:
        """Dispatch a single execute job using its OWN async session.

        Opens a fresh session from self._session_factory for every DB interaction
        (load_shared_vars, persist_task_return) so this coroutine is safe to run
        concurrently with other _dispatch_execute_job_isolated calls in the same
        asyncio.gather() batch — no shared _active_session is touched.

        # Sandbox wiring: BEFORE the agent's first tool call we resolve its
        # PermissionSpec (from config — a per-queue default) and ask the host's
        # SandboxBackend to apply it. After the job completes (or raises) the
        # backend's release() runs in the finally block. The whole lifecycle is
        # wrapped in try/except so a sandbox bug never wedges the daemon — a
        # failure logs loudly + dispatches with a "no sandbox" warning.
        """
        assert self._session_factory is not None

        lease_supervisor = self._execution_lease_supervisor_for_todo(todo)
        heartbeat_task: asyncio.Task[None] | None = None
        if lease_supervisor is not None:
            initial_status = await lease_supervisor.heartbeat_once()
            if initial_status is not LeaseRenewalStatus.RENEWED:
                try:
                    await lease_supervisor.confirm_termination()
                finally:
                    lease_supervisor.stop()
                raise OwnedExecutionCancelled(
                    "execution lease unavailable before dispatch"
                )
            heartbeat_task = asyncio.create_task(
                lease_supervisor.run(heartbeat_immediately=False)
            )
        sandbox_handle = await self._sandbox_apply_for_todo(todo)
        try:
            async with self._session_factory() as job_session:
                if sandbox_handle is not None and self._sandbox_executor is not None:
                    self._sandbox_executor.execute(
                        f"dispatch:{getattr(todo, 'todo_id', '?')}:{_safe_str(todo, 'work_type', 'unknown')}",
                        workdir=self._resolve_repo_root(getattr(todo, "project_id", None)),
                    )
                job_variable_repo = VariableNamespaceRepository(job_session)
                job_task_return_repo = TaskReturnRepository(job_session)
                try:
                    await self._dispatch_execute_job(
                        todo,
                        _variable_repo_override=job_variable_repo,
                        _task_return_repo_override=job_task_return_repo,
                        _session_override=job_session,
                        _lease_supervisor_override=lease_supervisor,
                    )
                except OwnedExecutionCancelled:
                    await self._stop_execution_lease_heartbeat(
                        lease_supervisor,
                        heartbeat_task,
                    )
                    if lease_supervisor is not None:
                        await lease_supervisor.request_cancellation()
                        await lease_supervisor.confirm_termination()
                    raise
                await self._stop_execution_lease_heartbeat(
                    lease_supervisor,
                    heartbeat_task,
                )
                await self._release_completed_execution_lease(job_session, todo)
                try:
                    await job_session.commit()
                except Exception as exc:
                    logger.warning(
                        "Failed to commit isolated job session for %s: %s",
                        getattr(todo, "todo_id", "?"),
                        exc,
                    )
        finally:
            await self._stop_execution_lease_heartbeat(
                lease_supervisor,
                heartbeat_task,
            )
            if sandbox_handle is not None:
                await self._sandbox_release(sandbox_handle)

    def _execution_lease_supervisor_for_todo(
        self,
        todo: Any,
    ) -> ExecutionLeaseSupervisor | None:
        """Build supervision only for a claim fenced by this process."""
        if self._session_factory is None:
            return None
        todo_id = _safe_str(todo, "todo_id", "") or ""
        leased_todo_ids = self._tick_state.get("execution_lease_todo_ids", [])
        if not todo_id or todo_id not in leased_todo_ids:
            return None
        versions = self._tick_state.get("execution_lease_versions", {})
        version = versions.get(todo_id) if isinstance(versions, Mapping) else None
        if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
            return None
        ttl_seconds, heartbeat_interval_seconds = self._execution_lease_timing()
        bucket_keys = _runtime_lease_bucket_keys(todo, self._tick_project_id)
        return ExecutionLeaseSupervisor(
            session_factory=self._session_factory,
            identity=ExecutionLeaseIdentity(
                bucket_key=bucket_keys[0],
                holder_id=self._lease_owner_id,
                todo_version=version,
            ),
            alias_bucket_keys=bucket_keys[1:],
            event_bus=self._event_bus,
            ttl_seconds=ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )

    def _execution_lease_timing(self) -> tuple[int, float]:
        """Return validated lease TTL and heartbeat interval configuration."""
        raw_config = self.config.get("event_loop", {})
        event_loop_config = raw_config if isinstance(raw_config, Mapping) else {}
        ttl_seconds = event_loop_config.get("execution_lease_ttl_seconds", 300)
        interval_seconds = event_loop_config.get(
            "execution_lease_heartbeat_interval_seconds",
            30.0,
        )
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or ttl_seconds <= 0
            or ttl_seconds > 86_400
        ):
            raise ValueError(
                "execution_lease_ttl_seconds must be an integer between 1 and 86400"
            )
        if (
            not isinstance(interval_seconds, (int, float))
            or isinstance(interval_seconds, bool)
            or not 0 < interval_seconds < ttl_seconds
        ):
            raise ValueError(
                "execution_lease_heartbeat_interval_seconds must be positive and shorter than the lease TTL"
            )
        return ttl_seconds, float(interval_seconds)

    @staticmethod
    async def _stop_execution_lease_heartbeat(
        supervisor: ExecutionLeaseSupervisor | None,
        heartbeat_task: asyncio.Task[None] | None,
    ) -> None:
        """Stop and drain a heartbeat task before releasing its database fence."""
        if supervisor is None or heartbeat_task is None:
            return
        supervisor.stop()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

    async def _release_completed_execution_lease(
        self,
        session: AsyncSession,
        todo: Any,
    ) -> None:
        """Release one lease only after its dispatch returned normally."""
        todo_id = _safe_str(todo, "todo_id", "") or ""
        leased_todo_ids = self._tick_state.get("execution_lease_todo_ids", [])
        if not todo_id or todo_id not in leased_todo_ids:
            return
        for bucket_key in _runtime_lease_bucket_keys(todo, self._tick_project_id):
            await release_lease(
                session,
                bucket_key,
                holder_id=self._lease_owner_id,
            )

    async def _sandbox_apply_for_todo(self, todo: Any) -> Any | None:
        """Resolve this todo's PermissionSpec and apply the host sandbox.

        Production daemon wiring supplies a durable attestation store and uses
        the fail-closed admission path.  The legacy fail-open behavior remains
        only for explicitly unwired EventLoop instances used by older callers.
        Returns a pinned dispatch lease or a legacy opaque handle.
        """
        if self._sandbox_attestation_store is not None:
            return await self._sandbox_admit_for_todo(todo)
        try:
            from general_ludd.security.sandboxes import SandboxTarget, detect

            backend = detect.auto()
            if backend is None:
                logger.warning(
                    "No sandbox backend for todo %s — dispatching UNSANDBOXED",
                    _safe_str(todo, "todo_id", "?"),
                )
                return None
            spec = self._resolve_permission_spec(todo)
            if spec is None:
                logger.info(
                    "No PermissionSpec resolved for todo %s — skipping sandbox",
                    _safe_str(todo, "todo_id", "?"),
                )
                return None
            target = SandboxTarget(
                directory=self._resolve_repo_root(getattr(todo, "project_id", None)),
            )
            handle = await self._bounded_to_thread(backend.apply, spec, target)
            findings = await self._bounded_to_thread(backend.verify, spec, handle)
            fails = [f for f in findings if f.severity == "fail"]
            if fails or not handle.applied:
                logger.error(
                    "Sandbox verify reported %d fail / %d warn findings for todo "
                    "%s (handle.applied=%s) — proceeding UNSANDBOXED: %s",
                    len(fails),
                    sum(1 for f in findings if f.severity == "warn"),
                    _safe_str(todo, "todo_id", "?"),
                    handle.applied,
                    [f.message for f in fails],
                )
            else:
                logger.info(
                    "Sandbox applied for todo %s via %s (token=%s, %d ok findings)",
                    _safe_str(todo, "todo_id", "?"),
                    getattr(backend, "name", "?"),
                    handle.token,
                    sum(1 for f in findings if f.severity == "ok"),
                )
            return handle
        except Exception as exc:
            logger.error(
                "Sandbox apply raised for todo %s — dispatching UNSANDBOXED: %s",
                _safe_str(todo, "todo_id", "?"),
                exc,
                exc_info=True,
            )
            return None

    async def _sandbox_admit_for_todo(self, todo: Any) -> Any:
        """Apply, independently observe, durably attest, then admit one todo.

        This is the production fail-closed path.  The resolved profile is a
        frozen value supplied at daemon construction, so this dispatch remains
        pinned to one policy hash while a replacement worker/profile is prepared
        and promoted.  Legacy test-only loops without a durable store retain the
        older helper above until their caller explicitly wires admission.
        """
        from general_ludd.security.sandboxes import SandboxTarget, detect
        from general_ludd.security.sandboxes.attestation import (
            RuntimeSandboxObservation,
        )
        from general_ludd.security.sandboxes.dispatch import (
            DurableSandboxDispatchGuard,
            SandboxDispatchIdentity,
            SandboxDispatchLease,
            unavailable_observation,
        )

        resolved = self._sandbox_profile
        if resolved is None:
            raise RuntimeError("sandbox dispatch denied: no resolved sandbox profile")
        store = self._sandbox_attestation_store
        if store is None:
            raise RuntimeError("sandbox dispatch denied: no durable attestation store")

        project_id = (_safe_str(todo, "project_id", "") or "").strip()
        work_item_id = (_safe_str(todo, "todo_id", "") or "").strip()
        if not project_id or not work_item_id:
            raise RuntimeError("sandbox dispatch denied: tenant/project and work-item identity are required")
        identity = SandboxDispatchIdentity(
            project_id=project_id,
            work_item_id=work_item_id,
            agent_id="event-loop",
            tenant_id=project_id,
            correlation_id=f"sandbox:{work_item_id}",
        )
        guard = DurableSandboxDispatchGuard(
            resolved=resolved,
            store=store,
        )
        if self._sandbox_executor is None:
            await guard.deny_unavailable(identity)
        backend = detect.auto()
        if backend is None:
            await guard.deny_unavailable(identity)

        backend_name = str(getattr(backend, "name", ""))
        spec = self._resolve_permission_spec(todo)
        if spec is None:
            await guard.deny_unavailable(identity, backend=backend_name)
        target = SandboxTarget(directory=self._resolve_repo_root(project_id))

        try:
            handle = await self._bounded_to_thread(backend.apply, spec, target)
        except Exception:
            logger.exception(
                "Sandbox apply failed for todo %s; denying dispatch",
                work_item_id,
            )
            await guard.deny_unavailable(identity, backend=backend_name)

        try:
            findings = await self._bounded_to_thread(backend.verify, spec, handle)
            observe_runtime = getattr(backend, "observe_runtime", None)
            if not callable(observe_runtime):
                observation = unavailable_observation(
                    resolved,
                    backend=backend_name,
                )
            else:
                raw_observation = await self._bounded_to_thread(
                    observe_runtime,
                    spec,
                    handle,
                    resolved,
                )
                observation = RuntimeSandboxObservation.model_validate(raw_observation)

            verification_failed = any(getattr(finding, "severity", None) == "fail" for finding in findings)
            if (
                verification_failed
                or not bool(getattr(handle, "applied", False))
                or observation.backend != backend_name
            ):
                observation = observation.model_copy(update={"applied": False})
        except Exception:
            logger.exception(
                "Sandbox observation failed for todo %s; denying dispatch",
                work_item_id,
            )
            await self._sandbox_release_backend(backend, handle)
            await guard.deny_unavailable(identity, backend=backend_name)

        try:
            attestation = await guard.attest(identity, observation)
        except BaseException:
            await self._sandbox_release_backend(backend, handle)
            raise

        logger.info(
            "Sandbox dispatch admitted for todo %s under %s via %s (attestation=%d)",
            work_item_id,
            attestation.policy_version,
            attestation.effective_backend,
            attestation.sequence,
        )
        return SandboxDispatchLease(
            backend=backend,
            handle=handle,
            attestation=attestation,
        )

    async def _sandbox_release_backend(self, backend: Any, handle: Any) -> None:
        """Release the exact backend selected for this admission attempt."""
        try:
            await self._bounded_to_thread(backend.release, handle)
        except Exception as exc:
            logger.warning(
                "Sandbox release raised (token=%s): %s",
                getattr(handle, "token", "?"),
                exc,
            )

    async def _sandbox_release(self, handle: Any) -> None:
        """Release a sandbox handle; best-effort + fail-open."""
        try:
            from general_ludd.security.sandboxes import detect
            from general_ludd.security.sandboxes.dispatch import SandboxDispatchLease

            if isinstance(handle, SandboxDispatchLease):
                await self._sandbox_release_backend(handle.backend, handle.handle)
                return

            backend = detect.auto()
            if backend is None or handle is None:
                return
            await self._bounded_to_thread(backend.release, handle)
        except Exception as exc:
            logger.warning(
                "Sandbox release raised (token=%s): %s",
                getattr(handle, "token", "?"),
                exc,
            )

    def _resolve_permission_spec(self, todo: Any) -> Any | None:
        """Resolve a PermissionSpec for this todo from config.

        Effective scope = ``human ∩ agent ∩ requested``:

        1. ``agent_spec`` — the per-queue ``permission_spec`` block in
           ``self.config`` (``queues[].permission_spec``).
        2. ``human_spec`` — the active human user's spec. Resolved from
           ``self._human_spec`` (set per-session by the daemon) or the
           default human role from ``self.config['default_human_role']``
           (default ``human-operator``). See
           :func:`general_ludd.security.permissions.default_human_spec`.
        3. The two are intersected via
           :meth:`PermissionSpecParser.intersection`. The result is what
           the dispatched subagent actually runs with — never more.

        Returns ``None`` if the security module is unavailable or no queue
        spec is configured (matching pre-existing unscoped behavior).
        """
        try:
            from general_ludd.security.permissions import (
                Capability,
                PermissionSpec,
                PermissionSpecParser,
                default_human_spec,
            )
        except Exception:
            return None
        queue = _safe_str(todo, "queue", "core") or "core"
        queues_cfg = self.config.get("queues", []) if isinstance(self.config, dict) else []
        spec_cfg: dict[str, Any] | None = None
        for q in queues_cfg:
            if isinstance(q, dict) and q.get("name") == queue:
                spec_cfg = q.get("permission_spec")
                break
        if spec_cfg is None:
            return None
        caps = [
            Capability(
                resource=str(c.get("resource", "file:repo")),
                actions=list(c.get("actions") or []),
                constraints=dict(c.get("constraints") or {}),
            )
            for c in (spec_cfg.get("capabilities") or [])
        ]
        denied = [
            Capability(
                resource=str(c.get("resource", "file:repo")),
                actions=list(c.get("actions") or []),
                constraints=dict(c.get("constraints") or {}),
            )
            for c in (spec_cfg.get("denied") or [])
        ]
        agent_spec = PermissionSpec(
            agent_type=f"{queue}-{getattr(todo, 'todo_id', 'agent')}",
            capabilities=caps,
            denied=denied,
        )

        human_spec = getattr(self, "_human_spec", None)
        if human_spec is None and isinstance(self.config, dict):
            role = self.config.get("default_human_role") or "human-operator"
            try:
                human_spec = default_human_spec(role)
            except Exception:
                human_spec = None
        if human_spec is None:
            return agent_spec
        return PermissionSpecParser.intersection(human_spec, agent_spec)

    async def _resolve_human_input_for_todo(self, todo_id: str) -> str | None:
        """Return the latest resolved HumanTodo text for a parent todo.

        Injected into the next dispatch's extravars as ``human_input`` so the
        agent receives the human's response to a blocker it raised. Returns
        ``None`` when no resolved human-todo exists for this parent (the
        common case — most todos never block on a human).

        Only terminal-and-done human-todos are surfaced (dismissed todos
        cancel the parent agent todo, so it never re-dispatches).

        E12: replaced the Python-side filter-over-all-rows pattern with a
        SQL-side WHERE parent_agent_todo_id=? AND status='done' LIMIT 1 query
        via HumanTodoRepository.get_done_for_parent.
        """
        factory = self._session_factory
        if factory is None:
            return None
        try:
            from general_ludd.db.repository import HumanTodoRepository
        except Exception:
            return None
        try:
            async with factory() as session:
                repo = HumanTodoRepository(session)
                top = await repo.get_done_for_parent(todo_id)
                if top is None:
                    return None
                return top.human_resolution
        except Exception as exc:
            logger.warning(
                "could not resolve human_input for todo %s: %s",
                todo_id,
                exc,
            )
            return None

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
        _variable_repo_override: VariableNamespaceRepository | None = None,
        _task_return_repo_override: TaskReturnRepository | None = None,
        _session_override: AsyncSession | None = None,
        _lease_supervisor_override: ExecutionLeaseSupervisor | None = None,
    ) -> None:
        """Dispatch a single execute job.

        The ``_*_override`` keyword arguments are used by
        :meth:`_dispatch_execute_job_isolated` to inject per-coroutine repos/sessions
        so this method remains safe when called concurrently via asyncio.gather.
        When called without overrides (sequential path) it falls back to the shared
        instance attributes as before.

        Phase-2 Step 6: a ``self_update``-queue todo is short-circuited to
        :meth:`_apply_self_update_code` — it never reaches the Ansible/HTTP
        execute path (those todos arm a code hot-rotation + reload, not a
        playbook run).
        """
        _dispatch_start = time.monotonic()
        # SpendLimiter pre-call gate: atomically check + record the projected
        # cost via try_charge().  The previous would_exceed()-only check was
        # non-mutating — it never recorded spend, so the rolling window stayed
        # at zero and the soft cap could never trip (bug: inert limiter).
        # try_charge() does the check and the record in one locked step so:
        #   * Every accepted dispatch is charged against the window immediately.
        #   * Concurrent dispatches cannot both observe the same headroom and
        #     both commit (check-and-record is atomic under the limiter's lock).
        # A None limiter (no cap configured) leaves dispatch unchanged.
        limiter = self._spend_limiter
        if limiter is not None:
            projected = self._estimated_dispatch_cost(1)
            accepted = limiter.try_charge(projected, kind="token")
            if not accepted:
                logger.warning(
                    "SpendLimiter: deferring dispatch for todo %s — projected=%.6f window_spend=%.6f remaining=%.6f",
                    _safe_str(todo, "todo_id", "?"),
                    projected,
                    limiter.window_spend(),
                    limiter.remaining(),
                )
                return
        if _safe_str(todo, "queue") == "self_update":
            await self._apply_self_update_code(todo, _session_override=_session_override)
            return
        # Resolve which repos/session to use: per-job overrides (concurrent path)
        # or the shared tick-level ones (sequential fallback path).
        eff_variable_repo = _variable_repo_override if _variable_repo_override is not None else self._variable_repo
        eff_task_return_repo = (
            _task_return_repo_override if _task_return_repo_override is not None else self._task_return_repo
        )
        eff_session = _session_override if _session_override is not None else self._active_session

        budget_context: dict[str, Any] = {}
        if self._mcp_tool_registry is not None:
            budget_context["mcp_tools"] = self._mcp_tool_registry.tool_names()
        default_playbook = self._config_snapshot.get("default_playbook", "noop.yml")
        work_type = _safe_str(todo, "work_type", "code") or "code"
        project_id_val = todo.project_id if hasattr(todo, "project_id") and isinstance(todo.project_id, str) else None
        if work_type == "self_improve":
            await self._dispatch_managed_self_improve(
                todo,
                project_id=project_id_val,
                task_return_repo=eff_task_return_repo,
                session=eff_session,
            )
            return
        workspaces = self._project_workspace if isinstance(self._project_workspace, dict) else None
        ws = workspaces.get(project_id_val) if workspaces and project_id_val else None
        playbook = _playbook_for_work_type(
            work_type,
            default_playbook,
            project_id=project_id_val,
            workspaces=workspaces,
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
            "acceptance_criteria": _format_acceptance_criteria(_safe_str(todo, "acceptance_criteria")),
            "definition_of_done": _safe_str(todo, "definition_of_done") or "",
        }
        project_templates_dir = (
            str(ws.templates_dir) if ws and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir() else None
        )
        prompt_text = _resolve_prompt_text_static(
            self._prompt_registry,
            resolved_prompt_profile,
            project_templates_dir=project_templates_dir,
            **task_context,
        )
        # Fallback: the prompt_profile path above is PRIMARY, but a generation
        # todo submitted via POST /api/todos has no prompt_profile, so
        # _resolve_prompt_text_static returns None and the model would never be
        # called (silent no-op). If no profile resolved a prompt but the todo
        # carries a title/description, synthesize a minimal task prompt so the
        # model IS invoked. This only fires when the primary path produced
        # nothing; a real prompt_profile still wins.
        if not prompt_text:
            _fallback_title = task_context.get("todo_title") or ""
            _fallback_desc = task_context.get("todo_description") or ""
            _synthesized = f"Task: {_fallback_title}\n\n{_fallback_desc}".strip()
            if _synthesized:
                prompt_text = _synthesized
                logger.info(
                    "EventLoop: no prompt_profile resolved for todo %s; "
                    "synthesized fallback prompt from title/description",
                    getattr(todo, "todo_id", "?"),
                )
        prompt_text = await self._append_message_queue_section(
            prompt_text,
            todo,
            project_id_val,
        )
        # B3.1.5 checkpoint — pre-model boundary. After claim + prompt
        # resolution, before the model is called. A crash here resumes with
        # the preserved prompt_text so the model invocation is not lost.
        # Wrapped: checkpoint failure MUST NOT abort dispatch (best-effort).
        _todo_id = _safe_str(todo, "todo_id", "") or ""
        if self._checkpoint_manager is not None and _todo_id:
            with contextlib.suppress(Exception):
                checkpoint_identity, checkpoint_project_owned = _runtime_work_identity(
                    todo,
                    project_id_val or self._tick_project_id,
                )
                if not checkpoint_project_owned:
                    raise ValueError(
                        "dispatch checkpoint requires an explicit project owner"
                    )
                todo_version = getattr(todo, "version", None)
                self._checkpoint_manager.checkpoint(
                    AgentEnvironmentSnapshot(
                        task_id=_todo_id,
                        agent_name=_safe_str(todo, "agent_name", "event_loop") or "event_loop",
                        dispatch_state=DispatchState(
                            todo_id=_todo_id,
                            resolved_model_profile=resolved_model_profile,
                            resolved_prompt_profile=resolved_prompt_profile,
                            prompt_text=prompt_text or "",
                            phase_marker="pre_model",
                            lease_holder_id=self._lease_owner_id,
                            project_id=checkpoint_identity.project_id,
                            queue=checkpoint_identity.queue,
                            todo_version=(
                                todo_version
                                if isinstance(todo_version, int)
                                and not isinstance(todo_version, bool)
                                else None
                            ),
                            resume_shard_id=checkpoint_identity.resume_shard_id,
                        ),
                    ),
                    phase="pre_model",
                )
        # Task #48: plan-time technical-debt evaluation (config-gated, default
        # OFF, NON-FATAL). When ``debt_eval.enabled`` is truthy, evaluate the
        # planned change for forward-looking scope gaps: ``fold_in`` gaps are
        # appended to the prompt so THIS task closes them; ``defer`` gaps become
        # BACKLOG child todos on the job session. The default-OFF path is a
        # no-op — dispatch is byte-for-byte unchanged when the flag is absent —
        # and any failure here is swallowed so execution always proceeds.
        debt_cfg = self.config.get("debt_eval", {}) if isinstance(self.config, dict) else {}
        if isinstance(debt_cfg, dict) and debt_cfg.get("enabled", False):
            try:
                from general_ludd.planning.artifact import PlanArtifact
                from general_ludd.planning.debt_applier import apply_debt_findings

                plan = PlanArtifact.from_todo(todo)
                goal = _safe_str(todo, "title") or _safe_str(todo, "description") or plan.todo_id
                findings = await self._bounded_to_thread(self._debt_evaluator.evaluate, plan, goal)
                repo = TodoRepository(eff_session) if eff_session is not None else self._todo_repo
                if repo is not None:
                    result = await apply_debt_findings(findings, plan, todo, repo, project_id=project_id_val)
                    if result.prompt_addendum:
                        prompt_text = (
                            f"{prompt_text}\n\n{result.prompt_addendum}" if prompt_text else result.prompt_addendum
                        )
            except Exception:
                logger.warning(
                    "debt-eval hook failed for todo %s; proceeding with dispatch",
                    getattr(todo, "todo_id", "?"),
                    exc_info=True,
                )
        skill_body = self._resolve_skill_body(todo)
        prompt_text = await self._build_memory_section(prompt_text, todo)
        # Load shared vars via the effective (possibly per-job) repo.
        shared_vars: dict[str, str] | None = None
        if eff_variable_repo is not None:
            try:
                shared_vars = await eff_variable_repo.load_vars_for_project(project_id_val)
            except Exception as exc:
                logger.warning("load_shared_vars failed for todo %s: %s", getattr(todo, "todo_id", "?"), exc)
        job_id = f"EXEC-{todo.todo_id}"
        ab_variant: dict[str, Any] | None = None
        if self._prompt_variant_selector is not None:
            ab_cfg = self.config.get("prompt_ab_testing", {}) if isinstance(self.config, dict) else {}
            if isinstance(ab_cfg, dict) and ab_cfg.get("enabled", False):
                with contextlib.suppress(Exception):
                    ab_variant = self._prompt_variant_selector.select(
                        template_name=resolved_prompt_profile,
                    )
                    if ab_variant is not None:
                        dispatched_profile = resolved_prompt_profile
                        variant_letter = ab_variant["variant"]
                        if dispatched_profile and variant_letter:
                            suffix = f".variant_{variant_letter.lower()}"
                            if not dispatched_profile.endswith(suffix):
                                base, _sep, ext = dispatched_profile.rpartition(".")
                                if base:
                                    resolved_prompt_profile = f"{base}{suffix}.{ext}"
                                else:
                                    resolved_prompt_profile = dispatched_profile + suffix
        if self._run_recorder is not None:
            with contextlib.suppress(Exception):
                event = {
                    "type": "dispatch_started",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "todo_id": todo.todo_id,
                    "work_type": work_type,
                    "model_profile": resolved_model_profile,
                    "prompt_profile": resolved_prompt_profile,
                    "project_id": project_id_val,
                }
                if ab_variant is not None:
                    event["ab_variant"] = ab_variant
                self._run_recorder.record(job_id, event)
        if self._runner is not None:
            if ws is not None and hasattr(ws, "private_data_dir"):
                import os as _os

                job_dir = _os.path.join(str(ws.private_data_dir), job_id)
                _os.makedirs(job_dir, exist_ok=True)
                _os.makedirs(_os.path.join(job_dir, "env"), exist_ok=True)
                pdd = str(ws.private_data_dir)
            else:
                # prepare_job_dirs does blocking os.makedirs; offload so it does
                # not stall the daemon's single asyncio event loop (AB).
                dirs = await self._bounded_to_thread(self._runner.prepare_job_dirs, job_id)
                pdd = dirs["root"]
            # C1 (W3.x): invoke the model for a generation work type the SAME
            # way the worker HTTP path does, then feed the generated text into
            # the playbook vars so the runner path is not a no-op generator.
            _model_call_start = time.monotonic()
            _model_call_success = False
            _model_call_error: str | None = None
            _eff_gateway = self._mock_gateway if self._mock_gateway is not None else self._model_gateway
            if _eff_gateway is not None and is_generation_work_type(_safe_str(todo, "work_type", "code") or "code"):
                # Deployment health check: before calling the model, verify
                # the targeted deployment is healthy. If unhealthy, the
                # self-healing router picks the first healthy fallback.
                _deployment_id = resolved_model_profile or "default"
                if self._deployment_health_router is not None:
                    _routed = self._deployment_health_router.check_and_route(
                        _deployment_id,
                    )
                    if _routed is not None and _routed != _deployment_id:
                        resolved_model_profile = _routed
                        _deployment_id = _routed
                    elif _routed is None:
                        logger.warning(
                            "Deployment %s is unhealthy with no healthy fallback; proceeding with original profile",
                            _deployment_id,
                        )
                _call_start = time.monotonic()
                # #56: opt-in SLM context-compaction on the generation path.
                # When the adaptive compaction controller is wired, use its
                # dynamically-tuned level; otherwise fall back to static config.
                if self._compaction_controller is not None:
                    if self._compaction_level is None:
                        _compaction_cfg = self.config.get("compaction", {}) if isinstance(self.config, dict) else {}
                        _cfg_level = _compaction_cfg.get("level", 1) if _compaction_cfg.get("enabled") else 0
                        self._compaction_level = _cfg_level
                    _use_slm_compaction = not self._compaction_disabled and self._compaction_level > 0
                    _compaction_level = _level_at(self._compaction_level) if _use_slm_compaction else None
                else:
                    _compaction_cfg = self.config.get("compaction", {}) if isinstance(self.config, dict) else {}
                    _use_slm_compaction = bool(_compaction_cfg.get("enabled", False))
                    _compaction_level = _level_at(_compaction_cfg.get("level", 1)) if _use_slm_compaction else None
                try:
                    from general_ludd.scheduling.scheduler import ComputeSchedulingHint

                    _sched_hint = ComputeSchedulingHint.for_work_type(_safe_str(todo, "work_type", "code") or "code")
                    model_response, model_tool_calls = await self._bounded_to_thread(
                        invoke_model_for_generation,
                        _eff_gateway,
                        job_id=job_id,
                        work_type=_safe_str(todo, "work_type", "code") or "code",
                        model_profile=resolved_model_profile,
                        prompt_text=prompt_text,
                        skill_body=skill_body,
                        budget_guard=self._budget_guard,
                        # S-1 (task #25): scope this job's secret resolution to
                        # its project so credential/api-base aliases resolve
                        # through the project's ProjectSecretsManager (isolation),
                        # not the shared base resolver. None → base behavior.
                        project_id=project_id_val,
                        use_slm_compaction=_use_slm_compaction,
                        compaction_level=_compaction_level,
                        scheduling_hint=_sched_hint,
                    )
                    _model_call_success = model_response is not None
                    if self._run_recorder is not None:
                        with contextlib.suppress(Exception):
                            self._run_recorder.record(
                                job_id,
                                {
                                    "type": "model_generation",
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "success": _model_call_success,
                                    "model_profile": resolved_model_profile,
                                    "input_tokens": len(prompt_text or "") // 4,
                                    "output_tokens": len(model_response or "") // 4,
                                },
                            )
                except Exception as _exc:
                    model_response = None
                    model_tool_calls = None
                    _model_call_error = str(_exc)
                    logger.warning(
                        "Model call raised in EventLoop for job %s: %s",
                        job_id,
                        _exc,
                    )
            else:
                model_response = None
                model_tool_calls = None
            _model_call_duration = time.monotonic() - _model_call_start
            # Feed the model-call duration into the shared clock-time tracker so an
            # anomalously-slow model call (e.g. a call that usually takes ~2s now
            # taking ~30s) is detectable vs its learned per-profile baseline. Only
            # record when a model call ACTUALLY happened: non-generation todos leave
            # model_response None, and recording that phantom ~0s sample would drag
            # the per-profile latency baseline toward zero, causing false
            # is_anomalous "slow" warnings and artificially short StallWatchdog
            # deadlines.
            # Token capture was RETIRED from this path: it only covered the daemon
            # generation branch and missed the worker / ToolCallLoop / reviewer /
            # SLM / langgraph paths. It now lives at the gateway billing chokepoint
            # (ModelGateway._invoke_and_bill) with real usage_metadata counts, for
            # complete coverage across every call path.
            if model_response is not None:
                default_tracker().check_then_record(
                    f"model:{resolved_model_profile or 'default'}", _model_call_duration
                )
            # Record deployment health after each model call so the
            # self-healing router learns which deployments are degraded.
            if self._deployment_health_router is not None:
                _health_id = resolved_model_profile or "default"
                _health_checker = self._deployment_health_router.health_checker
                if _model_call_success:
                    _health_checker.record_success(_health_id)
                elif _model_call_error:
                    _health_checker.record_failure(
                        _health_id,
                        _model_call_error,
                        kind="error",
                    )
            # Record the model call in the performance repository.
            if self._model_perf_repo is not None:
                _input_tokens = len(prompt_text or "") // 4
                _output_tokens = len(model_response or "") // 4
                _profile_id = resolved_model_profile or "default"
                _profile_obj: Any = None
                if _eff_gateway is not None:
                    _profile_obj = _eff_gateway.get_profile(_profile_id)
                _cost_usd = 0.0
                if _profile_obj is not None and hasattr(_profile_obj, "cost_per_input_token"):
                    _cost_usd = (
                        _input_tokens * _profile_obj.cost_per_input_token
                        + _output_tokens * _profile_obj.cost_per_output_token
                    )
                _provider = getattr(_profile_obj, "provider", "") if _profile_obj else ""
                _model_name = getattr(_profile_obj, "model_name", "") if _profile_obj else ""
                try:
                    await self._model_perf_repo.record_call(
                        service=_provider or "unknown",
                        model_name=_model_name or _profile_id,
                        model_profile_id=_profile_id,
                        task_type="generation",
                        work_type=_safe_str(todo, "work_type", "code") or "code",
                        success=_model_call_success,
                        input_tokens=_input_tokens,
                        output_tokens=_output_tokens,
                        cost_usd=_cost_usd,
                        duration_ms=_model_call_duration * 1000,
                        todo_id=_safe_str(todo, "todo_id", ""),
                        job_id=job_id,
                        error_message=_model_call_error,
                    )
                except Exception as _rec_exc:
                    logger.debug(
                        "Model perf recording failed for %s: %s",
                        job_id,
                        _rec_exc,
                    )
            if model_response is not None:
                from general_ludd.dispatch.dynamic_dispatcher import (
                    structured_tool_calls_to_calls,
                )
                from general_ludd.dispatch.limits import MAX_CALLS_PER_REQUEST

                # Dispatch the model's STRUCTURED tool_calls directly. The legacy
                # path re-parsed the TEXT (parse_tool_calls(model_response)) which
                # cannot recover the structured calls, so model-driven tool actions
                # were silently discarded on this path.
                calls = structured_tool_calls_to_calls(model_tool_calls)
                if len(calls) > MAX_CALLS_PER_REQUEST:
                    logger.error(
                        "EventLoop: model returned %d tool calls which exceeds cap %d — denying all (job %s)",
                        len(calls),
                        MAX_CALLS_PER_REQUEST,
                        job_id,
                    )
                elif calls:
                    if self._dispatcher is None:
                        logger.warning(
                            "EventLoop: model returned %d tool call(s) but no dispatcher "
                            "is wired — skipping dispatch (job %s)",
                            len(calls),
                            job_id,
                        )
                    else:
                        results = await self._dispatcher.dispatch_all(calls)
                        ok_count = sum(1 for r in results if r.ok)
                        err_count = len(results) - ok_count
                        logger.info(
                            "EventLoop: dispatched %d tool call(s): %d ok, %d error (job %s)",
                            len(results),
                            ok_count,
                            err_count,
                            job_id,
                        )
                        if eff_variable_repo is not None:
                            for r in results:
                                if r.ok:
                                    try:
                                        await eff_variable_repo.set_var(
                                            namespace="tool_results",
                                            key=f"tool_result:{r.name}",
                                            value=str(r.output),
                                        )
                                    except Exception:
                                        # Best-effort persistence of a tool result;
                                        # the dispatch already succeeded. Keep
                                        # swallowing per-result so one bad write
                                        # does not abort the rest, but log it.
                                        logger.debug(
                                            "Failed to persist tool_result for %s (job %s)",
                                            r.name,
                                            job_id,
                                            exc_info=True,
                                        )
                        if self._run_recorder is not None:
                            with contextlib.suppress(Exception):
                                self._run_recorder.record(
                                    job_id,
                                    {
                                        "type": "tool_calls_dispatched",
                                        "timestamp": datetime.now(UTC).isoformat(),
                                        "total": len(results),
                                        "ok": ok_count,
                                        "error_count": err_count,
                                        "calls": [r.to_dict() for r in results],
                                    },
                                )
            # Phase 2 (keystone): autonomous tool use via the ToolCallLoop.
            #
            # Phase 1 above is tool-free by design (CA-T9): it produces text and,
            # if the model emitted STRUCTURED tool_calls, those are dispatched
            # once. Phase 2 is the genuine agentic loop — for tool-requiring work
            # types it binds the live MCP tools (list_tools -> tools=), lets the
            # model choose+call them, executes via the MCP client, feeds results
            # back, and iterates until the model stops requesting tools. This is
            # what makes autonomous tool action FUNCTIONAL (not merely
            # dispatch-wired) end to end.
            #
            # Gated so it runs for tool-requiring work types (analysis, audit,
            # code, bug_fix, refactor, feature, test) — code work types now get
            # the iterative tool loop so they can react to test failures. Still
            # gated on MCP client + model gateway presence. Wrapped so any
            # failure logs + falls through without breaking the tick.
            if work_type in _TOOL_USE_WORK_TYPES and self._mcp_client is not None and self._model_gateway is not None:
                try:
                    _use_langgraph = (
                        self.config.get("use_langgraph_tool_loop", False) if isinstance(self.config, dict) else False
                    )

                    tool_loop: Any = None
                    if _use_langgraph:
                        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

                        auditor = ToolCallAuditor()
                        _adversarial_detector = None
                        if self._daemon_state is not None:
                            _adversarial_detector = self._daemon_state.get("_adversarial_detector")
                        _max_total_tokens = (
                            self.config.get("tool_loop", {}).get("max_total_tokens", None)
                            if isinstance(self.config, dict)
                            else None
                        )
                        tool_loop = LangGraphAgentLoop(
                            model_gateway=self._model_gateway,
                            mcp_client=self._mcp_client,
                            mcp_registry=self._mcp_tool_registry,
                            role="event_loop",
                            tool_auditor=auditor,
                            budget_guard=self._budget_guard,
                            adversarial_detector=_adversarial_detector,
                            max_total_tokens=_max_total_tokens,
                        )
                        logger.debug("EventLoop: using LangGraphAgentLoop for Phase-2")
                    else:
                        from general_ludd.execution.tool_loop import ToolCallLoop

                        # SLICE 2 (task #56): opt-in SLM context-compaction on the
                        # ITERATIVE tool loop. When the adaptive controller is wired,
                        # use its level; otherwise fall back to static config.
                        _tl_level = None
                        _tl_summarize_fn = None
                        if self._compaction_controller is not None:
                            _use_tl = not self._compaction_disabled and (self._compaction_level or 0) > 0
                            if _use_tl:
                                from general_ludd.compaction.slm import (
                                    make_slm_summarize_fn,
                                )

                                _tl_level = _level_at(self._compaction_level or 1)
                                _tl_summarize_fn = make_slm_summarize_fn(self._model_gateway, "compactor")
                        else:
                            _tl_compaction_cfg = (
                                self.config.get("compaction", {}) if isinstance(self.config, dict) else {}
                            )
                            if bool(_tl_compaction_cfg.get("enabled", False)):
                                from general_ludd.compaction.slm import (
                                    make_slm_summarize_fn,
                                )

                                _tl_level = _level_at(_tl_compaction_cfg.get("level", 1))
                                _tl_summarize_fn = make_slm_summarize_fn(self._model_gateway, "compactor")

                        auditor = ToolCallAuditor()
                        store = BadCallSituationStore()
                        _adversarial_detector = None
                        if self._daemon_state is not None:
                            _adversarial_detector = self._daemon_state.get("_adversarial_detector")
                        _code_max_iters = (
                            self.config.get("tool_loop", {}).get("code_max_iterations", 5)
                            if isinstance(self.config, dict)
                            else 5
                        )
                        _analysis_max_iters = (
                            self.config.get("tool_loop", {}).get("analysis_max_iterations", 10)
                            if isinstance(self.config, dict)
                            else 10
                        )
                        _max_total_tokens = (
                            self.config.get("tool_loop", {}).get("max_total_tokens", None)
                            if isinstance(self.config, dict)
                            else None
                        )
                        _per_iteration_timeout = (
                            self.config.get("tool_loop", {}).get("per_iteration_timeout_seconds", None)
                            if isinstance(self.config, dict)
                            else None
                        )
                        tool_loop = ToolCallLoop(
                            model_gateway=self._model_gateway,
                            mcp_client=self._mcp_client,
                            mcp_registry=self._mcp_tool_registry,
                            # Per-role capability gate: the event-loop turn handler
                            # drives MCP tool use under the built-in "event_loop"
                            # role, which grants the "mcp" dispatch kind (see
                            # capability_lattice._BUILTIN) so normal operation is
                            # unaffected. Supplying the role activates the fail-closed
                            # gate in run_with_tools instead of leaving it ungated.
                            role="event_loop",
                            compaction_level=_tl_level,
                            summarize_fn=_tl_summarize_fn,
                            tool_auditor=auditor,
                            situation_store=store,
                            budget_guard=self._budget_guard,
                            adversarial_detector=_adversarial_detector,
                            max_total_tokens=_max_total_tokens,
                            per_iteration_timeout=_per_iteration_timeout,
                            work_type_max_iterations={
                                "analysis": _analysis_max_iters,
                                "audit": _analysis_max_iters,
                                "code": _code_max_iters,
                                "bug_fix": _code_max_iters,
                                "refactor": _code_max_iters,
                                "feature": _code_max_iters,
                                "test": _code_max_iters,
                            },
                        )
                    # Use the Phase-1 generated text as additional context so the
                    # tool-driven phase REFINES the analysis rather than starting
                    # blind; fall back to the raw prompt when Phase 1 produced
                    # nothing.
                    phase2_user = prompt_text or ""
                    if model_response:
                        phase2_user = (
                            f"{phase2_user}\n\nInitial analysis (refine using the available tools):\n{model_response}"
                        ).strip()
                    phase2_job = JobSpec(
                        job_id=job_id,
                        todo_id=_safe_str(todo, "todo_id", "") or "",
                        playbook=playbook,
                        queue=_safe_str(todo, "queue", "core") or "core",
                        work_type=work_type,
                        resource_profile=_safe_str(todo, "resource_profile", "low_resource") or "low_resource",
                        model_profile=resolved_model_profile,
                        prompt_profile=resolved_prompt_profile,
                        prompt_text=phase2_user,
                        skill_body=skill_body,
                        project_id=project_id_val,
                    )
                    tool_result = await tool_loop.run_with_tools(
                        phase2_job,
                        skill_body or "",
                        phase2_user,
                    )
                    logger.info(
                        "EventLoop: Phase-2 ToolCallLoop completed for %s job %s (work_type=%s)",
                        _safe_str(todo, "todo_id", "?"),
                        job_id,
                        work_type,
                    )
                    if self._run_recorder is not None:
                        with contextlib.suppress(Exception):
                            self._run_recorder.record(
                                job_id,
                                {
                                    "type": "tool_loop_completed",
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "output": str(tool_result) if tool_result else None,
                                },
                            )
                    # Persist the tool-loop output alongside the Phase-1 dispatch
                    # results so the autonomous tool action is recorded on the job
                    # trace (mirrors how dispatch_all results are persisted above).
                    if eff_variable_repo is not None and tool_result:
                        try:
                            await eff_variable_repo.set_var(
                                namespace="tool_results",
                                key=f"tool_loop_result:{job_id}",
                                value=str(tool_result),
                            )
                        except Exception:
                            logger.debug(
                                "Failed to persist Phase-2 tool_loop_result (job %s)",
                                job_id,
                                exc_info=True,
                            )
                except Exception:
                    # Phase 2 is additive: a tool-loop failure must NOT break the
                    # tick. Log loudly and fall through to the playbook run with
                    # the Phase-1 output intact.
                    logger.warning(
                        "EventLoop: Phase-2 ToolCallLoop failed for job %s "
                        "(work_type=%s) — falling through to Phase-1 output",
                        job_id,
                        work_type,
                        exc_info=True,
                    )
            if model_response is not None and self._benchmark_recorder is not None:
                try:
                    from general_ludd.event_loop.benchmark import record_job_benchmark

                    _bt = asyncio.create_task(
                        record_job_benchmark(
                            self._benchmark_recorder,
                            model_profile=resolved_model_profile,
                            prompt_profile=resolved_prompt_profile,
                            work_type=work_type,
                            success=True,
                            input_tokens=len(prompt_text or "") // 4,
                        )
                    )
                    self._track_background_task(_bt)
                except Exception:
                    # Best-effort fire-and-forget benchmark write: a scheduling
                    # failure must never abort dispatch. Keep swallowing but make
                    # a persistently-dead recorder visible at debug.
                    logger.debug(
                        "Benchmark recorder scheduling failed (non-fatal)",
                        exc_info=True,
                    )
                # Trace-buffer feed (additive): the DB benchmark write above is
                # preserved, but the trace→recorder→RecentTracesBuffer chain was
                # never exercised, so /api/traces always reported count 0. Build a
                # genuine ExecutionTrace with one completed span around the model
                # generation (reusing the data already gathered above) and feed it
                # through AutoBenchmarkRecorder.record_from_trace so the in-process
                # recent-traces buffer reflects actually-captured telemetry.
                try:
                    from general_ludd.observability.tracer import ExecutionTrace

                    _input_tokens = len(prompt_text or "") // 4
                    _output_tokens = len(model_response or "") // 4
                    _trace = ExecutionTrace(
                        todo_id=_safe_str(todo, "todo_id", "") or "",
                        work_type=work_type,
                        # Tenant attribution (task #19): carry the project this
                        # todo belongs to so the trace is visible to
                        # project-scoped /api/traces callers. Without it every
                        # real trace records project_id=None and is EXCLUDED by
                        # the tenant-boundary filter in
                        # RecentTracesBuffer.recent()/snapshot(). None stays
                        # valid for genuinely project-less traces.
                        project_id=project_id_val,
                    )
                    _span = _trace.start_span(name="model_generation", phase="generate")
                    _span.complete(
                        status="success",
                        input_tokens=_input_tokens,
                        output_tokens=_output_tokens,
                        model_profile_id=resolved_model_profile,
                        prompt_profile_id=resolved_prompt_profile,
                    )
                    self._active_traces[_trace.trace_id] = _trace
                    # Prune the trace dict to prevent unbounded growth (MED audit
                    # finding): once we exceed the cap, evict the oldest entries.
                    if len(self._active_traces) > self._MAX_ACTIVE_TRACES:
                        _evict = list(self._active_traces)[: len(self._active_traces) - self._MAX_ACTIVE_TRACES // 2]
                        for _k in _evict:
                            self._active_traces.pop(_k, None)
                    _tbt = asyncio.create_task(
                        self._benchmark_recorder.record_from_trace(
                            _trace,
                            success=True,
                        )
                    )
                    self._track_background_task(_tbt)
                except Exception:
                    # Best-effort trace-buffer feed (telemetry only): a failure
                    # here must never abort dispatch. Keep swallowing but log at
                    # debug so a broken trace pipeline is observable.
                    logger.debug(
                        "Trace-buffer feed failed (non-fatal)",
                        exc_info=True,
                    )
            # write_vars does blocking os.makedirs + yaml file write + os.chmod;
            # offload so it does not stall the daemon's single asyncio loop (AB).
            # Flow 4: surface the most recent resolved HumanTodo's resolution
            # text as ``human_input`` so the agent receives the human's answer
            # to a blocker it raised. None when no human-todo resolved for
            # this todo (the common case).
            _human_input: str | None = await self._resolve_human_input_for_todo(todo.todo_id)
            await self._bounded_to_thread(
                self._runner.write_vars,
                job_id,
                job_vars={
                    "job_id": job_id,
                    "todo_id": todo.todo_id,
                    "queue": _safe_str(todo, "queue", "core"),
                    "work_type": _safe_str(todo, "work_type", "unknown"),
                    "model_profile": resolved_model_profile,
                    "prompt_profile": resolved_prompt_profile,
                    "prompt_text": prompt_text,
                    "skill_body": skill_body,
                    "model_response": model_response,
                    "playbook": playbook,
                    **budget_context,
                    **({"human_input": _human_input} if _human_input else {}),
                },
                shared_vars=shared_vars,
            )
            runner_env: dict[str, str] = {}
            if ws is not None and hasattr(ws, "roles_dir") and ws.roles_dir.is_dir():
                runner_env["ANSIBLE_ROLES_PATH"] = str(ws.roles_dir)
            if ws is not None and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir():
                runner_env["GLUDD_TEMPLATES_DIR"] = str(ws.templates_dir)
            # M9 (W3.3): run_playbook is a blocking I/O call; wrap in
            # asyncio.to_thread so the event loop stays responsive during
            # long playbook executions and CancelledError propagates cleanly.
            if (
                _lease_supervisor_override is not None
                and _lease_supervisor_override.is_cancellation_requested()
            ):
                raise OwnedExecutionCancelled
            if (
                _lease_supervisor_override is not None
                and _session_override is not None
            ):
                # The isolated session may have started a transaction while
                # loading shared variables.  Never pin that transaction across
                # a blocking runner: the independent heartbeat/cancellation
                # sessions must be able to observe and commit the durable
                # cancellation fence while the runner is still active.
                await _session_override.commit()
            if _lease_supervisor_override is None:
                run_result = await self._bounded_to_thread(
                    self._runner.run_playbook,
                    playbook_name=playbook,
                    private_data_dir=pdd,
                    env=runner_env,
                )
            else:
                run_result = await self._run_playbook_with_lease_supervision(
                    _lease_supervisor_override,
                    playbook=playbook,
                    private_data_dir=pdd,
                    env=runner_env,
                )
            if (
                _lease_supervisor_override is not None
                and isinstance(run_result, Mapping)
                and str(run_result.get("status", "")).lower()
                in {"canceled", "cancelled"}
            ):
                raise OwnedExecutionCancelled
            if self._run_recorder is not None:
                with contextlib.suppress(Exception):
                    self._run_recorder.record(
                        job_id,
                        {
                            "type": "dispatch_completed",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "success": True,
                            "playbook": playbook,
                        },
                    )
            if (
                self._prompt_variant_selector is not None
                and self._prompt_variant_selector.variant_metrics is not None
                and ab_variant is not None
            ):
                with contextlib.suppress(Exception):
                    _latency_ms = (time.monotonic() - _dispatch_start) * 1000.0
                    _success = _model_call_success if model_response is not None else True
                    self._prompt_variant_selector.record_outcome(
                        success=_success,
                        latency_ms=_latency_ms,
                    )
            return
        if self._http_client is None:
            return
        roles_path = str(ws.roles_dir) if ws and hasattr(ws, "roles_dir") and ws.roles_dir.is_dir() else None
        tpl_dir = str(ws.templates_dir) if ws and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir() else None
        job = JobSpec(
            job_id=f"EXEC-{todo.todo_id}",
            todo_id=todo.todo_id,
            playbook=playbook,
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type=_safe_str(todo, "work_type", "unknown") or "unknown",
            resource_profile=_safe_str(todo, "resource_profile", "low_resource") or "low_resource",
            model_profile=resolved_model_profile,
            prompt_profile=resolved_prompt_profile,
            plan_artifact=_safe_str(todo, "plan_artifact"),
            prompt_text=prompt_text,
            skill_body=skill_body,
            budget_context=budget_context,
            project_id=project_id_val,
            artifact_dir=str(ws.artifacts_dir) if ws and hasattr(ws, "artifacts_dir") else None,
            vars_namespace_refs=list(shared_vars.keys()) if shared_vars else [],
            ansible_roles_path=roles_path,
            templates_dir=tpl_dir,
            human_input=await self._resolve_human_input_for_todo(todo.todo_id),
        )
        resp = await self._http_client.post(
            f"{self.worker_base_url}/jobs/execute",
            json=job.model_dump(mode="json"),
        )
        await self._persist_task_return(
            todo,
            job,
            resp,
            _task_return_repo_override=eff_task_return_repo,
            _session_override=eff_session,
        )
        if self._run_recorder is not None:
            with contextlib.suppress(Exception):
                self._run_recorder.record(
                    job_id,
                    {
                        "type": "dispatch_completed",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "success": True,
                        "playbook": playbook,
                    },
                )
        # B3.1.5 checkpoint — clear-on-persist boundary. The dispatch
        # committed (persist_task_return wrote the task return), so the
        # checkpoint is no longer actionable and MUST be removed so the
        # next boot does not re-run a completed dispatch.
        if self._checkpoint_manager is not None:
            todo_id = _safe_str(todo, "todo_id", "")
            if todo_id:
                with contextlib.suppress(Exception):
                    checkpoint_identity, project_owned = _runtime_work_identity(
                        todo,
                        self._tick_project_id,
                    )
                    if not project_owned:
                        raise ValueError(
                            "dispatch checkpoint clear requires an explicit project owner"
                        )
                    self._checkpoint_manager.clear(
                        todo_id,
                        project_id=checkpoint_identity.project_id,
                        shard_id=checkpoint_identity.resume_shard_id,
                    )
