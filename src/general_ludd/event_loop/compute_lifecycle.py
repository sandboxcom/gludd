"""Controller evaluation, compute lifecycle, and runnable claims."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.controllers.load_scrape import LoadSnapshot
from general_ludd.controllers.pid import LoadController
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop import task_routing as _task_routing
from general_ludd.event_loop.lease import reclaim_expired_leases, release_lease
from general_ludd.event_loop.review_orchestration import safe_string_attribute as _safe_str
from general_ludd.event_loop.runtime_helpers import runtime_lease_bucket_keys as _runtime_lease_bucket_keys
from general_ludd.rules.engine import Rule, evaluate_rules
from general_ludd.schemas.queue import Queue
from general_ludd.schemas.todo import TodoStatus

if TYPE_CHECKING:
    # Mixin state is supplied by EventLoop, the sole mutable owner.
    _DynamicState = Any

logger = logging.getLogger(__name__)
_compute_todo_estimate = _task_routing.compute_todo_estimate

class ComputeLifecycleMixin:
    """Controller evaluation, compute lifecycle, and runnable claims."""

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any:
            """Type-only access to state owned by the facade."""
            ...

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
                try:
                    active_jobs = await self._todo_repo.count_active()
                except Exception:
                    # Best-effort load signal: a count failure falls back to 0
                    # (treated as idle). Log at debug so a broken query is visible
                    # without changing the fall-through behaviour.
                    logger.debug(
                        "count_active failed; using active_jobs=0",
                        exc_info=True,
                    )
            snapshot = LoadSnapshot(
                loadavg_1m=load_1,
                loadavg_5m=load_5,
                loadavg_10m=load_10,
                logical_cpu_count=cpu_count,
                cpu_percent=cpu_pct,
                memory_available_percent=mem.percent,
                disk_free_percent=disk_free_pct,
                active_jobs=active_jobs,
            )
            outputs = controller.evaluate_snapshot(snapshot, queues)
            self._tick_state["pid_outputs"] = outputs
        except Exception as exc:
            logger.debug("PID evaluation skipped: %s", exc)

    async def _phase_evaluate_rules(self) -> None:
        raw_rules = self.config.get("rules", [])
        rules = [r if isinstance(r, Rule) else Rule(**r) for r in raw_rules]
        # W4c: evaluate rules against the LIVE claimed todos for this tick (set by
        # _phase_claim_runnable_todos, which now runs first per PHASE_ORDER), not the
        # static self.config["todos"] — which is absent at runtime, so rules never fired.
        todos_ctx = self._tick_state.get("claimed_todos", [])
        all_results: list[dict[str, Any]] = []
        for todo_ctx in todos_ctx:
            context = {"todo": todo_ctx}
            actions = evaluate_rules(rules, context)
            if actions:
                all_results.append(
                    {
                        "todo_id": _safe_str(todo_ctx, "todo_id", ""),
                        "actions": [
                            {"rule_id": a.rule_id, "action_type": a.action_type, "params": a.params} for a in actions
                        ],
                    }
                )
        self._tick_state["rule_evaluation_results"] = all_results

    async def _phase_refill_task_buckets(self) -> None:
        if self._active_session is not None:
            reclaimed = await reclaim_expired_leases(self._active_session)
            self._tick_metrics["leases_reclaimed"] = reclaimed
        if self._todo_repo is not None and self._active_session is not None:
            await self._reap_stuck_todos()

    async def _phase_run_scheduler(self) -> None:
        """Promote due SCHEDULED todos (cron + one-shot) on each tick.

        DEFECT 0 fix: ``TodoScheduler`` was previously defined but never invoked,
        so SCHEDULED todos were never promoted and the cron feature was dead
        end-to-end. This phase runs BEFORE ``claim_runnable_todos`` in
        ``PHASE_ORDER`` so a todo promoted SCHEDULED→QUEUED (one-shot) or a freshly
        spawned cron child clone is claimable in the same tick. Uses the
        tick-scoped repo/session; ``tick()`` commits once after ``_run_phases``.
        """
        if self._todo_repo is None or self._active_session is None:
            return
        from general_ludd.event_loop.scheduler import TodoScheduler

        promoted, spawned = await TodoScheduler(self._todo_repo).tick()
        self._tick_metrics["scheduled_promoted"] = promoted
        self._tick_metrics["scheduled_spawned"] = spawned

    async def _phase_sdlc_gate(self) -> None:
        if not isinstance(self.config, dict):
            return
        sdlc_cfg = self.config.get("ai_sdlc", {})
        if not sdlc_cfg:
            return
        enforce = sdlc_cfg.get("enforce", False)
        stages = sdlc_cfg.get("pipeline_stages", {})
        results: dict[str, Any] = {
            "enforce": enforce,
            "stages_checked": 0,
            "stages_blocked": 0,
            "stage_results": {},
        }
        for stage_name, stage_spec in stages.items():
            if not isinstance(stage_spec, dict):
                continue
            entry_gates = stage_spec.get("entry_gates", {})
            exit_gates = stage_spec.get("exit_gates", {})
            entry_checks: list[str] = []
            exit_checks: list[str] = []
            stage_result: dict[str, object] = {
                "entry_passed": True,
                "exit_passed": True,
                "entry_checks": entry_checks,
                "exit_checks": exit_checks,
            }
            required_artifact_dir = stage_spec.get("artifact_dir")
            if required_artifact_dir:
                artifact_path = Path(required_artifact_dir)
                if not artifact_path.exists():
                    stage_result["entry_passed"] = False
                    entry_checks.append(f"artifact_dir missing: {required_artifact_dir}")
            for gate_name, gate_spec in entry_gates.items():
                if isinstance(gate_spec, dict) and gate_spec.get("required"):
                    stage_result["entry_passed"] = False
                    entry_checks.append(f"entry gate unsatisfied: {gate_name}")
            for gate_name, gate_spec in exit_gates.items():
                if isinstance(gate_spec, dict) and gate_spec.get("required"):
                    stage_result["exit_passed"] = False
                    exit_checks.append(f"exit gate unsatisfied: {gate_name}")
            if not stage_result["entry_passed"] or not stage_result["exit_passed"]:
                results["stages_blocked"] += 1
            results["stages_checked"] += 1
            results["stage_results"][stage_name] = stage_result
        self._tick_state["sdlc_gate_results"] = results
        if enforce and results["stages_blocked"] > 0:
            logger.warning(
                "SDLC gate: %d/%d stages blocked (enforce=true)",
                results["stages_blocked"],
                results["stages_checked"],
            )

    async def _phase_reconcile_compute_demand(self) -> None:
        """Provision compute only for this tick's durable, fenced claims.

        The production tick commits and closes the claim/lease transaction
        before entering this phase.  A worker that lost the claim therefore has
        no local claim batch and cannot provision or tear down shared compute.
        """
        raw_claimed = self._tick_state.get("claimed_todos", [])
        claimed = list(raw_claimed) if isinstance(raw_claimed, (list, tuple)) else []
        runnable_todos = len(claimed)
        self._tick_metrics["compute_demand_runnable_todos"] = runnable_todos
        if not claimed:
            root_value_for_state = self._resolve_repo_root(self._tick_project_id)
            owned_present = False
            if root_value_for_state is not None:
                root_key = str(Path(root_value_for_state).expanduser().resolve())
                state = self._execution_environment_states.get(root_key)
                owned_present = state is not None and state[0] == "present"
            self._tick_state["compute_ready"] = owned_present
            self._tick_state["compute_demand"] = {
                "state": "retained" if owned_present else "idle",
                "runnable_todos": 0,
                "execution_environment": "present" if owned_present else "unchanged",
            }
            return

        runner = self._runner
        lifecycle_method = (
            getattr(type(runner), "reconcile_execution_environment", None)
            if runner is not None
            else None
        )
        if not callable(lifecycle_method):
            # The caller-owned session compatibility path commits its claim
            # before entering this phase, but deliberately keeps the reusable
            # session attached.  A missing provider lifecycle means there is no
            # compute side effect to fence, even when a playbook-only runner is
            # present; dispatch may use the committed claim.
            self._tick_state["compute_ready"] = True
            self._tick_state["compute_demand"] = {
                "state": "externally_managed",
                "runnable_todos": runnable_todos,
                "execution_environment": "external",
            }
            return

        if self._active_session is not None:
            self._tick_metrics["compute_claim_fence_rejections"] = (
                self._tick_metrics.get("compute_claim_fence_rejections", 0) + 1
            )
            self._tick_state["compute_ready"] = False
            self._tick_state["compute_demand"] = {
                "state": "claim_transaction_open",
                "runnable_todos": runnable_todos,
                "execution_environment": "unchanged",
            }
            logger.error(
                "Compute provisioning skipped while the claim session remains active"
            )
            return

        project_id = self._tick_project_id
        root_value = self._resolve_repo_root(project_id)
        if root_value is None:
            logger.error("Compute demand cannot be reconciled without an exact project root")
            self._tick_state["compute_ready"] = False
            self._tick_state["compute_demand"] = {
                "state": "unknown",
                "runnable_todos": runnable_todos,
                "execution_environment": "preserved",
            }
            return

        execution_environment = self.config.get("execution_environment", {})
        if not isinstance(execution_environment, Mapping):
            logger.error("Compute demand cannot use a non-mapping execution_environment config")
            self._tick_state["compute_ready"] = False
            self._tick_state["compute_demand"] = {
                "state": "unknown",
                "runnable_todos": runnable_todos,
                "execution_environment": "preserved",
            }
            return
        if execution_environment.get("enabled", True) is False:
            self._tick_state["compute_ready"] = True
            self._tick_state["compute_demand"] = {
                "state": "externally_managed",
                "runnable_todos": runnable_todos,
                "execution_environment": "disabled",
            }
            return

        constraints = {
            str(name): value
            for name, value in execution_environment.items()
            if name != "enabled"
        }
        desired_state = "present"
        project_root = Path(root_value).expanduser().resolve()
        root_key = str(project_root)
        fingerprint = (
            desired_state,
            json.dumps(constraints, sort_keys=True, separators=(",", ":"), default=repr),
        )
        self._tick_state["compute_demand"] = {
            "state": "demanded",
            "runnable_todos": runnable_todos,
            "execution_environment": desired_state,
        }
        if self._execution_environment_states.get(root_key) == fingerprint:
            self._tick_state["compute_ready"] = True
            return

        reconcile = runner.reconcile_execution_environment
        try:
            result = await self._bounded_to_thread(
                reconcile,
                state=desired_state,
                project_root=project_root,
                constraints=constraints,
            )
            success = (
                isinstance(result, Mapping)
                and result.get("status") == "successful"
                and int(result.get("rc", 1)) == 0
            )
        except Exception as exc:
            logger.error(
                "Execution-environment reconciliation failed "
                "(state=%s, error_type=%s)",
                desired_state,
                type(exc).__name__,
            )
            success = False

        self._tick_metrics["execution_environment_reconciliations"] = 1
        if not success:
            self._tick_state["compute_ready"] = False
            self._tick_state["compute_demand"]["execution_environment"] = "failed"
            return

        self._execution_environment_states[root_key] = fingerprint
        self._tick_state["compute_ready"] = True
        logger.info(
            "Execution environment reconciled (state=%s, runnable_todos=%d)",
            desired_state,
            runnable_todos,
        )

    async def _phase_release_compute_demand(self) -> None:
        """Release only compute provisioned by this loop after terminal commit."""
        runner = self._runner
        lifecycle_method = (
            getattr(type(runner), "reconcile_execution_environment", None)
            if runner is not None
            else None
        )
        if runner is None or not callable(lifecycle_method):
            return

        project_id = self._tick_project_id
        root_value = self._resolve_repo_root(project_id)
        if root_value is None:
            return
        project_root = Path(root_value).expanduser().resolve()
        root_key = str(project_root)
        owned_state = self._execution_environment_states.get(root_key)
        if owned_state is None or owned_state[0] != "present":
            return

        try:
            if self._session_factory is not None:
                async with self._session_factory() as session:
                    summary = await TodoRepository(session).status_summary(
                        project_id=project_id,
                    )
            elif self._todo_repo is not None:
                summary = await self._todo_repo.status_summary(project_id=project_id)
            else:
                raise RuntimeError("todo repository unavailable")
            if not isinstance(summary, Mapping):
                raise TypeError("todo status summary is not a mapping")
            by_status = summary.get("by_status")
            if not isinstance(by_status, Mapping):
                raise TypeError("todo status counts are not a mapping")
            in_flight = sum(
                max(0, int(by_status.get(status, 0) or 0))
                for status in (
                    TodoStatus.ACTIVE.value,
                    TodoStatus.AWAITING_RESULT.value,
                    TodoStatus.REVIEWING_RETURN.value,
                    TodoStatus.NEEDS_MORE_WORK.value,
                )
            )
        except Exception as exc:
            logger.error(
                "Compute release demand unknown; preserving owned resources "
                "(error_type=%s)",
                type(exc).__name__,
            )
            self._tick_state["compute_ready"] = True
            self._tick_state["compute_demand"] = {
                "state": "unknown",
                "execution_environment": "preserved",
            }
            return

        if in_flight:
            self._tick_state["compute_ready"] = True
            self._tick_state["compute_demand"] = {
                "state": "retained",
                "runnable_todos": in_flight,
                "execution_environment": "present",
            }
            return

        execution_environment = self.config.get("execution_environment", {})
        if not isinstance(execution_environment, Mapping):
            return
        constraints = {
            str(name): value
            for name, value in execution_environment.items()
            if name != "enabled"
        }
        fingerprint = (
            "absent",
            json.dumps(constraints, sort_keys=True, separators=(",", ":"), default=repr),
        )
        try:
            result = await self._bounded_to_thread(
                runner.reconcile_execution_environment,
                state="absent",
                project_root=project_root,
                constraints=constraints,
            )
            success = (
                isinstance(result, Mapping)
                and result.get("status") == "successful"
                and int(result.get("rc", 1)) == 0
            )
        except Exception as exc:
            logger.error(
                "Execution-environment release failed (error_type=%s)",
                type(exc).__name__,
            )
            success = False

        self._tick_metrics["execution_environment_reconciliations"] = (
            int(self._tick_metrics.get("execution_environment_reconciliations", 0)) + 1
        )
        if not success:
            self._tick_state["compute_ready"] = True
            self._tick_state["compute_demand"] = {
                "state": "release_failed",
                "runnable_todos": 0,
                "execution_environment": "preserved",
            }
            return

        self._execution_environment_states[root_key] = fingerprint
        self._tick_state["compute_ready"] = False
        self._tick_state["compute_demand"] = {
            "state": "idle",
            "runnable_todos": 0,
            "execution_environment": "absent",
        }
        logger.info("Execution environment reconciled (state=absent, runnable_todos=0)")

    async def _effective_claim_limit(self) -> tuple[int, int, Any]:
        config = getattr(self, "config", {})
        event_loop_config = config.get("event_loop", {}) if isinstance(config, Mapping) else {}
        if not isinstance(event_loop_config, Mapping):
            event_loop_config = {}
        max_active_todos = event_loop_config.get("max_active_todos", 10)
        if (
            not isinstance(max_active_todos, int)
            or isinstance(max_active_todos, bool)
            or not 1 <= max_active_todos <= 10_000
        ):
            raise ValueError("max_active_todos must be an integer between 1 and 10000")
        currently_active = 0
        active_count_known = True
        if self._todo_repo is not None and self._active_session is not None:
            try:
                active_count = await self._todo_repo.count_active(
                    project_id=self._tick_project_id,
                )
            except Exception:
                active_count_known = False
                logger.exception("Active todo count unavailable; refusing new claims")
            else:
                if (
                    isinstance(active_count, int)
                    and not isinstance(active_count, bool)
                    and active_count >= 0
                ):
                    currently_active = active_count
                else:
                    active_count_known = False
                    logger.error(
                        "Active todo count was invalid (%r); refusing new claims",
                        active_count,
                    )
        effective_limit = (
            max(0, max_active_todos - currently_active)
            if active_count_known
            else 0
        )

        if self._floor_controller is not None:
            floor_max = self._floor_controller.get_max_active()
            floor_claimable = max(0, floor_max - currently_active)
            effective_limit = min(effective_limit, floor_claimable)

        pid_outputs = self._tick_state.get("pid_outputs")
        if pid_outputs is not None and hasattr(pid_outputs, "desired_total_active_buckets"):
            pid_desired = pid_outputs.desired_total_active_buckets
            pid_claimable = max(0, pid_desired - currently_active)
            effective_limit = min(effective_limit, pid_claimable)
        return effective_limit, currently_active, pid_outputs

    async def _recover_legacy_self_improve(self, project_id: Any) -> None:
        assert self._todo_repo is not None
        try:
            recovered_legacy = await self._todo_repo.recover_queued_legacy_self_improve(
                limit=10,
                project_id=project_id,
            )
            self._tick_state["recovered_legacy_self_improve"] = len(recovered_legacy)
        except Exception:
            logger.exception("Failed to recover queued legacy self-improve approvals")
            self._tick_state["recovered_legacy_self_improve"] = 0

    async def _defer_reaped_claims(
        self,
        claimed: list[Any],
        project_id: Any,
    ) -> list[Any]:
        assert self._todo_repo is not None
        reaped_ids = self._tick_state.get("reaped_todo_ids", set())
        if not reaped_ids:
            return claimed
        retained: list[Any] = []
        for todo in claimed:
            if todo.todo_id not in reaped_ids:
                retained.append(todo)
                continue
            try:
                await self._todo_repo.transition(
                    todo.todo_id,
                    TodoStatus.QUEUED,
                    todo.version,
                    project_id=project_id,
                )
                if self._active_session is not None:
                    for bucket_key in _runtime_lease_bucket_keys(todo, project_id):
                        await release_lease(
                            self._active_session,
                            bucket_key,
                            holder_id=self._lease_owner_id,
                        )
            except Exception as exc:
                logger.warning(
                    "Failed to defer reaped todo %s to next tick: %s",
                    todo.todo_id,
                    exc,
                )
        return retained

    async def _return_lease_conflicts(
        self,
        claimed: list[Any],
        project_id: Any,
    ) -> None:
        assert self._todo_repo is not None
        conflicted_ids: list[str] = []
        for todo in claimed:
            todo_id = _safe_str(todo, "todo_id", "") or ""
            version = getattr(todo, "version", None)
            if not todo_id or not isinstance(version, int):
                continue
            conflicted_ids.append(todo_id)
            try:
                await self._todo_repo.transition(
                    todo_id,
                    TodoStatus.QUEUED,
                    version,
                    project_id=project_id,
                )
            except Exception:
                logger.exception(
                    "Failed to return lease-conflicted todo %s to QUEUED",
                    todo_id,
                )
        self._tick_state["lease_conflict_todo_ids"] = conflicted_ids

    async def _acquire_claim_leases(
        self,
        claimed: list[Any],
        project_id: Any,
    ) -> list[Any]:
        if not claimed or self._active_session is None:
            return claimed
        from general_ludd.event_loop.lease import acquire_leases_batch

        bucket_keys = [
            bucket_key
            for todo in claimed
            for bucket_key in _runtime_lease_bucket_keys(todo, project_id)
        ]
        try:
            lease_ttl_seconds, _ = self._execution_lease_timing()
            if isinstance(self._active_session, AsyncSession):
                await self._active_session.flush()
            todo_versions = {
                execution_bucket: version
                for todo in claimed
                if isinstance((version := getattr(todo, "version", None)), int)
                and not isinstance(version, bool)
                for execution_bucket in _runtime_lease_bucket_keys(todo, project_id)
            }
            if isinstance(self._active_session, AsyncSession):
                async with self._active_session.begin_nested():
                    await acquire_leases_batch(
                        self._active_session,
                        bucket_keys,
                        holder_id=self._lease_owner_id,
                        ttl_seconds=lease_ttl_seconds,
                        project_id=project_id,
                        todo_versions=todo_versions,
                    )
            else:
                await acquire_leases_batch(
                    self._active_session,
                    bucket_keys,
                    holder_id=self._lease_owner_id,
                    ttl_seconds=lease_ttl_seconds,
                    project_id=project_id,
                    todo_versions=todo_versions,
                )
        except Exception as exc:
            logger.error(
                "Batch lease acquisition denied dispatch for %d todos: %s",
                len(bucket_keys),
                exc,
                exc_info=True,
            )
            await self._return_lease_conflicts(claimed, project_id)
            return []
        self._tick_state["execution_lease_todo_ids"] = [
            _safe_str(todo, "todo_id", "") or "" for todo in claimed
        ]
        self._tick_state["execution_lease_versions"] = {
            _safe_str(todo, "todo_id", "") or "": todo.version
            for todo in claimed
        }
        return claimed

    async def _phase_claim_runnable_todos(self) -> None:
        if self._todo_repo is None:
            return
        if (
            self._pause_controller is not None
            and self._tick_project_id is not None
            and self._pause_controller.is_paused("project", self._tick_project_id)
        ):
            logger.info("Project %s is paused — skipping claim", self._tick_project_id)
            self._tick_state["claimed_todos"] = []
            return
        project_id = self._tick_project_id
        if project_id is None and self._project_manager is not None:
            logger.warning("Claim skipped: no active project selected")
            self._tick_state["claimed_todos"] = []
            return
        claim_capacity = await self._effective_claim_limit()
        effective_limit = claim_capacity[0]
        currently_active = claim_capacity[1]
        pid_outputs = claim_capacity[2]
        await self._recover_legacy_self_improve(project_id)
        if effective_limit <= 0:
            logger.debug(
                "Claim skipped: effective_limit=%d (floor=%s, pid=%s, active=%d)",
                effective_limit,
                self._floor_controller.get_max_active() if self._floor_controller else "none",
                getattr(pid_outputs, "desired_total_active_buckets", "none") if pid_outputs else "none",
                currently_active,
            )
            self._tick_state["claimed_todos"] = []
            return
        claimed = await self._todo_repo.claim_runnable(
            limit=effective_limit,
            max_active=currently_active + effective_limit,
            project_id=project_id,
        )
        claimed = await self._defer_reaped_claims(claimed, project_id)
        if self._active_session is not None:
            for todo in claimed:
                todo.estimated_cost_usd = _compute_todo_estimate(todo)
        claimed = await self._acquire_claim_leases(claimed, project_id)
        self._tick_state["claimed_todos"] = claimed

    async def _trim_claimed_to_pid_cap(self, claimed: list[Any]) -> list[Any]:
        pid_outputs = self._tick_state.get("pid_outputs")
        desired = getattr(pid_outputs, "desired_total_active_buckets", None)
        if not isinstance(desired, int) or len(claimed) <= desired:
            return claimed
        if self._active_session is None or self._todo_repo is None:
            return claimed

        keep_count = max(0, desired)
        kept = list(claimed[:keep_count])
        released = list(claimed[keep_count:])
        for todo in released:
            todo_id = _safe_str(todo, "todo_id", "") or ""
            version = getattr(todo, "version", None)
            if todo_id and isinstance(version, int):
                try:
                    await self._todo_repo.transition(
                        todo_id,
                        TodoStatus.QUEUED,
                        version,
                        project_id=self._tick_project_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "PID cap release failed to requeue todo %s: %s",
                        todo_id,
                        exc,
                    )
                    continue
            if todo_id:
                try:
                    for bucket_key in _runtime_lease_bucket_keys(
                        todo,
                        self._tick_project_id,
                    ):
                        await release_lease(
                            self._active_session,
                            bucket_key,
                            holder_id=self._lease_owner_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "PID cap release failed to delete lease for todo %s: %s",
                        todo_id,
                        exc,
                    )
        self._tick_state["claimed_todos"] = kept
        return kept
