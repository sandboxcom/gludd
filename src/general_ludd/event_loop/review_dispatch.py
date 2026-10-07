"""Task-return review claiming, dispatch, and persistence."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from general_ludd.db.models import TaskDecisionModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.review_orchestration import safe_string_attribute as _safe_str
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturnStatus
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan
from general_ludd.self_improve.promotion import ManagedPromotionReceipt

if TYPE_CHECKING:
    # Mixin state is supplied by EventLoop, the sole mutable owner.
    _DynamicState = Any

logger = logging.getLogger(__name__)

class ReviewDispatchMixin:
    """Task-return review claiming, dispatch, and persistence."""

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any:
            """Type-only access to state owned by the facade."""
            ...

    async def _phase_claim_unreviewed_task_returns(self) -> None:
        if self._task_return_repo is None:
            return
        project_id = self._tick_project_id
        claimed = await self._task_return_repo.claim_unreviewed(project_id=project_id)
        reviewable: list[Any] = []
        for task_return in claimed:
            if self._todo_repo is None or not issubclass(
                type(self._todo_repo),
                TodoRepository,
            ):
                # Protocol fakes and externally managed repositories retain the
                # pre-existing review contract; SQL-backed production repos use
                # the atomic lifecycle advancement below.
                reviewable.append(task_return)
                continue
            todo_id = getattr(task_return, "todo_id", None)
            return_project_id = getattr(task_return, "project_id", None)
            if not isinstance(return_project_id, str):
                return_project_id = project_id
            try:
                todo = (
                    await self._todo_repo.get_by_id(
                        todo_id,
                        project_id=return_project_id,
                    )
                    if isinstance(todo_id, str)
                    else None
                )
                if todo is None:
                    raise ValueError("task return has no matching todo")
                current = TodoStatus(todo.status)
                if current in {TodoStatus.AWAITING_RESULT, TodoStatus.ACTIVE}:
                    await self._todo_repo.transition(
                        todo.todo_id,
                        TodoStatus.REVIEWING_RETURN,
                        todo.version,
                        project_id=return_project_id,
                    )
                elif current is not TodoStatus.REVIEWING_RETURN:
                    raise ValueError("task return todo is not reviewable")
            except Exception as exc:
                # Release only this return's guarded claim. The enclosing tick
                # transaction commits the reset with successful sibling claims.
                task_return.status = TaskReturnStatus.CREATED.value
                if hasattr(task_return, "updated_at"):
                    task_return.updated_at = datetime.now(UTC)
                logger.error(
                    "Return review claim released because todo ownership could not "
                    "advance (error_type=%s)",
                    type(exc).__name__,
                )
                continue
            reviewable.append(task_return)
        if self._active_session is not None:
            await self._active_session.flush()
        self._tick_state["claimed_returns"] = reviewable

    async def _phase_dispatch_return_review_jobs(self) -> None:
        claimed = self._tick_state.get("claimed_returns", [])
        if self._budget_guard is not None:
            check = self._budget_guard.check_all_limits(estimated_cost=self._estimated_dispatch_cost(len(claimed)))
            if not check["allowed"]:
                logger.warning("Budget exceeded, skipping return review dispatch: %s", check["reason"])
                self._tick_metrics["returns_reviewed"] = 0
                return
        for tr in claimed:
            await self._dispatch_review_job(tr)
        self._tick_metrics["returns_reviewed"] = len(claimed)

    async def _release_review_claim(self, tr: Any, reason: str = "") -> None:
        # Release the return claim so a retry remains possible, while moving the
        # associated todo to BLOCKED. A review infrastructure failure must be
        # visible to operators instead of silently re-queueing executable work.
        if self._active_session is not None:
            with contextlib.suppress(Exception):
                tr.status = "created"
            if hasattr(tr, "updated_at"):
                with contextlib.suppress(Exception):
                    tr.updated_at = datetime.now(UTC)
            with contextlib.suppress(Exception):
                await self._active_session.flush()
        logger.warning(
            "Released review claim for return %s: %s",
            getattr(tr, "return_id", "?"),
            reason or "unknown",
        )

        todo_id = getattr(tr, "todo_id", None)
        if not reason or self._todo_repo is None or not isinstance(todo_id, str):
            return
        project_id = getattr(tr, "project_id", None)
        if not isinstance(project_id, str):
            project_id = None
        try:
            todo = await self._todo_repo.get_by_id(todo_id, project_id=project_id)
            if todo is None:
                logger.error("Cannot block missing todo %s after review failure", todo_id)
                return
            await self._todo_repo.transition(
                todo_id,
                TodoStatus.BLOCKED,
                todo.version,
                project_id=project_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to block todo %s after review failure: %s",
                todo_id,
                type(exc).__name__,
            )

    async def _dispatch_review_job(self, tr: Any) -> None:
        # H4 (W3.2): when a gateway-backed reviewer is wired, review in-process
        # and route the decision through apply_decision. Failure escalates the
        # todo — it is never silently marked complete / passed through.
        #
        # G11: consensus review is an alternative path gated behind the
        # ``consensus_review.enabled`` config flag. When enabled AND a
        # ConsensusReviewer is wired, the multi-agent debate replaces the
        # single-model reviewer for this tick.
        _has_standard = self._reviewer is not None
        _has_consensus = (
            isinstance(self.config, dict)
            and bool(self.config.get("consensus_review", {}).get("enabled", False))
            and self._consensus_reviewer is not None
        )
        review_cfg = self.config.get("review", {}) if isinstance(self.config, dict) else {}
        if (_has_standard or _has_consensus) and self._active_session is not None and self._todo_repo is not None:
            in_process_timeout = float(review_cfg.get("in_process_timeout", 600.0))
            try:
                await asyncio.wait_for(
                    self._review_in_process(tr),
                    timeout=in_process_timeout,
                )
            except TimeoutError:
                await self._release_review_claim(
                    tr,
                    f"in-process review timeout after {in_process_timeout:.0f}s",
                )
            return
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
        review_cfg = self.config.get("review", {}) if isinstance(self.config, dict) else {}
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
            review_playbook_timeout = float(review_cfg.get("playbook_timeout", 600.0))
            try:
                runner_result = await asyncio.wait_for(
                    self._bounded_to_thread(
                        self._runner.run_playbook,
                        playbook_name="return_review.yml",
                        private_data_dir=dirs["root"],
                    ),
                    timeout=review_playbook_timeout,
                )
                if runner_result:
                    rc = runner_result.get("rc", 0)
                    if rc != 0:
                        await self._release_review_claim(
                            tr,
                            f"playbook rc={rc} (job_id={job.job_id})",
                        )
                    else:
                        logger.info(
                            "Review playbook completed for return %s (job_id=%s)",
                            getattr(tr, "return_id", "?"),
                            job.job_id,
                        )
            except TimeoutError:
                await self._release_review_claim(
                    tr,
                    f"playbook timeout after {review_playbook_timeout:.0f}s (job_id={job.job_id})",
                )
            except Exception:
                await self._release_review_claim(
                    tr,
                    f"playbook failed (job_id={job.job_id})",
                )
            return
        if self._http_client is None:
            return
        review_http_timeout = float(review_cfg.get("http_timeout", 600.0))
        try:
            resp = await asyncio.wait_for(
                self._http_client.post(
                    f"{self.worker_base_url}/jobs/return-review",
                    json=job.model_dump(mode="json"),
                ),
                timeout=review_http_timeout,
            )
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                await self._release_review_claim(
                    tr,
                    f"HTTP {status} from worker (job_id={job.job_id})",
                )
            else:
                await self._persist_review_response(tr, resp)
        except TimeoutError:
            await self._release_review_claim(
                tr,
                f"HTTP timeout after {review_http_timeout:.0f}s (job_id={job.job_id})",
            )

    def _resolve_repo_root(self, project_id: str | None) -> str | None:
        """Resolve the repository root for a project.

        Lookup priority:
          1. Per-project workspace repo_dir — when self._project_workspace is a
             dict of {pid: ProjectWorkspace} and the workspace's repo_dir is a
             real directory on disk (populated by materialize_project_workspace /
             git-clone at startup).
          2. self.config["repo_root"] — operator-configured fallback (also used
             as the daemon-level default set at startup for single-project use).
          3. None — completion_verifier will fail-closed (unverifiable ≠ verified).

        Does NOT fall through to os.getcwd() here; that default belongs at the
        config-injection site (daemon.py) so the intent is explicit and not
        silently inherited from the process cwd.
        """
        from pathlib import Path as _Path

        workspaces = self._project_workspace if isinstance(self._project_workspace, dict) else None
        if workspaces is not None and project_id is not None:
            ws = workspaces.get(project_id)
            if ws is not None and hasattr(ws, "repo_dir"):
                try:
                    rdir = _Path(str(ws.repo_dir))
                    if rdir.is_dir():
                        return str(rdir)
                except Exception:
                    pass
        return self.config.get("repo_root") if isinstance(self.config, dict) else None

    async def _persist_in_process_decision(
        self,
        tr: Any,
        decision: TaskDecision,
    ) -> None:
        """Commit the review record before any managed promotion side effect."""
        if self._active_session is None:
            raise RuntimeError("review decision persistence requires an active session")
        existing = (
            await self._active_session.execute(
                select(TaskDecisionModel).where(
                    TaskDecisionModel.return_id == decision.return_id
                )
            )
        ).scalar_one_or_none()
        project_id = getattr(tr, "project_id", None)
        if not isinstance(project_id, str):
            project_id = None
        if existing is not None:
            expected = (
                project_id,
                decision.matched_todo_id,
                decision.decision,
                float(decision.confidence),
                json.dumps(decision.evidence_refs),
                json.dumps(decision.todo_updates),
                json.dumps(decision.child_todos),
                json.dumps(decision.validation_requests),
                json.dumps(decision.git_requests),
                json.dumps(decision.audit_notes),
                json.dumps(decision.policy_flags),
            )
            actual = (
                existing.project_id,
                existing.matched_todo_id,
                existing.decision,
                float(existing.confidence),
                existing.evidence_refs,
                existing.todo_updates,
                existing.child_todos,
                existing.validation_requests,
                existing.git_requests,
                existing.audit_notes,
                existing.policy_flags,
            )
            if actual != expected:
                raise ValueError(
                    "persisted review decision cannot be rebound to new content"
                )
            await self._active_session.commit()
            return
        self._active_session.add(
            TaskDecisionModel(
                return_id=decision.return_id,
                project_id=project_id,
                matched_todo_id=decision.matched_todo_id,
                decision=decision.decision,
                confidence=float(decision.confidence),
                evidence_refs=json.dumps(decision.evidence_refs),
                todo_updates=json.dumps(decision.todo_updates),
                child_todos=json.dumps(decision.child_todos),
                validation_requests=json.dumps(decision.validation_requests),
                git_requests=json.dumps(decision.git_requests),
                audit_notes=json.dumps(decision.audit_notes),
                policy_flags=json.dumps(decision.policy_flags),
            )
        )
        await self._active_session.flush()
        await self._active_session.commit()

    async def _ensure_managed_self_improve_promotion(
        self,
        tr: Any,
        todo: Any,
    ) -> ManagedPromotionReceipt:
        """Return a Git-verified durable receipt for one reviewed result."""
        if self._active_session is None:
            raise RuntimeError("managed promotion requires an active session")
        todo_id = getattr(todo, "todo_id", None)
        project_id = getattr(todo, "project_id", None)
        plan_artifact = getattr(todo, "plan_artifact", None)
        result_artifact = getattr(tr, "result_summary", None)
        return_id = getattr(tr, "return_id", None)
        if not isinstance(todo_id, str) or not todo_id:
            raise ValueError("managed promotion requires a todo identity")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("managed promotion requires a project identity")
        if not isinstance(plan_artifact, str) or not plan_artifact:
            raise ValueError("managed promotion requires an approved plan artifact")
        if not isinstance(result_artifact, str) or not result_artifact:
            raise ValueError("managed promotion requires a result artifact")
        if not isinstance(return_id, str) or not return_id:
            raise ValueError("managed promotion requires a return identity")
        repo_root = self._resolve_repo_root(project_id)
        if not isinstance(repo_root, str) or not repo_root:
            raise ValueError("managed promotion requires a canonical repository root")
        owner = (
            f"gludd-promotion:{project_id}:"
            f"{self._self_improve_promotion_instance}"
        )
        coordinator = self._self_improve_promotion_factory(
            self._active_session,
            Path(repo_root),
            owner,
        )
        try:
            plan = ApprovedSelfImprovePlan.from_json(plan_artifact)
        except (TypeError, ValueError):
            plan = None
        if plan is not None and plan.repository_binding_digest:
            binding = self._resolve_managed_self_improve_binding(project_id)
            if binding is None:
                raise ValueError(
                    "managed promotion requires a current repository binding"
                )
            receipt = await coordinator.promote(
                plan_artifact=plan_artifact,
                result_artifact=result_artifact,
                todo_id=todo_id,
                project_id=project_id,
                repo_root=Path(repo_root),
                return_id=return_id,
                repository_binding_digest=binding.digest,
            )
        else:
            receipt = await coordinator.promote(
                plan_artifact=plan_artifact,
                result_artifact=result_artifact,
                todo_id=todo_id,
                project_id=project_id,
                repo_root=Path(repo_root),
                return_id=return_id,
            )
        if not isinstance(receipt, ManagedPromotionReceipt):
            raise ValueError("managed promotion returned an invalid receipt")
        return receipt

    async def _release_managed_review_for_retry(self, tr: Any) -> None:
        """Durably re-open a managed review after a promotion failure."""
        if self._active_session is None:
            return
        tr.status = TaskReturnStatus.CREATED.value
        if hasattr(tr, "updated_at"):
            tr.updated_at = datetime.now(UTC)
        await self._active_session.flush()
        await self._active_session.commit()

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
