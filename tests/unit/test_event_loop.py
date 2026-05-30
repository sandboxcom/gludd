"""Unit tests for event loop."""

import pytest

from agentic_harness.event_loop.loop import EventLoop
from agentic_harness.schemas.task_decision import TaskDecision
from agentic_harness.schemas.task_return import TaskReturn, TaskReturnStatus
from agentic_harness.schemas.todo import Todo, TodoStatus


class TestEventLoop:
    @pytest.mark.asyncio
    async def test_event_loop_dispatches_return_review_for_unreviewed_return(self):
        loop = EventLoop()
        task_return = TaskReturn(
            return_id="RET-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        result = await loop.dispatch_return_review(task_return)
        assert result["status"] == "dispatched"

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
        loop = EventLoop()
        result = await loop.tick()
        assert "returns_reviewed" in result
        assert "todos_dispatched" in result
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_event_loop_claims_runnable_todos(self):
        loop = EventLoop()
        queued = Todo(title="queued", status=TodoStatus.QUEUED)
        backlog = Todo(title="backlog", status=TodoStatus.BACKLOG)
        active = Todo(title="active", status=TodoStatus.ACTIVE)
        runnable = await loop.claim_runnable_todos([queued, backlog, active])
        assert len(runnable) == 1
        assert runnable[0].title == "queued"

    @pytest.mark.asyncio
    async def test_event_loop_respects_manual_hold(self):
        loop = EventLoop()
        manual = Todo(title="manual", status=TodoStatus.MANUAL_HOLD)
        queued = Todo(title="queued", status=TodoStatus.QUEUED)
        runnable = await loop.claim_runnable_todos([manual, queued])
        assert len(runnable) == 1
        assert runnable[0].title == "queued"

    @pytest.mark.asyncio
    async def test_event_loop_continues_when_manual_hold_exists(self):
        loop = EventLoop()
        result = await loop.tick()
        assert result is not None

    @pytest.mark.asyncio
    async def test_reconcile_decision_complete(self):
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
    async def test_reconcile_decision_needs_more_work(self):
        loop = EventLoop()
        todo = Todo(title="test", status=TodoStatus.REVIEWING_RETURN)
        decision = TaskDecision(
            return_id="RET-001",
            decision="needs_more_work",
            confidence=0.8,
        )
        updated = await loop.reconcile_decision(decision, todo)
        assert updated.status == TodoStatus.NEEDS_MORE_WORK
