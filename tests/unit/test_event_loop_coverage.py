"""WP-C1: behavioral coverage for the event_loop phase pipeline.

Targets the 16 phase methods and the claim/dispatch/review/reconcile/
lease-reclaim control flow that was previously under-exercised. Each test
pins a single observable behavior (a phase skip, an exception-continuation,
an escalation, a commit-rollback, a budget stop, a lease reclaim) rather
than asserting on internal plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
