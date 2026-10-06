"""Decision reconciliation, delivery, maintenance, and facade helpers."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from general_ludd.event_loop.decision_reconciliation import reconcile_completed_decisions
from general_ludd.event_loop.review_orchestration import safe_string_attribute as _safe_str
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus
from general_ludd.schemas.todo import Todo, TodoStatus

if TYPE_CHECKING:
    # Mixin state is supplied by EventLoop, the sole mutable owner.
    _DynamicState = Any

logger = logging.getLogger(__name__)
class _FileClaimConflict(Exception):
    """Signal that a todo's delivery would clobber another worker's file.

    Treated by the completed-work push path exactly like any other delivery
    failure: the work id is left OUT of the pushed ledger so the commit is
    retried on a later tick (after the conflicting worker releases its claim),
    rather than two todos committing the same file simultaneously.
    """

class DecisionCompletionMixin:
    """Decision reconciliation, delivery, maintenance, and facade helpers."""

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any:
            """Type-only access to state owned by the facade."""
            ...

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
        await reconcile_completed_decisions(self)

    async def _attempt_completed_push(self, todo: Any) -> bool:
        """Push completed work exactly once; return True if this attempt FAILED.

        F2: deduped by work (todo) id — a todo already pushed is a no-op, so the
        push fires exactly once across ticks (never twice).
        F3: a commit/push failure is surfaced (returned True + error log), and
        the work id is left OUT of the ledger so the push is RETRIED on a later
        tick rather than silently leaving status=COMPLETE-but-unpushed.
        #53: retry limit — after _MAX_PUSH_RETRIES consecutive failures the
        todo is transitioned to BLOCKED instead of retrying every tick forever.
        Exponential backoff spreads retries apart so a persistent conflict
        doesn't burn every tick.
        """
        tid = todo.todo_id
        if tid in self._pushed_work:
            logger.debug(
                "Completed work %s already pushed — skipping duplicate push",
                tid,
            )
            return False

        # #53: if we already escaped the livelock for this todo (BLOCKED or
        # BLOCKED_ON_HUMAN), don't retry.
        todo_status = _safe_str(todo, "status", "")
        if todo_status in (TodoStatus.BLOCKED.value, TodoStatus.BLOCKED_ON_HUMAN.value):
            logger.debug(
                "#53: todo %s is %s — push skipped (livelock already escaped)",
                tid,
                todo_status,
            )
            return False

        # D10 (#53): exponential backoff with jitter + per-todo hash offset.
        # Two todos at the same retry_count that use only tick % window
        # always check the same tick — they collide deterministically.
        # Per-todo offset (hash(tid) % window) spreads their check ticks
        # apart, making deterministic collision structurally impossible.
        # The jittered sleep after still provides runtime de-sync.
        retry_count = self._push_retry_count.get(tid, 0)
        if retry_count > 1:
            window = 2 ** min(retry_count, 6)  # cap at 64-tick window
            tick = self._total_ticks
            offset = abs(hash(tid)) % window
            if (tick + offset) % window != 0:
                logger.debug(
                    "#53: backoff skip for %s (retry %d, tick %d, window %d, offset %d)",
                    tid,
                    retry_count,
                    tick,
                    window,
                    offset,
                )
                return True  # signal failure so ledger skips marking pushed
            # D10: jittered backoff to break retry-phase alignment
            jitter_sec = random.uniform(0.1, min(5.0, 0.5 * (2**retry_count)))
            logger.debug(
                "#53: jittered backoff for %s (retry %d, %.2fs)",
                tid,
                retry_count,
                jitter_sec,
            )
            await asyncio.sleep(jitter_sec)

        try:
            await self._try_commit_completed_work(todo)
        except Exception as exc:
            # #53: increment retry counter; escape to BLOCKED at the limit.
            self._push_retry_count[tid] = retry_count + 1
            new_retry = self._push_retry_count[tid]
            if new_retry > self._MAX_PUSH_RETRIES:
                logger.error(
                    "#53: livelock ESCAPE — todo %s failed push %d times "
                    "(max %d). Transitioning to BLOCKED. Last error: %s",
                    tid,
                    new_retry,
                    self._MAX_PUSH_RETRIES,
                    exc,
                )
                await self._escape_push_livelock(todo, new_retry, exc)
                return False
            logger.error(
                "Reconcile: completed-work push FAILED for %s "
                "(state diverged COMPLETE vs unpushed) — will retry "
                "(attempt %d/%d): %s",
                tid,
                new_retry,
                self._MAX_PUSH_RETRIES,
                exc,
            )
            return True

        # Success: clear retry counter + record push.
        self._push_retry_count.pop(tid, None)
        self._ledger_add(self._pushed_work, tid)
        return False

    async def _escape_push_livelock(
        self,
        todo: Any,
        retry_count: int,
        last_error: Exception,
    ) -> None:
        """#53: transition the todo to BLOCKED after exhausting push retries.

        Best-effort: a failure here (no repo, version mismatch, etc.) is logged
        but never propagated — the todo may stay stuck, but the retry loop has
        already been broken by returning False from _attempt_completed_push.
        """
        tid = todo.todo_id
        try:
            if self._todo_repo is not None:
                todo_version = getattr(todo, "version", None)
                if todo_version is not None:
                    await self._todo_repo.transition(
                        tid,
                        TodoStatus.BLOCKED,
                        todo_version,
                    )
                    logger.info(
                        "#53: todo %s transitioned to BLOCKED after %d failed push attempts",
                        tid,
                        retry_count,
                    )
                else:
                    logger.warning(
                        "#53: cannot transition %s — no version on todo object",
                        tid,
                    )
            else:
                logger.warning(
                    "#53: cannot transition %s — no TodoRepository wired",
                    tid,
                )
        except Exception as transition_exc:
            logger.error(
                "#53: BLOCKED transition failed for %s after livelock escape "
                "(retries=%d, push_error=%s, transition_error=%s). "
                "The todo may remain stuck but the retry loop is broken.",
                tid,
                retry_count,
                last_error,
                transition_exc,
            )

        # Clear the retry counter so a future status change (manual unblock)
        # starts a fresh budget.
        self._push_retry_count.pop(tid, None)

    def _decision_to_status(self, decision: str) -> TodoStatus | None:
        mapping: dict[str, TodoStatus] = {
            "complete": TodoStatus.COMPLETE,
            "needs_more_work": TodoStatus.NEEDS_MORE_WORK,
            "failed": TodoStatus.FAILED,
            "blocked": TodoStatus.BLOCKED,
            "manual_hold": TodoStatus.MANUAL_HOLD,
        }
        return mapping.get(decision)

    async def _try_commit_completed_work(self, todo: Any) -> None:
        """H6: commit/branch/push completed work via git automation.

        #31 (multi-agent safety): this is the git-DELIVERY path — the ONLY point
        where a todo's affected files are actually known. They cannot be reserved
        at dispatch/claim time: the model has not run, no worktree changes exist,
        so claiming an empty set then would be a no-op. Here the worktree has been
        written, so ``repo.changed_files()`` yields the real affected paths. We
        therefore claim those files in the shared FileClaimRegistry BEFORE the
        commit, refuse to proceed if another live worker already holds an
        overlapping file (deferring this commit to a later tick rather than
        clobbering), and release the claim once delivery finishes (success OR
        failure). A None registry leaves behaviour unchanged.
        """
        configured_branch = getattr(todo, "branch_name", None)
        project_id = getattr(todo, "project_id", None)
        worktree = getattr(todo, "worktree", None) or self._resolve_repo_root(project_id)
        if worktree:
            from general_ludd.git_automation.repo import GitAutomation

            repo = GitAutomation(worktree)
            registry = self._file_claims
            worker_id = _safe_str(todo, "todo_id", "") or str(id(todo))
            claimed = False
            try:
                # D10 (#31 / #53): discover affected files and atomically
                # claim-or-conflict them in one lock acquisition.  The old
                # claim()+overlaps()+release() pattern was a three-step
                # livelock: two workers could simultaneously claim overlapping
                # sets, each see the other in overlaps(), both release, both
                # retry in lockstep.  claim_or_conflict sorts files for
                # total-order determinism and checks conflicts BEFORE
                # recording the claim, so the first worker to acquire the
                # lock wins and every other worker gets a clean conflict
                # signal without ever holding the lock.
                if registry is not None:
                    affected = await self._bounded_to_thread(repo.changed_files)
                    if affected:
                        acquired = registry.claim_or_conflict(worker_id, affected)
                        if not acquired:
                            logger.info(
                                "#31: deferring commit for todo %s — files %s "
                                "contested by another live worker; will retry next tick",
                                worker_id,
                                sorted(affected),
                            )
                            raise _FileClaimConflict(
                                f"file-claim conflict for {worker_id}: {sorted(affected)} contested"
                            )
                        claimed = True
                current_branch = await self._bounded_to_thread(repo.current_branch)
                if configured_branch:
                    branch_name = configured_branch
                elif current_branch.startswith("gludd/"):
                    # Execution may already have created a more descriptive
                    # todo branch. Preserve that exact checkout for delivery.
                    branch_name = current_branch
                else:
                    branch_name = f"gludd/{todo.todo_id.lower()}"
                if current_branch != branch_name:
                    # create_branch both creates and checks out the task ref,
                    # keeping the subsequent commit off trunk.
                    await self._bounded_to_thread(repo.create_branch, branch_name)
                # M (LIVE stall fix): commit/push shell out to blocking git.
                # Even with a per-subprocess timeout, a 60s blocking call inside
                # the async tick would freeze every other coroutine. Offload to a
                # worker thread (mirrors the playbook dispatch's asyncio.to_thread)
                # so blocking git can never stall the event loop.
                await self._bounded_to_thread(repo.commit, f"[{todo.todo_id}] {todo.title}")
                # Record the per-commit LOC delta into the accounting ledger
                # (counted via git show --numstat). Best-effort: a counting
                # failure must never abort the commit/push flow that follows.
                if self._loc_ledger is not None:
                    try:
                        delta = await self._bounded_to_thread(repo.lines_changed_in_commit)
                        pid = project_id or self._tick_project_id or ""
                        self._loc_ledger.record_loc_changed(pid, delta)
                    except Exception as loc_exc:
                        logger.debug(
                            "loc_changed recording failed for %s: %s",
                            todo.todo_id,
                            loc_exc,
                        )
                pushed = await self._bounded_to_thread(repo.push, branch=branch_name)
                if pushed is not True:
                    raise RuntimeError(
                        f"git push returned false for todo {todo.todo_id} on {branch_name}"
                    )
                logger.info("H6: committed + pushed %s to %s", todo.todo_id, branch_name)
                await self._maybe_open_pr(todo, worktree, branch_name)
            except Exception as exc:
                # F3: surface (don't swallow) so the caller can detect the
                # COMPLETE-but-unpushed split-brain and retry. The caller decides
                # whether to mark the work pushed — a raised failure leaves it
                # unmarked so the push is retried, never silently lost. A
                # _FileClaimConflict is the same shape of "retry next tick" signal.
                if not isinstance(exc, _FileClaimConflict):
                    logger.warning("H6: git automation failed for %s: %s", todo.todo_id, exc)
                raise
            finally:
                # #31: release the claim once delivery settles (committed+pushed
                # OR failed) so the next tick's worker can take overlapping files.
                # release() is a no-op when we never claimed (registry None /
                # no affected files / conflict already released above).
                if registry is not None and claimed:
                    registry.release(worker_id)

    async def _maybe_open_pr(self, todo: Any, worktree: str, branch_name: str) -> None:
        """F1: open a PR for completed work when git_automation.open_pr is set.

        Disabled by default — only fires when config opts in, so the daemon
        never opens PRs unexpectedly. Uses PRDelivery (push branch + gh pr
        create).

        H2 fix: push_and_create_pr shells out to ``gh pr create`` / ``git push``
        which can block for 5-30 s.  Run it in a thread so the event loop is
        never stalled during PR delivery.
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
        result = await self._bounded_to_thread(
            delivery.push_and_create_pr,
            repo_path=worktree,
            branch_name=branch_name,
            todo_id=todo.todo_id,
            title=getattr(todo, "title", todo.todo_id),
        )
        if result.get("error"):
            logger.warning("F1: PR delivery for %s failed: %s", todo.todo_id, result["error"])
        else:
            logger.info("F1: opened PR for %s: %s", todo.todo_id, result.get("pr_url"))

    async def _phase_check_compute_utilization(self) -> None:
        interval = self.config.get("compute_idle_check_interval_ticks", 60)
        if self._total_ticks % interval != 0:
            return

        # Persisted hard-TTL cleanup runs before utilization heuristics. This
        # resumes after daemon restarts and prevents paid accelerator resources
        # from surviving merely because their endpoint was not re-registered.
        if self._deployment_manager is not None:
            expired = await self._deployment_manager.cleanup_expired()
            for instance_id in expired:
                logger.warning(
                    "Destroyed expired compute deployment %s at its hard TTL",
                    instance_id,
                )

        # Floor auto-tuning: dynamically adjust concurrency floor based on
        # system health metrics (CPU, memory, dispatch success rate, queue depth).
        if self._floor_controller is not None:
            try:
                import psutil

                cpu_pct = psutil.cpu_percent(interval=0)
                mem = psutil.virtual_memory()
                memory_pct = mem.percent

                claimed = self._tick_state.get("claimed_todos", [])
                dispatched = self._tick_metrics.get("todos_dispatched", 0)
                dispatch_success_rate = (dispatched / len(claimed) * 100) if claimed else 100.0

                queue_depth = 0
                if self._todo_repo is not None:
                    try:
                        summary = await self._todo_repo.status_summary()
                        by_status = summary.get("by_status", {})
                        queue_depth = int(by_status.get("queued", 0))
                    except Exception:
                        pass

                new_floor = self._floor_controller.auto_tune(
                    cpu_pct=cpu_pct,
                    memory_pct=memory_pct,
                    dispatch_success_rate=dispatch_success_rate,
                    queue_depth=queue_depth,
                )
                entry = self._floor_controller.floor_history[-1]
                if entry["reason"] != "no_change":
                    logger.info(
                        "Floor auto-tuned: %d -> %d (reason=%s, success_rate=%.1f%%, "
                        "queue_depth=%d, cpu=%.1f%%, mem=%.1f%%)",
                        entry["previous_floor"],
                        new_floor,
                        entry["reason"],
                        dispatch_success_rate,
                        queue_depth,
                        cpu_pct,
                        memory_pct,
                    )
                if self._daemon_state is not None:
                    self._daemon_state["floor_auto_tune"] = {
                        "floor": new_floor,
                        "history_size": len(self._floor_controller.floor_history),
                    }
            except Exception as exc:
                logger.warning("Floor auto-tune failed: %s", exc)

        if self._utilization_tracker is None:
            # GPU metric collection runs even without a utilization tracker
            # so that /admin/compute/gpu-metrics always has fresh data.
            import time as _time

            from general_ludd.infra.gpu_metrics import GPUMetricsCollector

            metrics = GPUMetricsCollector.collect_all_gpu_metrics()
            if self._daemon_state is not None:
                self._daemon_state["_last_gpu_metrics"] = metrics
                self._daemon_state["_last_gpu_metrics_at"] = _time.time()
            return
        threshold = self.config.get("compute_idle_gpu_sm_pct", 5.0)
        teardown_ticks = self.config.get("compute_idle_teardown_threshold_ticks", 3)
        notice_ticks = self.config.get("compute_idle_preemption_notice_ticks", 1)
        idle_endpoints = self._utilization_tracker.find_idle_gpus(
            threshold=float(threshold),
            window=900,
        )
        daemon_state = self._daemon_state if self._daemon_state is not None else {}
        idle_tracking: dict[str, Any] = daemon_state.setdefault("idle_endpoints", {})
        torn_down: list[str] = daemon_state.setdefault("torn_down_endpoints", [])
        current_idle_ids: set[str] = set()
        # bill-6: collect GPU metrics after compute utilization check.
        import time as _time

        from general_ludd.infra.gpu_metrics import GPUMetricsCollector

        metrics = GPUMetricsCollector.collect_all_gpu_metrics()
        daemon_state["_last_gpu_metrics"] = metrics
        daemon_state["_last_gpu_metrics_at"] = _time.time()
        for ep in idle_endpoints:
            current_idle_ids.add(ep.endpoint_id)
            if ep.endpoint_id in torn_down:
                try:
                    self._utilization_tracker.unregister_endpoint(ep.endpoint_id)
                except Exception as exc:
                    logger.error(
                        "Failed to finalize local cleanup for destroyed endpoint %s: %s",
                        ep.endpoint_id,
                        exc,
                    )
                    continue
                idle_tracking.pop(ep.endpoint_id, None)
                continue
            if ep.endpoint_id not in idle_tracking:
                idle_tracking[ep.endpoint_id] = {
                    "endpoint_id": ep.endpoint_id,
                    "url": ep.url,
                    "model": ep.model,
                    "gpu_type": ep.gpu_type,
                    "last_used": ep.last_used,
                    "idle_ticks": 0,
                }
            idle_tracking[ep.endpoint_id]["idle_ticks"] += 1
            count = idle_tracking[ep.endpoint_id]["idle_ticks"]
            logger.warning(
                "Idle GPU endpoint %s (%s, %s): %d consecutive idle ticks",
                ep.endpoint_id,
                ep.model,
                ep.gpu_type,
                count,
            )
            # bill-7: preemption notice — warn N ticks before teardown.
            if notice_ticks > 0 and count == teardown_ticks - notice_ticks:
                logger.warning(
                    "[COMPUTE] endpoint %s will be torn down in %d ticks if idle persists",
                    ep.endpoint_id,
                    notice_ticks,
                )
            if count >= teardown_ticks:
                logger.warning(
                    "Tearing down idle GPU endpoint %s after %d idle ticks",
                    ep.endpoint_id,
                    count,
                )
                # bill-7: record infra cost on teardown.
                if self._infra_tracker is not None:
                    try:
                        gpu_seconds = _time.time() - ep.last_used if ep.last_used > 0 else 0.0
                        self._infra_tracker.record_gpu_seconds(
                            provider=getattr(ep, "provider", "runpod") or "runpod",
                            gpu_type=ep.gpu_type,
                            seconds=max(gpu_seconds, 0.0),
                            spot=True,
                            project_id=getattr(ep, "project_id", None),
                        )
                    except Exception as _exc:
                        logger.warning(
                            "Failed to record infra cost for teardown of %s: %s",
                            ep.endpoint_id,
                            _exc,
                        )
                else:
                    logger.warning(
                        "_infra_tracker not initialized; cannot record GPU cost for teardown of %s",
                        ep.endpoint_id,
                    )
                if self._deployment_manager is not None:
                    try:
                        await self._deployment_manager.destroy(ep.endpoint_id)
                    except Exception as exc:
                        logger.error(
                            "Failed to tear down idle endpoint %s: %s",
                            ep.endpoint_id,
                            exc,
                        )
                        continue
                else:
                    logger.warning(
                        "_deployment_manager not initialized; cannot destroy idle endpoint %s",
                        ep.endpoint_id,
                    )
                    # Keep the endpoint and its idle evidence until an owner can
                    # prove destruction.  Unregistering here would turn a missing
                    # lifecycle owner into a false teardown-success receipt while
                    # the paid resource may still be running.
                    continue
                # Destruction is the authoritative external transition. Record
                # its tombstone before local tracker cleanup so a partial local
                # failure can retry unregistering without destroying twice.
                if ep.endpoint_id not in torn_down:
                    torn_down.append(ep.endpoint_id)
                try:
                    self._utilization_tracker.unregister_endpoint(ep.endpoint_id)
                except Exception as exc:
                    logger.error(
                        "Destroyed idle endpoint %s but local unregister failed: %s",
                        ep.endpoint_id,
                        exc,
                    )
                    continue
                idle_tracking.pop(ep.endpoint_id, None)
        no_longer_idle = [eid for eid in idle_tracking if eid not in current_idle_ids]
        for eid in no_longer_idle:
            idle_tracking.pop(eid, None)

    async def _phase_check_service_credits(self) -> None:
        """Refresh prepaid service credit balances every N ticks.

        Queries the wired :class:`~general_ludd.budget.credit_tracker.CreditTracker`
        (if any) and stashes the latest per-service balances on
        ``self._daemon_state["credits"]`` so the ``GET /api/credits`` endpoint
        can serve them without issuing a fresh HTTP call per request. The
        tracker handles transport errors / parse failures / missing keys
        per-provider, so a single bad key never poisons the rest.

        Interval: ``credit_check_interval_ticks`` (default 600 = ~10 minutes at
        1s/tick). Set to 0 to disable.
        """
        if self._credit_tracker is None:
            return
        interval = int(self.config.get("credit_check_interval_ticks", 600))
        if interval <= 0:
            return
        if self._total_ticks % interval != 0:
            return
        try:
            results = await self._bounded_to_thread(self._credit_tracker.check_all_balances)
        except Exception as exc:
            logger.warning("Service-credit balance check failed: %s", exc)
            return
        if self._daemon_state is not None:
            self._daemon_state["credits"] = results
        # Surface low-balance providers in tick metrics for the dashboard.
        low = [
            svc
            for svc, r in results.items()
            if r.get("balance_usd") is not None and self._credit_tracker.should_refill(svc)
        ]
        if low:
            self._tick_metrics["low_credit_services"] = low
            logger.warning("Low service credit balance: %s", ", ".join(low))

    async def _phase_flush_spend_ledger(self) -> None:
        """SPD-1: periodically persist in-memory spend records to the DB.

        Gated by ``spend_persist_interval_ticks`` (default 60; <=0 disables).
        Opens a DEDICATED session from the session factory so failures here
        never affect the shared tick session held across post-dispatch phases.
        Records their seq watermark, writes through ``SpendRepository.add()``,
        then calls ``SpendLimiter.mark_flushed()`` to advance the watermark so
        the next flush is incremental.

        Failure semantics: a persist failure (DB error) is logged at WARNING
        and never blocks dispatch — the in-memory limiter stays authoritative
        for the live cap; only the next tick's flush retries.
        """
        if self._spend_limiter is None or self._session_factory is None:
            return
        interval = int(self.config.get("spend_persist_interval_ticks", 60))
        if interval <= 0:
            return
        if self._total_ticks % interval != 0:
            return
        unflushed = self._spend_limiter.unflushed_records()
        if not unflushed:
            return
        try:
            from general_ludd.db.repository import SpendRepository

            max_seq = unflushed[0][0]
            async with self._session_factory() as session:
                repo = SpendRepository(session)
                for seq, ts, cost_usd, project_id in unflushed:
                    await repo.add(
                        ts=float(ts),
                        cost_usd=float(cost_usd),
                        kind="token",
                        project_id=project_id,
                    )
                    if seq > max_seq:
                        max_seq = seq
                await session.commit()
            self._spend_limiter.mark_flushed(max_seq)
            logger.info(
                "SpendLimiter: flushed %d record(s) to spend_records (upto_seq=%d)",
                len(unflushed),
                max_seq,
            )
        except Exception as exc:
            logger.warning(
                "SpendLimiter flush failed (will retry next tick): %s",
                exc,
            )

    async def _phase_remediate_blocked_tasks(self) -> None:
        """Auto-remediation tick phase (#52): scan for blocked todos and act.

        Runs the read-only :class:`BlockerDetector` scan against the tick's
        live session/todo_repo, then hands at most
        ``remediation_max_actions_per_tick`` findings to
        :class:`RemediationDispatcher` per tick — a large blocked backlog is
        drained gradually rather than flooding the todo/human-todo tables in
        one pass. Idempotent: a finding already acted on within the
        configured ``retry_delay_hours`` cooldown is skipped via
        ``RemediationActionRepository.exists_recent`` so a still-blocked task
        does not get a fresh dispatch/retry/human-todo filed every tick.

        Interval: ``remediation_check_interval_ticks`` (default 30). Set to 0
        to disable (kill switch) — mirrors
        ``_phase_refresh_model_performance``'s interval idiom. Reads the
        SAME ``RemediationConfig`` the ``/admin/remediation/*`` HTTP
        endpoints read (``self._daemon_state["remediation_config"]``, wired
        by daemon.py from ``UserConfig.remediation``) so the tick path and
        the HTTP path never disagree on thresholds. Actions are conservative
        (todo/retry/human-todo) and never touch code — every action is
        audited via ``RemediationActionRepository``.
        """
        interval = int(self.config.get("remediation_check_interval_ticks", 30))
        if interval <= 0 or self._total_ticks % interval != 0:
            return
        if self._todo_repo is None or self._active_session is None:
            return
        try:
            from general_ludd.db.repository import (
                HumanTodoRepository,
                RemediationActionRepository,
            )
            from general_ludd.remediation.blocker_detector import (
                BlockerDetector,
                RemediationConfig,
            )
            from general_ludd.remediation.dispatcher import RemediationDispatcher

            cfg = self._daemon_state.get("remediation_config") if self._daemon_state is not None else None
            if not isinstance(cfg, RemediationConfig):
                cfg = RemediationConfig()

            # NEEDS_MORE_WORK → QUEUED requeue sweep: cycle eligible work
            # back into the pipeline before filing human-todos.
            nmw_requeued = await self._todo_repo.requeue_needs_more_work(
                cooldown_hours=cfg.needs_more_work_cooldown_hours,
                max_run_count=cfg.max_requeues_before_chronic,
            )
            self._tick_metrics["remediation_nmw_requeued"] = nmw_requeued

            human_todo_repo = HumanTodoRepository(self._active_session)
            detector = BlockerDetector(
                todo_repo=self._todo_repo,
                human_todo_repo=human_todo_repo,
                config=cfg,
                session=self._active_session,
            )
            findings = await detector.scan(project_id=self._tick_project_id)
            self._tick_metrics["remediation_scanned"] = len(findings)
            if not findings:
                return
            remediation_repo = RemediationActionRepository(self._active_session)
            max_actions = int(self.config.get("remediation_max_actions_per_tick", 5))
            since = datetime.now(UTC) - timedelta(hours=cfg.retry_delay_hours)
            dispatcher = RemediationDispatcher(
                detector=detector,
                todo_repo=self._todo_repo,
                human_todo_repo=human_todo_repo,
                remediation_repo=remediation_repo,
            )
            acted = 0
            for blocked in findings:
                if acted >= max_actions:
                    break
                if await remediation_repo.exists_recent(blocked.todo_id, since):
                    continue
                await dispatcher.remediate(blocked)
                acted += 1
            self._tick_metrics["remediation_actions"] = acted
        except Exception as exc:
            logger.warning("Auto-remediation tick phase failed: %s", exc, exc_info=True)

    # Matrix of concurrent dispatch ceiling overrides below: maximum entries kept
    # in the per-instance idempotency ledgers.
    # Beyond this cap, the oldest entry is evicted (FIFO) so the ledgers never
    # grow without bound (P3: unbounded-memory defect, audit finding MED).
    _MAX_PUSH_RETRIES: ClassVar[int] = 5

    _MAX_LEDGER_SIZE: ClassVar[int] = 10_000

    def _ledger_add(self, ledger: OrderedDict[str, None], key: str) -> None:
        """Insert ``key`` into a bounded LRU ledger, evicting the oldest past cap.

        P3 (unbounded ledger growth): ``ledger`` is an insertion-ordered
        ``OrderedDict`` used as an ordered set. A re-inserted key is moved to the
        most-recently-used end (``move_to_end``) so an id that is still being
        touched is never the eviction victim; a genuinely new key is appended and,
        if the ledger now exceeds ``_MAX_LEDGER_SIZE``, the OLDEST key is dropped in
        O(1) via ``popitem(last=False)``.

        This replaces the previous ``set`` + ``list(set)[:surplus]`` prune, which
        (a) materialised the whole ledger into a list at capacity (O(n) spike) and
        (b) evicted an ARBITRARY victim because a ``set`` has no order — which could
        forget a still-relevant recent id and re-open the F1/F2 re-apply / re-push
        window. FIFO-by-insertion keeps the most recent ``_MAX_LEDGER_SIZE`` ids,
        which is exactly the set most likely to still be in flight.
        """
        if key in ledger:
            ledger.move_to_end(key)
            return
        ledger[key] = None
        while len(ledger) > self._MAX_LEDGER_SIZE:
            ledger.popitem(last=False)

    # Maximum in-memory ExecutionTrace entries. Each trace is small (~1 KB)
    # so 1 000 entries is safe even under heavy dispatch rates.
    _MAX_ACTIVE_TRACES: ClassVar[int] = 1_000
    # P3: cap the daemon-state self_update audit list (one entry per successful
    # self_update reload). 500 recent applies is ample for operator inspection
    # while the list stays small and JSON-serialisable on app.state.
    _MAX_SELF_UPDATE_APPLIES: ClassVar[int] = 500

    _PRIORITY_MAP: ClassVar[dict[str, int]] = {
        "low": 0,
        "medium": 5,
        "high": 10,
        "critical": 20,
    }

    async def _persist_self_improve_todos(self, todos: list[dict[str, Any]], project_id: str | None = None) -> int:
        if self._todo_repo is None or self._active_session is None:
            return 0
        from general_ludd.self_improve.gate import SelfImproveGate
        from general_ludd.self_improve.staging import (
            build_managed_plan_request_payload,
        )

        si_cfg = self.config.get("self_improve", {}) if isinstance(self.config, dict) else {}
        if not isinstance(si_cfg, dict):
            si_cfg = {}
        gate = SelfImproveGate(
            max_open=si_cfg.get("max_open", 10),
        )
        terminal = {
            TodoStatus.COMPLETE.value,
            TodoStatus.FAILED.value,
            TodoStatus.CANCELLED.value,
        }
        existing = await self._todo_repo.list_by_work_type("self_improve", project_id=project_id)
        open_count = sum(1 for t in existing if _safe_str(t, "status") not in terminal)
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
            managed_payload = (
                build_managed_plan_request_payload(todo, project_id=project_id)
                if project_id is not None
                else {}
            )
            payload: dict[str, Any] = {
                "title": str(todo.get("title", "Self-improvement task"))[:512],
                "description": str(todo.get("description", "")),
                "status": decision.initial_status,
                "work_type": "self_improve",
                "priority": priority,
                "created_by": "self_improve_harness",
                "project_id": project_id,
                **managed_payload,
            }
            try:
                await self._todo_repo.create(payload)
                persisted += 1
                open_count += 1
            except Exception as exc:
                logger.warning("Failed to persist self-improve todo: %s", exc)
        if persisted:
            try:
                await self._active_session.flush()
            except Exception:
                logger.warning(
                    "Flush of %d self-improve todo(s) failed",
                    persisted,
                    exc_info=True,
                )
        return persisted

    async def dispatch_return_review(self, task_return: TaskReturn) -> dict[str, Any]:
        """Describe a review dispatch for one newly created task return."""
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
        """Return only todos whose status is ready for execution."""
        runnable = [t for t in todos if t.status == TodoStatus.QUEUED]
        return runnable

    async def reconcile_decision(self, decision: TaskDecision, todo: Todo) -> Todo:
        """Apply one review decision to the in-memory todo state machine."""
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
