"""Managed self-improvement dispatch, recovery, and self-update."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.repository import ConcurrencyError, TaskReturnRepository, TodoRepository
from general_ludd.event_loop import task_routing as _task_routing
from general_ludd.event_loop.managed_self_improve_dispatch import bind_local_plan as _bind_approved_local_plan
from general_ludd.event_loop.managed_self_improve_dispatch import (
    configured_execution_mode as _configured_self_improve_execution_mode,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    decode_worker_response as _decode_managed_worker_response,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    resolve_repository_binding as _resolve_approved_repository_binding,
)
from general_ludd.event_loop.managed_self_improve_dispatch import serialize_run_result as _serialize_managed_run_result
from general_ludd.event_loop.managed_self_improve_dispatch import validate_approved_plan as _validate_managed_plan
from general_ludd.event_loop.managed_self_improve_dispatch import (
    validate_worker_result as _validate_managed_worker_result,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    worker_rejection_reason as _managed_worker_rejection_reason,
)
from general_ludd.event_loop.review_orchestration import safe_string_attribute as _safe_str
from general_ludd.projects.repository_binding import ProjectRepositoryBinding
from general_ludd.reload.self_improve import SelfImprovementWorkflow
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.project_identity import ProjectWorkIdentity
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan

if TYPE_CHECKING:
    # Mixin state is supplied by EventLoop, the sole mutable owner.
    _DynamicState = Any

logger = logging.getLogger(__name__)
_WORK_TYPE_PLAYBOOK_MAP = _task_routing.WORK_TYPE_PLAYBOOK_MAP

class SelfImproveLifecycleMixin:
    """Managed self-improvement dispatch, recovery, and self-update."""

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any:
            """Type-only access to state owned by the facade."""
            ...

    async def _dispatch_managed_self_improve(
        self,
        todo: Any,
        *,
        project_id: str | None,
        task_return_repo: TaskReturnRepository | None,
        session: AsyncSession | None,
    ) -> None:
        """Run one immutable approved plan without a generic dispatch fallback."""
        validation = _validate_managed_plan(todo, project_id)
        if validation.plan is None:
            await self._persist_managed_self_improve_return(
                todo,
                project_id=project_id,
                reason=validation.rejection_reason or "invalid_plan_artifact",
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        plan = validation.plan
        todo_id = validation.todo_id
        assert project_id is not None
        execution_mode = _configured_self_improve_execution_mode(self.config)
        if execution_mode is None:
            await self._persist_managed_self_improve_return(
                todo,
                project_id=project_id,
                reason="invalid_execution_mode",
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        binding, binding_reason = _resolve_approved_repository_binding(
            plan,
            project_id,
            execution_mode,
            self._resolve_managed_self_improve_binding,
        )
        if binding_reason is not None:
            await self._persist_managed_self_improve_return(
                todo,
                project_id=project_id,
                reason=binding_reason,
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        if execution_mode == "worker":
            await self._dispatch_managed_self_improve_to_worker(
                todo,
                plan=plan,
                binding=binding,
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        repo_root = self._resolve_managed_self_improve_repo(project_id)
        local_plan, local_reason = _bind_approved_local_plan(
            plan,
            repo_root,
            binding,
        )
        if repo_root is None or local_plan is None:
            await self._persist_managed_self_improve_return(
                todo,
                project_id=project_id,
                reason=local_reason or "repository_unavailable",
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        await self._run_local_managed_self_improve(
            todo,
            plan=local_plan,
            repo_root=repo_root,
            project_id=project_id,
            todo_id=todo_id,
            task_return_repo=task_return_repo,
            session=session,
        )

    async def _run_local_managed_self_improve(
        self,
        todo: Any,
        *,
        plan: ApprovedSelfImprovePlan,
        repo_root: Path,
        project_id: str,
        todo_id: str,
        task_return_repo: TaskReturnRepository | None,
        session: AsyncSession | None,
    ) -> None:
        """Run and validate one repository-bound local improvement plan."""
        try:
            async with self._self_improve_run_lock:
                if self._self_improve_executor is not None:
                    result = await self._self_improve_executor.run_async(repo_root, plan)
                else:
                    managed_runner = self._self_improve_runner_factory(repo_root)
                    result = await self._bounded_to_thread(managed_runner.run, plan)
        except Exception as exc:
            logger.warning(
                "Managed self-improvement failed for todo %s (%s)",
                todo_id,
                type(exc).__name__,
            )
            await self._persist_managed_self_improve_return(
                todo,
                project_id=project_id,
                reason="managed_execution_failed",
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        try:
            result_summary, exit_code = _serialize_managed_run_result(plan, result)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Managed self-improvement returned an invalid result for todo %s (%s)",
                todo_id,
                type(exc).__name__,
            )
            await self._persist_managed_self_improve_return(
                todo,
                project_id=project_id,
                reason="managed_result_invalid",
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        await self._persist_managed_self_improve_return(
            todo,
            project_id=project_id,
            result_summary=result_summary,
            exit_code=exit_code,
            task_return_repo=task_return_repo,
            session=session,
        )

    async def _dispatch_managed_self_improve_to_worker(
        self,
        todo: Any,
        *,
        plan: ApprovedSelfImprovePlan,
        binding: ProjectRepositoryBinding | None,
        task_return_repo: TaskReturnRepository | None,
        session: AsyncSession | None,
    ) -> None:
        """Dispatch one path-independent approved plan to a configured worker."""
        if self._http_client is None or binding is None:
            await self._persist_managed_self_improve_return(
                todo,
                project_id=plan.project_id,
                reason="worker_unavailable",
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        job = JobSpec(
            job_id=f"EXEC-{todo.todo_id}",
            todo_id=todo.todo_id,
            playbook=_WORK_TYPE_PLAYBOOK_MAP["self_improve"],
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type="self_improve",
            resource_profile=(
                _safe_str(todo, "resource_profile", "local_heavy") or "local_heavy"
            ),
            plan_artifact=plan.to_json(),
            project_id=plan.project_id,
            repository_binding_digest=binding.digest,
        )
        try:
            response = await self._http_client.post(
                f"{self.worker_base_url}/jobs/execute",
                json=job.model_dump(mode="json"),
            )
            data, status_code = await _decode_managed_worker_response(response)
        except Exception as exc:
            logger.warning(
                "Managed self-improvement worker dispatch failed for todo %s (%s)",
                todo.todo_id,
                type(exc).__name__,
            )
            await self._persist_managed_self_improve_return(
                todo,
                project_id=plan.project_id,
                reason="worker_dispatch_failed",
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        rejection_reason = _managed_worker_rejection_reason(data, status_code)
        if rejection_reason is not None:
            await self._persist_managed_self_improve_return(
                todo,
                project_id=plan.project_id,
                reason=rejection_reason,
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        try:
            _validate_managed_worker_result(plan, data)
        except (TypeError, ValueError):
            await self._persist_managed_self_improve_return(
                todo,
                project_id=plan.project_id,
                reason="managed_result_invalid",
                task_return_repo=task_return_repo,
                session=session,
            )
            return
        await self._persist_task_return(
            todo,
            job,
            data,
            _task_return_repo_override=task_return_repo,
            _session_override=session,
        )

    def _resolve_managed_self_improve_binding(
        self,
        project_id: str,
    ) -> ProjectRepositoryBinding | None:
        """Build a trusted path-independent binding from current project state."""
        manager = self._project_manager
        getter = getattr(manager, "get_project", None)
        if not callable(getter):
            return None
        try:
            project = getter(project_id)
            if project is None or not getattr(project, "active", True):
                return None
            return ProjectRepositoryBinding.for_project(
                project_id=project_id,
                workspace_path=getattr(project, "workspace_path", "") or project_id,
                repo_url=getattr(project, "repo_url", "") or "",
            )
        except (TypeError, ValueError):
            return None

    def _resolve_managed_self_improve_repo(self, project_id: str) -> Path | None:
        """Resolve one project-owned canonical repository without fallbacks."""
        workspaces = (
            self._project_workspace
            if isinstance(self._project_workspace, dict)
            else None
        )
        if workspaces is None:
            return None
        workspace = workspaces.get(project_id)
        repo_dir = getattr(workspace, "repo_dir", None)
        if repo_dir is None:
            return None
        try:
            repo_root = Path(repo_dir).resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        return repo_root if repo_root.is_dir() else None

    async def _persist_managed_self_improve_return(
        self,
        todo: Any,
        *,
        project_id: str | None,
        task_return_repo: TaskReturnRepository | None,
        session: AsyncSession | None,
        reason: str | None = None,
        result_summary: str | None = None,
        exit_code: int = 1,
    ) -> None:
        """Persist a managed result through the existing TaskReturn pipeline."""
        if result_summary is None:
            result_summary = json.dumps(
                {
                    "accepted": False,
                    "kind": "managed_self_improve",
                    "reason": reason or "managed_execution_failed",
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        job = JobSpec(
            job_id=f"EXEC-{todo.todo_id}",
            todo_id=todo.todo_id,
            playbook=_WORK_TYPE_PLAYBOOK_MAP["self_improve"],
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type="self_improve",
            resource_profile=(
                _safe_str(todo, "resource_profile", "local_heavy")
                or "local_heavy"
            ),
            plan_artifact=_safe_str(todo, "plan_artifact"),
            project_id=project_id,
        )
        await self._persist_task_return(
            todo,
            job,
            {
                "return_id": f"RET-{job.job_id}",
                "exit_code": exit_code,
                "result_summary": result_summary,
            },
            _task_return_repo_override=task_return_repo,
            _session_override=session,
        )

    async def _resume_interrupted_dispatches(self) -> None:
        """B3.1.5: hydrate crash-interrupted dispatches on boot, re-enqueue.

        Called once from :meth:`run_forever` before the tick loop starts. For
        every checkpoint found on disk whose todo is NOT already COMPLETED,
        mark it resumed (emits the observability event) and re-enqueue the
        todo via the standard in-process inbound queue if wired. If no queue
        is wired, the resume is logged only — a single-process EventLoop has
        no separate worker to re-dispatch onto, and the standard tick will
        pick the todo up by its normal scheduling path once its status allows.

        Best-effort end to end: any failure here is logged and the loop
        continues — a corrupt checkpoint must never prevent boot.
        """
        if self._checkpoint_manager is None:
            return
        try:
            interrupted = self._checkpoint_manager.list_interrupted()
        except Exception:
            logger.exception("B3.1.5: list_interrupted raised; skipping resume")
            return
        if not interrupted:
            return
        if self._todo_repo is None:
            logger.error(
                "B3.1.5: todo repository unavailable; refusing checkpoint resume"
            )
            return
        for snap in interrupted:
            state = snap.dispatch_state
            project_id = getattr(state, "project_id", None)
            shard_id = getattr(state, "resume_shard_id", None)
            state_todo_id = getattr(state, "todo_id", None)
            if (
                state is None
                or not isinstance(project_id, str)
                or not project_id
                or not isinstance(state_todo_id, str)
                or state_todo_id != snap.task_id
            ):
                logger.error(
                    "B3.1.5: checkpoint %s lacks an exact project identity; skipping",
                    snap.task_id,
                )
                continue
            try:
                identity = ProjectWorkIdentity(project_id, state_todo_id)
            except ValueError:
                logger.exception(
                    "B3.1.5: invalid checkpoint identity for %s",
                    snap.task_id,
                )
                continue
            if shard_id != identity.resume_shard_id:
                logger.error(
                    "B3.1.5: checkpoint shard mismatch for %s; skipping",
                    snap.task_id,
                )
                continue
            try:
                todo = await self._todo_repo.get_by_id(
                    snap.task_id,
                    project_id=project_id,
                )
            except Exception:
                logger.exception(
                    "B3.1.5: scoped status lookup failed for %s; refusing resume",
                    snap.task_id,
                )
                continue
            if todo is None:
                logger.error(
                    "B3.1.5: scoped todo %s is missing; refusing resume",
                    snap.task_id,
                )
                continue
            todo_project_id = _safe_str(todo, "project_id")
            if todo_project_id is not None and todo_project_id != project_id:
                logger.error(
                    "B3.1.5: scoped todo owner mismatch for %s; refusing resume",
                    snap.task_id,
                )
                continue
            status = str(getattr(todo, "status", ""))
            if status == TodoStatus.COMPLETE.value:
                with contextlib.suppress(Exception):
                    self._checkpoint_manager.clear(
                        snap.task_id,
                        project_id=project_id,
                        shard_id=shard_id,
                    )
                continue
            # ACTIVE can still have a live executor. Only the lease-recovery
            # path may prove termination and return it to QUEUED; startup never
            # reuses the stale holder token embedded in the checkpoint.
            if status != TodoStatus.QUEUED.value:
                logger.info(
                    "B3.1.5: checkpoint %s deferred in status=%s",
                    snap.task_id,
                    status or "unknown",
                )
                continue
            phase = state.phase_marker
            try:
                claimed = self._checkpoint_manager.claim_resume(
                    snap.task_id,
                    project_id=project_id,
                    shard_id=shard_id,
                    owner_id=self._lease_owner_id,
                    ttl_seconds=self._execution_lease_timing()[0],
                )
            except Exception:
                logger.exception(
                    "B3.1.5: resume ownership failed for todo %s",
                    snap.task_id,
                )
                continue
            if not claimed:
                logger.info(
                    "B3.1.5: resume deferred; shard already owned for todo %s",
                    snap.task_id,
                )
                continue
            self._checkpoint_manager.mark_resumed(snap.task_id, phase=phase)
            logger.info(
                "B3.1.5: resumed dispatch for todo %s from phase=%s",
                snap.task_id,
                phase,
            )

    def _make_daemon_health_probe(self) -> Callable[[], bool]:
        """Build an in-process health probe for a code-tier hot-rotation.

        Returns a callable suitable for
        :meth:`SelfImprovementWorkflow.set_code_target`'s ``health_check``. The
        probe reads ``self._daemon_state`` (the dict shared with ``app.state``)
        for a ``_degraded`` flag — the same flag the daemon sets on startup
        failure (``daemon.py:1216``). This is intentionally NOT an HTTP
        ``/readyz`` call: a reload-induced regression must be observable from
        the same process without a network round-trip, mirroring the contract
        documented in :mod:`reload.hot_reloader` (``health_check`` returns False
        when ``app.state._degraded`` is set).

        When ``self._daemon_state`` is None the health of the reload CANNOT be
        observed, so the probe FAILS CLOSED (returns False): an unverifiable
        code reload is rolled back by ``reload_code_module`` rather than kept.
        This is the recoverability guarantee — "if it can't be shown to work,
        roll it back." Set ``self_improve.allow_unverified_reload: true`` to opt
        back into the legacy fail-OPEN behavior (e.g. an embedded EventLoop with
        no daemon health surface that still wants reloads to land).
        """
        state = self._daemon_state
        si_cfg = self.config.get("self_improve", {}) if isinstance(self.config, dict) else {}
        allow_unverified = bool(si_cfg.get("allow_unverified_reload", False)) if isinstance(si_cfg, dict) else False

        def _probe() -> bool:
            if state is None:
                # Cannot verify health → fail CLOSED (roll back) unless the
                # operator explicitly opted into unverified reloads.
                return allow_unverified
            # Dict-style access (the EventLoop holds the shared dict) and
            # attribute-style access (a Starlette ``app.state``-like object may
            # be wired in tests) are both acceptable surfaces.
            degraded: object = state.get("_degraded") if isinstance(state, dict) else getattr(state, "_degraded", None)
            return not bool(degraded)

        return _probe

    async def _apply_self_update_code(
        self,
        todo: Any,
        *,
        _session_override: AsyncSession | None = None,
    ) -> None:
        """Phase-2 Step 6: arm ``set_code_target`` + ``reload_if_needed``.

        Reads the ``module:<name>`` and ``candidate:<path>`` tags the intake
        half (``routers/self_update.py``) writes onto a code-tier self-update
        todo, arms a real leaf-module hot-rotation on a
        :class:`SelfImprovementWorkflow`, fires ``reload_if_needed``, and
        transitions the todo COMPLETE or FAILED based on the reload verdict.

        Fail-closed at every stage:

          * A missing ``module:`` or ``candidate:`` tag → todo → FAILED, no
            reload attempted. The intake half MUST arm both for a code-tier
            apply; their absence means the request was malformed and running a
            partial reload would be unsafe.
          * A reload verdict that is not ``"success"`` → todo → FAILED. This
            includes ``"no_op"`` (the ReloadManager fallback that performs no
            real swap — BUG#2): a self-update that touched nothing cannot be
            marked COMPLETE, or an approved code-tier request would silently
            no-op forever. Only an actual hot-rotation whose health gate
            passed counts as applied.

        The todo transition uses a guarded compare-and-set (version check) via
        :meth:`TodoRepository.transition`; a lost version race logs and swallows
        rather than crashing the tick — the todo's persisted state is the
        authoritative record either way.
        """
        from general_ludd.reload.self_improve import ApplyResult
        from general_ludd.schemas.todo import TodoStatus

        todo_id = _safe_str(todo, "todo_id", "") or ""
        version = int(getattr(todo, "version", 1) or 1)

        # Per-call todo_repo: the isolated dispatch path passes its own session
        # via _session_override; the sequential path reuses the tick-level
        # self._todo_repo (already bound to the tick session).
        if _session_override is not None:
            todo_repo: TodoRepository | None = TodoRepository(_session_override)
        else:
            todo_repo = self._todo_repo

        # Resolve the module + candidate tags. Missing either is fail-closed.
        tags = getattr(todo, "tags", None) or []
        module_name: str | None = None
        candidate_path: str | None = None
        base_path: str | None = None
        for tag in tags:
            if not isinstance(tag, str):
                continue
            if module_name is None and tag.startswith("module:"):
                module_name = tag.split(":", 1)[1].strip()
            elif candidate_path is None and tag.startswith("candidate:"):
                candidate_path = tag.split(":", 1)[1].strip()
            elif base_path is None and tag.startswith("base:"):
                # Optional anti-clobber base snapshot the candidate was generated
                # against; when present, activates the 3-way merge so a concurrent
                # edit to the live module is refused rather than clobbered.
                base_path = tag.split(":", 1)[1].strip()

        if not module_name or not candidate_path:
            logger.error(
                "Step 6: self_update todo %s missing module/candidate tags (module=%r, candidate=%r) — failing closed",
                todo_id,
                module_name,
                candidate_path,
            )
            await self._transition_self_update_todo(
                todo_repo,
                todo_id,
                TodoStatus.FAILED,
                version,
            )
            return

        workflow = SelfImprovementWorkflow()
        workflow.set_code_target(
            module_name=module_name,
            candidate_source_path=candidate_path,
            health_check=self._make_daemon_health_probe(),
            base_source_path=base_path,
        )

        # Build an ApplyResult that requests the reload. The workflow's
        # reload_if_needed gates on apply_result.reload_needed; we do NOT bypass
        # validation here — a code-tier candidate must already have been
        # validated upstream by the time it reaches the backlog (the intake half
        # can refuse to enqueue unvalidated code-tier work). Marking
        # validation_passed=True asserts "the candidate was vetted before
        # enqueue"; the live health gate inside reload_if_needed is the second
        # line of defense.
        apply_result = ApplyResult(
            todo_id=todo_id,
            applied=True,
            reload_needed=True,
            validation_passed=True,
        )

        try:
            reload_result = workflow.reload_if_needed(apply_result)
        except Exception as exc:
            logger.error(
                "Step 6: reload_if_needed raised for todo %s (module=%s): %s",
                todo_id,
                module_name,
                exc,
            )
            await self._transition_self_update_todo(
                todo_repo,
                todo_id,
                TodoStatus.FAILED,
                version,
            )
            return

        reload_status = getattr(reload_result, "status", "")
        reload_ok = reload_status == "success"
        logger.info(
            "Step 6: self_update todo %s reload verdict=%s ok=%s (module=%s)",
            todo_id,
            reload_status,
            reload_ok,
            module_name,
        )

        target_status = TodoStatus.COMPLETE if reload_ok else TodoStatus.FAILED
        await self._transition_self_update_todo(
            todo_repo,
            todo_id,
            target_status,
            version,
        )

        if self._daemon_state is not None and isinstance(self._daemon_state, dict):
            # P3 (unbounded ledger growth): this audit list grows by one entry per
            # successful self_update reload and was NEVER pruned — over a long-lived
            # daemon it accumulates without bound. Bound it to the last
            # ``_MAX_SELF_UPDATE_APPLIES`` entries. It stays a plain ``list`` (NOT a
            # deque) so it remains JSON-serialisable and index-/slice-able for the
            # daemon consumers that surface it via app.state; we trim from the FRONT
            # so the most recent applies (the ones an operator cares about) survive.
            applies = self._daemon_state.setdefault("self_update_applies", [])
            applies.append(
                {
                    "todo_id": todo_id,
                    "module": module_name,
                    "candidate": candidate_path,
                    "verdict": reload_status,
                    "ok": reload_ok,
                }
            )
            if len(applies) > self._MAX_SELF_UPDATE_APPLIES:
                del applies[: len(applies) - self._MAX_SELF_UPDATE_APPLIES]

    async def _transition_self_update_todo(
        self,
        todo_repo: TodoRepository | None,
        todo_id: str,
        target: TodoStatus,
        expected_version: int,
    ) -> None:
        """Guarded CAS transition of a self_update todo; never raises into the tick.

        A lost version race (``ConcurrencyError``) or a missing todo row is
        logged and swallowed: the persisted row is the source of truth, and a
        concurrent writer already moved the state — re-applying would clobber
        it. This mirrors the reconcile phase's CAS discipline.
        """
        if todo_repo is None:
            logger.warning(
                "Step 6: no todo_repo available to transition %s -> %s",
                todo_id,
                target.value,
            )
            return
        try:
            await todo_repo.transition(todo_id, target, expected_version)
        except ConcurrencyError as exc:
            logger.info(
                "Step 6: lost version race transitioning %s -> %s: %s",
                todo_id,
                target.value,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Step 6: transition failed for %s -> %s: %s",
                todo_id,
                target.value,
                exc,
            )

    async def _dispatch_validate_job(
        self,
        todo: Any,
        *,
        decision_id: str | None = None,
        worktree_path: str | None = None,
        test_commands: tuple[str, ...] = (),
        timeout_seconds: float = 300.0,
    ) -> bool:
        """Run one canonical worker validation and persist its honest result."""
        import hashlib as _hashlib
        import inspect as _inspect

        import httpx as _httpx

        if self._http_client is None:
            logger.warning("Validation worker unavailable for todo %s", todo.todo_id)
            return False
        decision_digest = (
            _hashlib.sha256(decision_id.encode("utf-8")).hexdigest()[:16]
            if decision_id is not None
            else None
        )
        job_id = f"VALIDATE-{todo.todo_id}"
        if decision_digest is not None:
            job_id = f"{job_id}-{decision_digest}"
        budget_context: dict[str, object] = {}
        if worktree_path is not None and test_commands:
            budget_context = {
                "worktree_path": worktree_path,
                "test_commands": list(test_commands),
            }
        job = JobSpec(
            job_id=job_id,
            todo_id=todo.todo_id,
            playbook="validate_task.yml",
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type="validation",
            project_id=getattr(todo, "project_id", None),
            budget_context=budget_context,
            timeout=timeout_seconds,
        )
        try:
            response = await self._http_client.post(
                f"{self.worker_base_url}/jobs/validate",
                json=job.model_dump(mode="json"),
                timeout=timeout_seconds,
            )
            status_code = getattr(response, "status_code", None)
            if not isinstance(status_code, int) or not 200 <= status_code < 300:
                logger.warning(
                    "Validation worker rejected todo %s: status=%s",
                    todo.todo_id,
                    status_code,
                )
                return False
            body_reader = getattr(response, "json", None)
            if not callable(body_reader):
                return False
            data = body_reader()
            if _inspect.isawaitable(data):
                data = await data
        except (TimeoutError, _httpx.TimeoutException):
            logger.warning("Validation worker timed out for todo %s", todo.todo_id)
            return False
        except (_httpx.HTTPError, TypeError, ValueError):
            logger.warning(
                "Validation worker returned an unusable response for todo %s",
                todo.todo_id,
                exc_info=True,
            )
            return False
        except Exception:
            logger.warning(
                "Validation worker unavailable for todo %s",
                todo.todo_id,
                exc_info=True,
            )
            return False
        if not self._valid_validation_response(data, todo, job):
            logger.warning("Validation worker response malformed for todo %s", todo.todo_id)
            return False
        return await self._persist_task_return(todo, job, data)

    @staticmethod
    def _valid_validation_response(data: object, todo: Any, job: JobSpec) -> bool:
        """Require the canonical worker identity and reviewable result fields."""
        if not isinstance(data, dict):
            return False
        exit_code = data.get("exit_code")
        return (
            data.get("return_id") == f"RET-{job.job_id}"
            and data.get("todo_id") == todo.todo_id
            and data.get("job_id") == job.job_id
            and isinstance(exit_code, int)
            and not isinstance(exit_code, bool)
            and isinstance(data.get("result_summary"), str)
        )

    async def _persist_task_return(
        self,
        todo: Any,
        job: JobSpec,
        resp: Any,
        *,
        _task_return_repo_override: TaskReturnRepository | None = None,
        _session_override: AsyncSession | None = None,
    ) -> bool:
        import inspect as _inspect

        eff_repo = _task_return_repo_override if _task_return_repo_override is not None else self._task_return_repo
        eff_session = _session_override if _session_override is not None else self._active_session
        real_session = eff_session is not None and issubclass(
            type(eff_session),
            AsyncSession,
        )
        if eff_repo is None:
            return False
        try:
            async def _persist_and_advance() -> bool:
                body = getattr(resp, "json", None)
                if callable(body):
                    data = body()
                    if _inspect.isawaitable(data):
                        data = await data
                elif isinstance(resp, dict):
                    data = resp
                else:
                    return False
                if not isinstance(data, dict):
                    return False
                await eff_repo.create(
                    data={
                        "return_id": data.get("return_id", f"RET-{job.job_id}"),
                        "todo_id": todo.todo_id,
                        "job_id": job.job_id,
                        "playbook": job.playbook,
                        "queue": job.queue,
                        "work_type": job.work_type,
                        "resource_profile": job.resource_profile,
                        "exit_code": data.get("exit_code", 0),
                        "result_summary": data.get("result_summary", ""),
                        "project_id": job.project_id,
                    }
                )
                expected_version = getattr(todo, "version", None)
                if real_session:
                    if not isinstance(expected_version, int):
                        raise RuntimeError(
                            "task return persistence requires todo ownership"
                        )
                    await TodoRepository(
                        cast(AsyncSession, eff_session)
                    ).transition(
                        todo.todo_id,
                        TodoStatus.AWAITING_RESULT,
                        expected_version,
                        project_id=job.project_id,
                    )
                elif self._todo_repo is not None and not isinstance(
                    self._todo_repo,
                    TodoRepository,
                ):
                    # Test/embedded repository boundaries may be protocol fakes
                    # without a SQLAlchemy session; retain their observable call.
                    await self._todo_repo.transition(
                        todo.todo_id,
                        TodoStatus.AWAITING_RESULT,
                        expected_version,
                        project_id=job.project_id,
                    )
                if eff_session is not None:
                    await eff_session.flush()
                return True

            if real_session:
                # A SAVEPOINT makes TaskReturn creation and todo advancement one
                # unit without rolling back unrelated work in the tick session.
                assert eff_session is not None
                async with eff_session.begin_nested():
                    persisted = await _persist_and_advance()
            else:
                persisted = await _persist_and_advance()
            if persisted:
                logger.info("Persisted TaskReturn for todo %s", todo.todo_id)
            return persisted
        except Exception as exc:
            logger.warning("Failed to persist task return for %s: %s", todo.todo_id, exc)
            return False
