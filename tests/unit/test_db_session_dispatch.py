from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.event_loop.loop import DISPATCH_PHASE_INDEX, PHASE_ORDER, EventLoop


@pytest_asyncio.fixture
async def sqlite_session_factory() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class TestDBSessionPinnedAcrossDispatch:
    """E.10: DB session must be committed + closed BEFORE dispatch gather
    so SQLite's single-writer lock is not held for the potentially
    30-minute dispatch window."""

    async def test_repos_cleared_before_dispatch(self, sqlite_session_factory):
        """All repo references (_todo_repo, _task_return_repo, _audit_repo,
        _variable_repo) must be None when dispatch phase runs."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        repo_state_during_dispatch: dict[str, object] = {}

        async def spy_phase_range(start: int, end: int) -> None:
            phases = PHASE_ORDER[start:end]
            if "dispatch_execute_jobs" in phases:
                repo_state_during_dispatch.update({
                    "_todo_repo": loop._todo_repo,
                    "_task_return_repo": loop._task_return_repo,
                    "_audit_repo": loop._audit_repo,
                    "_variable_repo": loop._variable_repo,
                })

        with patch.object(loop, "_run_phase_range", spy_phase_range), \
             patch.object(loop, "_commit_tick_session", AsyncMock()):
            await loop.tick()

        for name, val in repo_state_during_dispatch.items():
            assert val is None, (
                f"{name} should be None during dispatch, got {val!r}"
            )

    async def test_dispatched_data_visible_to_fresh_session(
        self, sqlite_session_factory,
    ):
        """Data committed by the pre-dispatch tick session must be VISIBLE
        to a fresh session opened inside the dispatch phase. This verifies
        a real commit happened, not just _active_session = None.

        We pre-populate the DB via a separate session (simulating data
        committed by a prior tick), then run tick() and verify the dispatch
        phase can read that committed data from its own fresh session."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        from general_ludd.db.models import TodoModel
        from general_ludd.schemas.todo import TodoStatus

        created_todo_id = f"E10-VIS-{uuid.uuid4().hex[:8]}"

        async with sqlite_session_factory() as prep_session:
            prep_session.add(TodoModel(
                todo_id=created_todo_id,
                project_id="p-e10",
                title="E10 persist test",
                status=TodoStatus.QUEUED.value,
                priority=100,
            ))
            await prep_session.commit()

        row_visible: list[bool] = []

        async def spy_dispatch_phase() -> None:
            async with sqlite_session_factory() as read_session:
                from sqlalchemy import select
                stmt = select(TodoModel).where(
                    TodoModel.todo_id == created_todo_id,
                )
                result = await read_session.execute(stmt)
                row_visible.append(result.scalar_one_or_none() is not None)

        with patch.object(
            loop, "_phase_dispatch_execute_jobs", spy_dispatch_phase
        ):
            await loop.tick()

        assert len(row_visible) == 1, "dispatch phase should have run exactly once"
        assert row_visible[0], (
            f"Todo {created_todo_id} was NOT visible to a fresh session "
            "during dispatch — commit is either not real or not ordered"
        )

    async def test_active_session_none_during_isolated_dispatch(
        self, sqlite_session_factory,
    ):
        """_dispatch_execute_job_isolated must see _active_session = None
        and use its own session override."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        active_was_none: list[bool] = []

        orig_isolated = loop._dispatch_execute_job_isolated

        async def spy_isolated(todo: object) -> None:
            active_was_none.append(loop._active_session is None)

        loop._dispatch_execute_job_isolated = spy_isolated

        call_count: list[int] = []

        async def spy_dispatch_phase() -> None:
            from general_ludd.db.models import TodoModel
            from general_ludd.schemas.todo import TodoStatus

            async with sqlite_session_factory() as setup_session:
                async with setup_session.begin():
                    for i in range(3):
                        setup_session.add(TodoModel(
                            todo_id=f"E10-DISP-{uuid.uuid4().hex[:8]}",
                            project_id="p-e10",
                            title=f"E10 todo {i}",
                            status=TodoStatus.QUEUED.value,
                            priority=100,
                        ))
                await setup_session.commit()

            claimed = []
            async with sqlite_session_factory() as claim_session:
                from general_ludd.db.repository import TodoRepository
                repo = TodoRepository(claim_session)
                claimed = await repo.claim_runnable(limit=10, project_id="p-e10")
                await claim_session.commit()

            for todo in claimed:
                await orig_isolated(todo)
            call_count.append(len(claimed))

        with patch.object(
            loop, "_phase_dispatch_execute_jobs", spy_dispatch_phase
        ):
            await loop.tick()

        assert call_count[0] > 0, (
            "no todos were dispatched; test setup may have failed"
        )
        assert all(active_was_none), (
            "_active_session must be None for every isolated dispatch; "
            f"got {active_was_none}"
        )

    async def test_factory_based_tick_persists_before_dispatch(
        self, sqlite_session_factory,
    ):
        """When using a session_factory, data written pre-dispatch must be
        committed before dispatch phase, and the session must not leak."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        pre_dispatch_committed = False
        dispatch_session_is_none = False

        orig_commit = loop._commit_tick_session

        async def spy_commit(session: AsyncSession) -> None:
            nonlocal pre_dispatch_committed
            await orig_commit(session)
            pre_dispatch_committed = True

        async def spy_phase_range(start: int, end: int) -> None:
            nonlocal dispatch_session_is_none
            phases = PHASE_ORDER[start:end]
            if "dispatch_execute_jobs" in phases:
                dispatch_session_is_none = loop._active_session is None

        with patch.object(loop, "_commit_tick_session", spy_commit), \
             patch.object(loop, "_run_phase_range", spy_phase_range), \
             patch.object(loop, "_phase_dispatch_execute_jobs", AsyncMock()):
            await loop.tick()

        assert pre_dispatch_committed, (
            "_commit_tick_session must be called before dispatch"
        )
        assert dispatch_session_is_none, (
            "_active_session must be None when dispatch phase runs"
        )

    async def test_clear_repos_resets_all_references(
        self, sqlite_session_factory,
    ):
        """_clear_repos must set _active_session and all repos to None."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        repo_names = [
            "_todo_repo", "_task_return_repo",
            "_audit_repo", "_variable_repo",
        ]

        any_factory = sqlite_session_factory
        async with any_factory() as s:
            loop._active_session = s
            from general_ludd.db.repository import (
                AuditEventRepository,
                TaskReturnRepository,
                TodoRepository,
                VariableNamespaceRepository,
            )
            loop._todo_repo = TodoRepository(s)
            loop._task_return_repo = TaskReturnRepository(s)
            loop._audit_repo = AuditEventRepository(s)
            loop._variable_repo = VariableNamespaceRepository(s)

        for name in repo_names:
            assert getattr(loop, name) is not None, (
                f"{name} should be set before _clear_repos"
            )

        loop._clear_repos()

        for name in [*repo_names, "_active_session"]:
            assert getattr(loop, name) is None, (
                f"{name} should be None after _clear_repos()"
            )

    async def test_dispatch_phase_index_invariant(self):
        """DISPATCH_PHASE_INDEX must point to 'dispatch_execute_jobs'."""
        assert PHASE_ORDER.index("dispatch_execute_jobs") == DISPATCH_PHASE_INDEX
        assert PHASE_ORDER[DISPATCH_PHASE_INDEX] == "dispatch_execute_jobs"
        assert DISPATCH_PHASE_INDEX > 0, (
            "dispatch cannot be the first phase"
        )
        assert len(PHASE_ORDER) - 1 > DISPATCH_PHASE_INDEX, (
            "post-dispatch phases must exist"
        )

    async def test_legacy_live_session_path_no_early_close(
        self, sqlite_session_factory,
    ):
        """When passing a bare AsyncSession, E.10 must NOT break the
        legacy code path which runs all phases in one session."""
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as live_session:
                loop = EventLoop(session=live_session, daemon_state={})
                loop.session = live_session
                await loop.tick()

                assert loop._active_session is None, (
                    "_active_session should be cleared after tick "
                    "in legacy path"
                )
        finally:
            await engine.dispose()
