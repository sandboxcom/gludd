"""WP-C1: behavioral coverage for the event_loop phase pipeline.

Targets the 16 phase methods and the claim/dispatch/review/reconcile/
lease-reclaim control flow that was previously under-exercised. Each test
pins a single observable behavior (a phase skip, an exception-continuation,
an escalation, a commit-rollback, a budget stop, a lease reclaim) rather
than asserting on internal plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.lease import reclaim_expired_leases
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    http_client = AsyncMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=session,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "http_client": http_client,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


class TestClaimPhaseSkipsWhenNoRunnableTodos:
    """Phase must record an empty claim and acquire NO leases when claim returns []."""

    @pytest.mark.asyncio
    async def test_claim_phase_skips_when_no_runnable_todos(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        loop._active_session = mocks["session"]
        loop._tick_project_id = "proj-test"

        await loop._phase_claim_runnable_todos()

        assert loop._tick_state["claimed_todos"] == []
        assert loop._tick_metrics is not None
        mocks["todo_repo"].claim_runnable.assert_awaited_once()


class TestDispatchPhaseAdvancesOnSuccess:
    """Phase increments todos_dispatched when the sequential path completes cleanly."""

    @pytest.mark.asyncio
    async def test_dispatch_phase_advances_on_success(self):
        loop, mocks = _make_loop()
        todo = Todo(
            title="dispatch-ok",
            todo_id="TODO-DISPATCH-OK",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        loop._tick_state["pid_outputs"] = None

        await loop._phase_dispatch_execute_jobs()

        assert loop._tick_metrics["todos_dispatched"] == 1
        mocks["http_client"].post.assert_awaited_once()


class TestDispatchPhaseLogsOnExceptionAndContinues:
    """A single job that raises must not abort the phase — the loop continues."""

    @pytest.mark.asyncio
    async def test_dispatch_phase_logs_on_exception_and_continues(self):
        loop, _ = _make_loop()
        todo_a = Todo(
            title="dispatch-fail",
            todo_id="TODO-FAIL",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        todo_b = Todo(
            title="dispatch-ok",
            todo_id="TODO-OK",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        loop._tick_state["claimed_todos"] = [todo_a, todo_b]
        loop._tick_state["pid_outputs"] = None

        call_log: list[str] = []

        async def _dispatch_side_effect(todo, **_kwargs):
            call_log.append(todo.todo_id)
            if todo.todo_id == "TODO-FAIL":
                raise RuntimeError("forced dispatch failure")

        with patch.object(
            loop, "_dispatch_execute_job", side_effect=_dispatch_side_effect
        ):
            await loop._phase_dispatch_execute_jobs()

        # Both todos were attempted despite the first raising.
        assert call_log == ["TODO-FAIL", "TODO-OK"]
        # The surviving job still counts toward the dispatch metric.
        assert loop._tick_metrics["todos_dispatched"] == 1


class TestReviewPhaseEscalatesOnFailure:
    """A reviewer that raises must escalate to a manual_hold decision — never silent pass."""

    @pytest.mark.asyncio
    async def test_review_phase_escalates_on_failure(self):
        reviewer = MagicMock()
        reviewer.review_return.side_effect = RuntimeError("reviewer blew up")

        loop, _mocks = _make_loop(reviewer=reviewer)

        tr = MagicMock()
        tr.return_id = "RET-ESC-1"
        tr.todo_id = "TODO-ESC-1"
        tr.job_id = "JOB-ESC-1"
        tr.playbook = "noop.yml"
        tr.queue = "model"
        tr.work_type = "review"
        tr.exit_code = 0
        tr.result_summary = "ran"
        tr.project_id = None

        captured_decisions: list[TaskDecision] = []

        async def _fake_apply_decision(decision, *_args, **_kwargs):
            captured_decisions.append(decision)

        with patch(
            "general_ludd.review.decision_applier.apply_decision",
            new=_fake_apply_decision,
        ), patch(
            "general_ludd.event_loop.loop.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _invoke_sync(fn, a, kw),
        ):
            await loop._review_in_process(tr)

        assert len(captured_decisions) == 1
        escalated = captured_decisions[0]
        assert escalated.decision == "manual_hold"
        assert escalated.confidence == 0.0
        assert any("Reviewer error" in note for note in escalated.audit_notes)


def _invoke_sync(fn, args, kwargs):
    """Run the sync `fn` (asyncio.to_thread replacement) and return its result."""
    return fn(*args, **kwargs)


class TestReconcilePhaseCommitsCompletedDecisions:
    """A `complete` decision transitions the todo to COMPLETE and the apply ledger records it."""

    @pytest.mark.asyncio
    async def test_reconcile_phase_commits_completed_decisions(self):
        loop, mocks = _make_loop()

        decision_row = MagicMock()
        decision_row.id = 9001
        decision_row.return_id = "RET-RC-1"
        decision_row.matched_todo_id = "TODO-RC-1"
        decision_row.decision = "complete"
        decision_row.confidence = 0.95
        decision_row.evidence_refs = "[]"
        decision_row.audit_notes = "[]"
        decision_row.project_id = None

        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock

        todo = MagicMock()
        todo.todo_id = "TODO-RC-1"
        todo.status = TodoStatus.REVIEWING_RETURN.value
        todo.version = 7
        todo.project_id = None
        mocks["todo_repo"].get_by_ids = AsyncMock(return_value={"TODO-RC-1": todo})

        # Make the push a no-op success so the apply ledger records the decision.
        async def _noop_commit(_todo):
            return None

        with patch.object(loop, "_try_commit_completed_work", side_effect=_noop_commit), \
                patch(
                    "general_ludd.review.completion_verifier.verify_completion",
                    return_value=decision_row_as_verified(decision_row),
                ):
            await loop._phase_reconcile_completed_decisions()

        mocks["todo_repo"].transition.assert_awaited_once_with(
            "TODO-RC-1", TodoStatus.COMPLETE, 7
        )
        assert "id:9001" in loop._applied_decisions
        assert loop._tick_metrics["decisions_applied"] == 1


def decision_row_as_verified(decision_row):
    """Build a TaskDecision mirroring the row to satisfy the evidence gate."""
    return TaskDecision(
        return_id=getattr(decision_row, "return_id", "") or "",
        matched_todo_id=decision_row.matched_todo_id,
        decision=decision_row.decision,
        confidence=float(getattr(decision_row, "confidence", 0.0) or 0.0),
        evidence_refs=[],
        audit_notes=[],
    )


class TestReconcilePhaseRollsBackOnCommitError:
    """A commit failure must NOT mark the work pushed and must increment the retry counter."""

    @pytest.mark.asyncio
    async def test_reconcile_phase_rolls_back_on_commit_error(self):
        loop, _ = _make_loop()

        todo = Todo(
            title="commit-fail",
            todo_id="TODO-ROLLBACK",
            status=TodoStatus.COMPLETE,
            queue="core",
            work_type="code",
        )

        async def _boom(_todo):
            raise RuntimeError("git push exploded")

        with patch.object(loop, "_try_commit_completed_work", side_effect=_boom):
            result = await loop._attempt_completed_push(todo)

        # The attempt reports failure...
        assert result is True
        # ...the todo is NOT recorded as pushed...
        assert "TODO-ROLLBACK" not in loop._pushed_work
        # ...and the retry counter was incremented for the next tick.
        assert loop._push_retry_count.get("TODO-ROLLBACK") == 1


class TestTickStopsWhenBudgetExhausted:
    """When the budget guard denies dispatch, the phase returns without dispatching."""

    @pytest.mark.asyncio
    async def test_tick_stops_when_budget_exhausted(self):
        budget_guard = MagicMock()
        budget_guard.check_all_limits.return_value = {
            "allowed": False,
            "reason": "daily_cap_exhausted",
        }

        loop, mocks = _make_loop(budget_guard=budget_guard)
        todo = Todo(
            title="budget-blocked",
            todo_id="TODO-BUDGET",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        loop._tick_state["claimed_todos"] = [todo]
        loop._tick_state["pid_outputs"] = None

        await loop._phase_dispatch_execute_jobs()

        assert loop._tick_metrics["todos_dispatched"] == 0
        mocks["http_client"].post.assert_not_awaited()


class TestLeaseReclaimedWhenExpired:
    """An expired lease is deleted and its todo is requeued to QUEUED."""

    @pytest.mark.asyncio
    async def test_lease_reclaimed_when_expired(self):
        session = AsyncMock()
        expired = MagicMock()
        expired.id = 42
        expired.bucket_key = "core:TODO-RECLAIM-1"
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=120)

        expired_result = MagicMock()
        expired_result.scalars().all.return_value = [expired]

        live_lease_result = MagicMock()
        live_lease_result.scalars().all.return_value = []

        update_result = MagicMock()
        delete_result = MagicMock()
        session.execute.side_effect = [
            expired_result,
            live_lease_result,
            update_result,
            delete_result,
        ]
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        count = await reclaim_expired_leases(session, max_age_seconds=300)

        assert count == 1
        session.delete.assert_awaited_once_with(expired)
        session.flush.assert_awaited_once()


class TestLegacySandboxFallbackBranches:
    """The explicitly unwired compatibility path stays observable and fail-open."""

    @pytest.mark.asyncio
    async def test_missing_backend_skips_sandbox(self):
        loop, _ = _make_loop()
        todo = SimpleNamespace(todo_id="TODO-NO-BACKEND", project_id=None)

        with patch(
            "general_ludd.security.sandboxes.detect.auto", return_value=None
        ):
            assert await loop._sandbox_apply_for_todo(todo) is None

    @pytest.mark.asyncio
    async def test_missing_permission_spec_skips_backend(self):
        loop, _ = _make_loop()
        todo = SimpleNamespace(todo_id="TODO-NO-SPEC", project_id=None)
        backend = MagicMock()

        with patch(
            "general_ludd.security.sandboxes.detect.auto", return_value=backend
        ), patch.object(loop, "_resolve_permission_spec", return_value=None):
            assert await loop._sandbox_apply_for_todo(todo) is None

        backend.apply.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("applied", "severities"),
        [
            (True, ["ok", "warn"]),
            (True, ["fail", "warn"]),
            (False, ["warn"]),
        ],
    )
    async def test_backend_result_is_returned_for_every_verify_outcome(
        self, applied, severities
    ):
        loop, _ = _make_loop()
        todo = SimpleNamespace(todo_id="TODO-SANDBOX", project_id="project-1")
        backend = MagicMock(name="sandbox-backend")
        backend.name = "test-sandbox"
        handle = SimpleNamespace(applied=applied, token="sandbox-token")
        findings = [
            SimpleNamespace(severity=severity, message=f"{severity}-finding")
            for severity in severities
        ]
        backend.apply.return_value = handle
        backend.verify.return_value = findings

        async def _run_inline(function, *args):
            return function(*args)

        with patch(
            "general_ludd.security.sandboxes.detect.auto", return_value=backend
        ), patch.object(loop, "_resolve_permission_spec", return_value=object()), \
                patch.object(loop, "_resolve_repo_root", return_value="/repo"), \
                patch.object(loop, "_bounded_to_thread", side_effect=_run_inline):
            result = await loop._sandbox_apply_for_todo(todo)

        assert result is handle
        backend.apply.assert_called_once()
        backend.verify.assert_called_once()

    @pytest.mark.asyncio
    async def test_backend_exception_is_contained(self):
        loop, _ = _make_loop()
        todo = SimpleNamespace(todo_id="TODO-SANDBOX-ERROR", project_id=None)
        backend = MagicMock()

        with patch(
            "general_ludd.security.sandboxes.detect.auto", return_value=backend
        ), patch.object(
            loop, "_resolve_permission_spec", side_effect=RuntimeError("bad policy")
        ):
            assert await loop._sandbox_apply_for_todo(todo) is None


class TestHumanInputResolutionBranches:
    """Resolved human input is scoped to one fresh session and failures are soft."""

    @staticmethod
    def _factory(session):
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        return factory

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resolved", [None, "operator response"])
    async def test_resolved_row_or_none_is_returned(self, resolved):
        session = AsyncMock()
        factory = self._factory(session)
        loop, _ = _make_loop()
        loop._session_factory = factory
        repo = MagicMock()
        repo.get_done_for_parent = AsyncMock(
            return_value=(
                None
                if resolved is None
                else SimpleNamespace(human_resolution=resolved)
            )
        )

        with patch(
            "general_ludd.db.repository.HumanTodoRepository", return_value=repo
        ):
            result = await loop._resolve_human_input_for_todo("TODO-PARENT")

        assert result == resolved
        repo.get_done_for_parent.assert_awaited_once_with("TODO-PARENT")

    @pytest.mark.asyncio
    async def test_repository_failure_returns_none(self):
        session = AsyncMock()
        factory = self._factory(session)
        loop, _ = _make_loop()
        loop._session_factory = factory
        repo = MagicMock()
        repo.get_done_for_parent = AsyncMock(side_effect=RuntimeError("db down"))

        with patch(
            "general_ludd.db.repository.HumanTodoRepository", return_value=repo
        ):
            assert await loop._resolve_human_input_for_todo("TODO-PARENT") is None


class TestInterruptedDispatchRecoveryBranches:
    """Startup recovery remains bounded to actionable checkpoints and fail-soft."""

    @pytest.mark.asyncio
    async def test_checkpoint_enumeration_failure_is_contained(self):
        manager = MagicMock()
        manager.list_interrupted.side_effect = RuntimeError("corrupt store")
        loop, _ = _make_loop(checkpoint_manager=manager)

        await loop._resume_interrupted_dispatches()

        manager.filter_actionable_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_checkpoint_set_is_a_noop(self):
        manager = MagicMock()
        manager.list_interrupted.return_value = []
        loop, _ = _make_loop(checkpoint_manager=manager)

        await loop._resume_interrupted_dispatches()

        manager.filter_actionable_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_lookup_and_phase_fallback_feed_actionable_filter(self):
        manager = MagicMock()
        snapshots = [
            SimpleNamespace(
                task_id="TODO-RESUME-1",
                dispatch_state=SimpleNamespace(phase_marker="post-model"),
            ),
            SimpleNamespace(task_id="TODO-RESUME-2", dispatch_state=None),
            SimpleNamespace(task_id="TODO-RESUME-3", dispatch_state=None),
        ]
        manager.list_interrupted.return_value = snapshots
        manager.filter_actionable_sync.return_value = snapshots[:2]
        loop, mocks = _make_loop(checkpoint_manager=manager)
        mocks["todo_repo"].get_by_id.side_effect = [
            SimpleNamespace(status="active"),
            None,
            RuntimeError("lookup failed"),
        ]

        await loop._resume_interrupted_dispatches()

        manager.filter_actionable_sync.assert_called_once_with(
            snapshots,
            statuses={"TODO-RESUME-1": "active"},
        )
        manager.mark_resumed.assert_any_call("TODO-RESUME-1", phase="post-model")
        manager.mark_resumed.assert_any_call("TODO-RESUME-2", phase="pre_model")
        assert manager.mark_resumed.call_count == 2

    @pytest.mark.asyncio
    async def test_unwired_repository_assumes_checkpoint_is_actionable(self):
        manager = MagicMock()
        snapshot = SimpleNamespace(task_id="TODO-RESUME", dispatch_state=None)
        manager.list_interrupted.return_value = [snapshot]
        manager.filter_actionable_sync.return_value = [snapshot]
        loop, _ = _make_loop(todo_repo=None, checkpoint_manager=manager)
        loop._todo_repo = None

        await loop._resume_interrupted_dispatches()

        manager.filter_actionable_sync.assert_called_once_with(
            [snapshot], statuses={}
        )
        manager.mark_resumed.assert_called_once_with(
            "TODO-RESUME", phase="pre_model"
        )


class TestLegacyClaimRecoveryBranches:
    """A stale legacy claim is removed before leases reach the dispatcher."""

    @staticmethod
    def _claimed(todo_id, *, queue="core"):
        return SimpleNamespace(
            todo_id=todo_id,
            queue=queue,
            version=4,
            resource_profile="low_resource",
            confidence=None,
        )

    @pytest.mark.asyncio
    async def test_reaped_claim_is_requeued_and_its_lease_released(self):
        loop, mocks = _make_loop()
        keep = self._claimed("TODO-KEEP")
        reaped = self._claimed("TODO-REAPED", queue="repair")
        mocks["todo_repo"].count_active.return_value = 0
        mocks["todo_repo"].recover_queued_legacy_self_improve.return_value = []
        mocks["todo_repo"].claim_runnable.return_value = [keep, reaped]
        loop._active_session = mocks["session"]
        loop._tick_project_id = "project-1"
        loop._tick_state["reaped_todo_ids"] = {"TODO-REAPED"}

        with patch(
            "general_ludd.event_loop.loop.release_lease", new_callable=AsyncMock
        ) as release, patch(
            "general_ludd.event_loop.lease.acquire_leases_batch",
            new_callable=AsyncMock,
        ) as acquire:
            await loop._phase_claim_runnable_todos()

        assert loop._tick_state["claimed_todos"] == [keep]
        mocks["todo_repo"].transition.assert_awaited_once_with(
            "TODO-REAPED", TodoStatus.QUEUED, 4, project_id="project-1"
        )
        release.assert_awaited_once_with(
            mocks["session"], "repair:TODO-REAPED", holder_id="tick-0"
        )
        acquire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reaped_claim_transition_failure_is_contained(self):
        loop, mocks = _make_loop()
        reaped = self._claimed("TODO-REAPED")
        mocks["todo_repo"].count_active.return_value = 0
        mocks["todo_repo"].recover_queued_legacy_self_improve.return_value = []
        mocks["todo_repo"].claim_runnable.return_value = [reaped]
        mocks["todo_repo"].transition.side_effect = RuntimeError("CAS lost")
        loop._active_session = mocks["session"]
        loop._tick_state["reaped_todo_ids"] = {"TODO-REAPED"}

        with patch(
            "general_ludd.event_loop.loop.release_lease", new_callable=AsyncMock
        ) as release:
            await loop._phase_claim_runnable_todos()

        assert loop._tick_state["claimed_todos"] == []
        release.assert_not_awaited()


class TestPidClaimTrimBranches:
    """Post-claim PID trimming requeues and releases every excess claim safely."""

    @staticmethod
    def _todo(todo_id, *, version=3, queue="core"):
        return SimpleNamespace(todo_id=todo_id, version=version, queue=queue)

    @pytest.mark.asyncio
    async def test_missing_active_session_leaves_claims_untouched(self):
        loop, _ = _make_loop()
        claimed = [self._todo("TODO-1"), self._todo("TODO-2")]
        loop._tick_state["pid_outputs"] = SimpleNamespace(
            desired_total_active_buckets=1
        )
        loop._active_session = None

        assert await loop._trim_claimed_to_pid_cap(claimed) == claimed

    @pytest.mark.asyncio
    async def test_excess_claim_is_requeued_then_releases_lease(self):
        loop, mocks = _make_loop()
        keep = self._todo("TODO-KEEP")
        excess = self._todo("TODO-EXCESS", queue="batch")
        loop._tick_state["pid_outputs"] = SimpleNamespace(
            desired_total_active_buckets=1
        )
        loop._active_session = mocks["session"]

        with patch(
            "general_ludd.event_loop.loop.release_lease", new_callable=AsyncMock
        ) as release:
            kept = await loop._trim_claimed_to_pid_cap([keep, excess])

        assert kept == [keep]
        mocks["todo_repo"].transition.assert_awaited_once_with(
            "TODO-EXCESS", TodoStatus.QUEUED, 3
        )
        release.assert_awaited_once_with(mocks["session"], "batch:TODO-EXCESS")

    @pytest.mark.asyncio
    async def test_requeue_failure_skips_lease_release_and_continues(self):
        loop, mocks = _make_loop()
        first = self._todo("TODO-FIRST")
        second = self._todo("TODO-SECOND")
        loop._tick_state["pid_outputs"] = SimpleNamespace(
            desired_total_active_buckets=0
        )
        loop._active_session = mocks["session"]
        mocks["todo_repo"].transition.side_effect = [
            RuntimeError("CAS lost"),
            None,
        ]

        with patch(
            "general_ludd.event_loop.loop.release_lease", new_callable=AsyncMock
        ) as release:
            kept = await loop._trim_claimed_to_pid_cap([first, second])

        assert kept == []
        release.assert_awaited_once_with(mocks["session"], "core:TODO-SECOND")

    @pytest.mark.asyncio
    async def test_lease_release_failure_is_contained(self):
        loop, mocks = _make_loop()
        excess = self._todo("TODO-EXCESS")
        loop._tick_state["pid_outputs"] = SimpleNamespace(
            desired_total_active_buckets=0
        )
        loop._active_session = mocks["session"]

        with patch(
            "general_ludd.event_loop.loop.release_lease",
            new_callable=AsyncMock,
            side_effect=RuntimeError("lease store down"),
        ):
            assert await loop._trim_claimed_to_pid_cap([excess]) == []

        mocks["todo_repo"].transition.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_malformed_excess_claim_is_dropped_without_repository_calls(self):
        loop, mocks = _make_loop()
        malformed = self._todo("", version=None, queue="")
        loop._tick_state["pid_outputs"] = SimpleNamespace(
            desired_total_active_buckets=0
        )
        loop._active_session = mocks["session"]

        with patch(
            "general_ludd.event_loop.loop.release_lease", new_callable=AsyncMock
        ) as release:
            assert await loop._trim_claimed_to_pid_cap([malformed]) == []

        mocks["todo_repo"].transition.assert_not_awaited()
        release.assert_not_awaited()


class TestPromptContextFallbackBranches:
    """Optional prompt context keeps dispatch available when its stores fail."""

    @pytest.mark.asyncio
    async def test_message_queue_session_failure_uses_empty_section(self):
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(
            side_effect=RuntimeError("message database unavailable")
        )
        loop, _ = _make_loop(config={"message_queue_prompt": True})
        loop._session_factory = factory
        todo = SimpleNamespace(assigned_agent="builder", work_type="code")

        result = await loop._append_message_queue_section(
            "base prompt", todo, "project-1"
        )

        assert result is not None
        assert result.startswith("base prompt\n\n")

    @pytest.mark.asyncio
    async def test_empty_message_queue_render_preserves_prompt(self):
        loop, _ = _make_loop(config={"message_queue_prompt": True})
        todo = SimpleNamespace(assigned_agent=None, work_type=None)

        with patch(
            "general_ludd.prompts.registry.render_message_queue_section",
            return_value="",
        ):
            result = await loop._append_message_queue_section(
                "base prompt", todo, None
            )

        assert result == "base prompt"

    @pytest.mark.asyncio
    async def test_shared_vars_require_both_repository_and_session(self):
        loop, _ = _make_loop()
        loop._variable_repo = AsyncMock()
        loop._active_session = None

        assert await loop._load_shared_vars("project-1") is None
        loop._active_session = AsyncMock()
        loop._variable_repo.load_vars_for_project.return_value = {"region": "east"}
        assert await loop._load_shared_vars("project-1") == {"region": "east"}
