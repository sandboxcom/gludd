"""W3.14 (M14): exactly one project is selected per tick.

Previously the claim and review phases each called select_project()
independently, so one tick could claim from project A and review project B.
The loop must select once per tick and pass the same project to every phase.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import create_async_engine

from general_ludd.db.session import create_async_session_factory, ensure_tables
from general_ludd.event_loop.loop import EventLoop


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await ensure_tables(engine)
    return create_async_session_factory(engine)


class TestSingleProjectPerTick:
    async def test_select_project_called_once_per_tick(self):
        factory = await _make_session_factory()

        project = MagicMock()
        project.project_id = "proj-1"
        pm = MagicMock()
        pm.select_project.return_value = project

        loop = EventLoop(session=factory, project_manager=pm)
        await loop.tick()

        # All phases share one selection; select_project is called exactly once.
        assert pm.select_project.call_count == 1

    async def test_same_project_passed_to_claim_and_review(self):
        # Use an explicit live session + injected repos so we can spy on the
        # project_id each phase used.
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        await ensure_tables(engine)
        sf = create_async_session_factory(engine)

        project = MagicMock()
        project.project_id = "proj-X"
        pm = MagicMock()
        pm.select_project.return_value = project

        seen: list[str | None] = []

        task_return_repo = AsyncMock()

        async def _claim_unreviewed(project_id=None, limit=10):
            seen.append(project_id)
            return []

        task_return_repo.claim_unreviewed.side_effect = _claim_unreviewed

        todo_repo = AsyncMock()

        async def _claim_runnable(project_id=None, limit=10):
            seen.append(project_id)
            return []

        todo_repo.claim_runnable.side_effect = _claim_runnable

        async with sf() as session:
            loop = EventLoop(
                session=session,
                project_manager=pm,
                todo_repo=todo_repo,
                task_return_repo=task_return_repo,
            )
            await loop.tick()

        # Both phases observed the single selected project; selection happened once.
        assert seen == ["proj-X", "proj-X"], seen
        assert pm.select_project.call_count == 1
