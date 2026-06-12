"""W3.2 (H4): ReturnReviewer + apply_decision wired into the review phase.

The review phase must run a real reviewer (gateway-backed) in-process and route
the resulting decision through apply_decision. A model/review failure must
escalate the todo (never silently mark it complete / pass it through).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy.ext.asyncio import create_async_engine

from general_ludd.db.repository import TaskReturnRepository, TodoRepository
from general_ludd.db.session import (
    create_async_session_factory,
    ensure_tables,
)
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await ensure_tables(engine)
    return create_async_session_factory(engine)


async def _seed_todo_in_review(session, todo_id: str) -> int:
    repo = TodoRepository(session)
    todo = await repo.create(
        {
            "todo_id": todo_id,
            "title": "review me",
            "status": TodoStatus.REVIEWING_RETURN.value,
            "queue": "core",
            "work_type": "code",
        }
    )
    await session.flush()
    return todo.version


async def _seed_return(session, return_id: str, todo_id: str) -> None:
    repo = TaskReturnRepository(session)
    await repo.create(
        {
            "return_id": return_id,
            "todo_id": todo_id,
            "job_id": f"JOB-{return_id}",
            "playbook": "noop.yml",
            "queue": "core",
            "status": "created",
        }
    )
    await session.flush()


class TestReviewerWiring:
    async def test_success_decision_applied_to_todo(self):
        factory = await _make_session_factory()
        async with factory() as session:
            await _seed_todo_in_review(session, "TODO-REV-OK")
            await _seed_return(session, "RET-OK", "TODO-REV-OK")
            await session.commit()

        reviewer = MagicMock()
        reviewer.review_return.return_value = TaskDecision(
            return_id="RET-OK",
            matched_todo_id="TODO-REV-OK",
            decision="complete",
            confidence=0.9,
            evidence_refs=["artifact://diff"],
        )

        loop = EventLoop(session=factory, reviewer=reviewer)
        await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.get_by_id("TODO-REV-OK")
            assert todo is not None
            assert todo.status == TodoStatus.COMPLETE.value
        reviewer.review_return.assert_called()

    async def test_review_failure_does_not_silently_complete(self):
        factory = await _make_session_factory()
        async with factory() as session:
            await _seed_todo_in_review(session, "TODO-REV-FAIL")
            await _seed_return(session, "RET-FAIL", "TODO-REV-FAIL")
            await session.commit()

        # Reviewer's model call failed -> it returns a "failed" decision.
        reviewer = MagicMock()
        reviewer.review_return.return_value = TaskDecision(
            return_id="RET-FAIL",
            matched_todo_id="TODO-REV-FAIL",
            decision="failed",
            confidence=0.0,
            audit_notes=["Model call failed"],
        )

        loop = EventLoop(session=factory, reviewer=reviewer)
        await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.get_by_id("TODO-REV-FAIL")
            assert todo is not None
            assert todo.status != TodoStatus.COMPLETE.value
            assert todo.status == TodoStatus.FAILED.value

    async def test_reviewer_exception_escalates_never_completes(self):
        factory = await _make_session_factory()
        async with factory() as session:
            await _seed_todo_in_review(session, "TODO-REV-EXC")
            await _seed_return(session, "RET-EXC", "TODO-REV-EXC")
            await session.commit()

        reviewer = MagicMock()
        reviewer.review_return.side_effect = RuntimeError("reviewer blew up")

        loop = EventLoop(session=factory, reviewer=reviewer)
        await loop.tick()

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.get_by_id("TODO-REV-EXC")
            assert todo is not None
            assert todo.status != TodoStatus.COMPLETE.value
            # Escalated, not silently passed.
            assert todo.status in {
                TodoStatus.MANUAL_HOLD.value,
                TodoStatus.FAILED.value,
            }
