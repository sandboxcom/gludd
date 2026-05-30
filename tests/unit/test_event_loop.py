"""Unit tests for event loop."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_harness.event_loop.lease import reclaim_expired_leases
from agentic_harness.event_loop.loop import EventLoop
from agentic_harness.schemas.task_decision import TaskDecision
from agentic_harness.schemas.task_return import TaskReturn, TaskReturnStatus
from agentic_harness.schemas.todo import Todo, TodoStatus


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.delete = AsyncMock()
    session.flush = AsyncMock()
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


def _recording_phase(name: str, record: list[str]):
    async def _phase():
        record.append(name)

    return _phase


class TestEventLoop:
    @pytest.mark.asyncio
    async def test_event_loop_tick_runs_all_phases(self):
        loop, _ = _make_loop()
        call_order: list[str] = []
        expected = [
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
        for name in expected:
            setattr(loop, f"_phase_{name}", _recording_phase(name, call_order))
        await loop.tick()
        assert call_order == expected

    @pytest.mark.asyncio
    async def test_event_loop_dispatches_return_review_for_unreviewed_return(self):
        loop, mocks = _make_loop()
        tr = TaskReturn(
            return_id="RET-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        mocks["task_return_repo"].claim_unreviewed.return_value = [tr]
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        await loop._phase_claim_unreviewed_task_returns()
        await loop._phase_dispatch_return_review_jobs()
        mocks["http_client"].post.assert_called_once()
        url = mocks["http_client"].post.call_args[0][0]
        assert "return-review" in url

    @pytest.mark.asyncio
    async def test_event_loop_skips_reviewed_return(self):
        loop = EventLoop()
        task_return = TaskReturn(
            return_id="RET-002",
            job_id="JOB-002",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.REVIEWED,
        )
        result = await loop.dispatch_return_review(task_return)
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_event_loop_never_executes_playbook_inline(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []
        result = await loop.tick()
        assert isinstance(result, dict)
        assert "phases_completed" in result
        mocks["http_client"].post.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_loop_claims_runnable_todos(self):
        loop, mocks = _make_loop()
        queued = Todo(title="queued", status=TodoStatus.QUEUED)
        mocks["todo_repo"].claim_runnable.return_value = [queued]
        await loop._phase_claim_runnable_todos()
        assert len(loop._tick_state["claimed_todos"]) == 1

    @pytest.mark.asyncio
    async def test_event_loop_respects_manual_hold(self):
        loop, mocks = _make_loop()
        queued = Todo(title="queued", status=TodoStatus.QUEUED)
        mocks["todo_repo"].claim_runnable.return_value = [queued]
        await loop._phase_claim_runnable_todos()
        for t in loop._tick_state["claimed_todos"]:
            assert t.status != TodoStatus.MANUAL_HOLD

    @pytest.mark.asyncio
    async def test_event_loop_continues_when_manual_hold_exists(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []
        result = await loop.tick()
        assert result is not None

    @pytest.mark.asyncio
    async def test_event_loop_reclaims_expired_job_lease(self):
        session = AsyncMock()
        expired = MagicMock()
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=60)
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [expired]
        session.execute.return_value = result_mock
        session.delete = AsyncMock()
        session.flush = AsyncMock()
        count = await reclaim_expired_leases(session, max_age_seconds=300)
        assert count == 1
        session.delete.assert_called_once_with(expired)

    @pytest.mark.asyncio
    async def test_event_loop_does_not_reclaim_active_lease(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = []
        session.execute.return_value = result_mock
        session.delete = AsyncMock()
        session.flush = AsyncMock()
        count = await reclaim_expired_leases(session, max_age_seconds=300)
        assert count == 0
        session.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_loop_dispatches_execute_jobs(self):
        loop, mocks = _make_loop()
        todo = Todo(
            title="test task",
            todo_id="TODO-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            resource_profile="low_resource",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        mocks["http_client"].post.assert_called_once()
        url = mocks["http_client"].post.call_args[0][0]
        assert "execute" in url

    @pytest.mark.asyncio
    async def test_event_loop_reconcile_decision_complete(self):
        loop, mocks = _make_loop()
        todo_model = MagicMock()
        todo_model.todo_id = "TODO-001"
        todo_model.status = "reviewing_return"
        todo_model.version = 1
        decision_row = MagicMock()
        decision_row.return_id = "RET-001"
        decision_row.matched_todo_id = "TODO-001"
        decision_row.decision = "complete"
        decision_row.confidence = 0.95
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock
        mocks["todo_repo"].get_by_id.return_value = todo_model
        mocks["todo_repo"].transition = AsyncMock(return_value=todo_model)
        await loop._phase_reconcile_completed_decisions()
        mocks["todo_repo"].transition.assert_called_once_with(
            "TODO-001", TodoStatus.COMPLETE, 1
        )

    @pytest.mark.asyncio
    async def test_event_loop_reconcile_decision_needs_more_work(self):
        loop, mocks = _make_loop()
        todo_model = MagicMock()
        todo_model.todo_id = "TODO-001"
        todo_model.status = "reviewing_return"
        todo_model.version = 1
        decision_row = MagicMock()
        decision_row.return_id = "RET-002"
        decision_row.matched_todo_id = "TODO-001"
        decision_row.decision = "needs_more_work"
        decision_row.confidence = 0.8
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock
        mocks["todo_repo"].get_by_id.return_value = todo_model
        mocks["todo_repo"].transition = AsyncMock(return_value=todo_model)
        await loop._phase_reconcile_completed_decisions()
        mocks["todo_repo"].transition.assert_called_once_with(
            "TODO-001", TodoStatus.NEEDS_MORE_WORK, 1
        )

    @pytest.mark.asyncio
    async def test_event_loop_emits_tick_metrics(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []
        result = await loop.tick()
        assert "phases_completed" in result
        assert "tick_duration_ms" in result
        assert isinstance(result["tick_duration_ms"], float)
        assert result["phases_completed"] == 10

    @pytest.mark.asyncio
    async def test_run_forever_can_be_stopped(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []
        iterations = 0
        original_tick = loop.tick

        async def counting_tick():
            nonlocal iterations
            iterations += 1
            if iterations >= 3:
                loop.stop()
            return await original_tick()

        loop.tick = counting_tick
        await loop.run_forever(interval=0.01)
        assert iterations >= 3

    @pytest.mark.asyncio
    async def test_reconcile_decision_complete_backward_compat(self):
        loop = EventLoop()
        todo = Todo(title="test", status=TodoStatus.REVIEWING_RETURN)
        decision = TaskDecision(
            return_id="RET-001",
            decision="complete",
            confidence=0.95,
        )
        updated = await loop.reconcile_decision(decision, todo)
        assert updated.status == TodoStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_reconcile_decision_needs_more_work_backward_compat(self):
        loop = EventLoop()
        todo = Todo(title="test", status=TodoStatus.REVIEWING_RETURN)
        decision = TaskDecision(
            return_id="RET-001",
            decision="needs_more_work",
            confidence=0.8,
        )
        updated = await loop.reconcile_decision(decision, todo)
        assert updated.status == TodoStatus.NEEDS_MORE_WORK
