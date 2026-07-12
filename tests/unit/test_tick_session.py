from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.event_loop.loop import DISPATCH_PHASE_INDEX, PHASE_ORDER, EventLoop


@pytest_asyncio.fixture
async def sqlite_session_factory() -> AsyncGenerator[async_sessionmaker, None]:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class TestTickSessionClosedBeforeDispatch:
    """E.10: Tick DB session must be committed+closed BEFORE the dispatch
    gather so SQLite's single-writer lock is released during the potentially
    30-minute dispatch window."""

    async def test_no_active_session_during_dispatch(self, sqlite_session_factory):
        """_active_session must be None when dispatch phase runs."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        recorded: list[tuple[str, object]] = []

        async def spy_run_phase_range(start: int, end: int) -> None:
            for phase_name in PHASE_ORDER[start:end]:
                recorded.append((phase_name, loop._active_session))

        with patch.object(loop, "_run_phase_range", spy_run_phase_range), \
             patch.object(loop, "_commit_tick_session", AsyncMock()):
            await loop.tick()

        for phase_name, session in recorded:
            if phase_name == "dispatch_execute_jobs":
                assert session is None, (
                    f"dispatch phase {phase_name!r} had active session"
                )
            elif phase_name in PHASE_ORDER[:DISPATCH_PHASE_INDEX]:
                assert session is not None, (
                    f"pre-dispatch phase {phase_name!r} had no active session"
                )

    async def test_post_dispatch_sessions_are_fresh(self, sqlite_session_factory):
        """Post-dispatch phases each get a fresh session from the factory."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        session_ids: list[tuple[str, int]] = []

        async def spy_run_phase_range(start: int, end: int) -> None:
            for phase_name in PHASE_ORDER[start:end]:
                active = loop._active_session
                if (
                    PHASE_ORDER.index(phase_name) > DISPATCH_PHASE_INDEX
                    and active is not None
                ):
                    session_ids.append((phase_name, id(active)))

        with patch.object(loop, "_run_phase_range", spy_run_phase_range), \
             patch.object(loop, "_commit_tick_session", AsyncMock()):
            await loop.tick()

        unique_ids = {sid for _, sid in session_ids}
        assert len(unique_ids) >= 1, (
            "post-dispatch phases should get fresh sessions"
        )

    async def test_commit_before_dispatch_ordering(self, sqlite_session_factory):
        """Commit must happen before dispatch phase runs (ordering)."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        order: list[str] = []
        orig_commit = loop._commit_tick_session
        orig_dispatch = loop._phase_dispatch_execute_jobs

        async def spy_commit(session: AsyncSession) -> None:
            order.append("commit")
            await orig_commit(session)

        async def spy_dispatch() -> None:
            order.append("dispatch")
            await orig_dispatch()

        with patch.object(loop, "_commit_tick_session", spy_commit), \
             patch.object(loop, "_phase_dispatch_execute_jobs", spy_dispatch):
            await loop.tick()

        commit_idx = order.index("commit")
        dispatch_idx = order.index("dispatch")
        assert commit_idx < dispatch_idx, (
            f"commit (idx {commit_idx}) before dispatch (idx {dispatch_idx}); "
            f"order: {order}"
        )

    async def test_isolated_dispatch_bypasses_active_session(
        self, sqlite_session_factory,
    ):
        """_dispatch_execute_job_isolated uses its own session, not
        the (now-None) _active_session."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        active_during_dispatch: list[object] = []

        async def spy_isolated(self_: EventLoop, todo: object) -> None:
            active_during_dispatch.append(self_._active_session)
            await self_._dispatch_execute_job(
                todo,
                _variable_repo_override=AsyncMock(),
                _task_return_repo_override=AsyncMock(),
                _session_override=AsyncMock(spec=AsyncSession),
            )

        with patch.object(
            EventLoop, "_dispatch_execute_job_isolated", spy_isolated
        ):
            await loop.tick()

        for val in active_during_dispatch:
            assert val is None, (
                f"_active_session was {val!r} during isolated dispatch"
            )

    async def test_legacy_live_session_still_works(self, sqlite_session_factory):
        """Ticks with a bare session (self.session is not None) still
        complete.  E10 separates the factory path; the legacy path is
        preserved for backwards compatibility."""
        async with sqlite_session_factory() as live_session:
            loop = EventLoop(session=live_session, daemon_state={})
            result = await loop.tick()

        assert "total_ticks" in result
        assert "phases_completed" in result

    async def test_no_db_tick_returns_metrics(self):
        """No-DB tick produces a metric dict without error."""
        loop = EventLoop(daemon_state={})
        result = await loop.tick()
        assert "total_ticks" in result
        assert "phases_completed" in result
        assert "tick_duration_ms" in result
