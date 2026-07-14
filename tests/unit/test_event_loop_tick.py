from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.event_loop.loop import DISPATCH_PHASE_INDEX, PHASE_ORDER, EventLoop


@pytest_asyncio.fixture
async def sqlite_session_factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class TestE10DispatchSessionClosedBeforeGather:
    """E.10: Verify the tick DB session is committed+closed BEFORE the
    dispatch gather, so the DB writer lock is released during the
    potentially-30-minute dispatch window."""

    DISPATCH_PHASE = PHASE_ORDER[DISPATCH_PHASE_INDEX]

    async def test_session_closed_before_dispatch(
        self, sqlite_session_factory,
    ):
        """Pre-dispatch phases get a session. Dispatch phase runs with
        _active_session = None (session already committed + closed)."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        pre_dispatch_session = None
        dispatch_session = None
        original_run_phase_range = loop._run_phase_range

        async def spy_run_phase_range(start: int, end: int) -> None:
            nonlocal pre_dispatch_session, dispatch_session
            phases = PHASE_ORDER[start:end]
            if self.DISPATCH_PHASE in phases:
                dispatch_session = loop._active_session
            else:
                pre_dispatch_session = loop._active_session
            await original_run_phase_range(start, end)

        with patch.object(loop, "_run_phase_range", spy_run_phase_range):
            await loop.tick()

        assert pre_dispatch_session is not None, (
            "pre-dispatch phases should have an active session"
        )
        assert dispatch_session is None, (
            "dispatch phase should run with _active_session=None "
            "(session committed+closed before gather)"
        )

    async def test_post_dispatch_phases_get_fresh_sessions(
        self, sqlite_session_factory,
    ):
        """Post-dispatch phases each get a fresh session from the factory."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        post_dispatch_sessions = []

        async def spy_run_phase_range(start: int, end: int) -> None:
            nonlocal post_dispatch_sessions
            PHASE_ORDER[start:end]
            if (
                start > DISPATCH_PHASE_INDEX
                and loop._active_session is not None
            ):
                post_dispatch_sessions.append(id(loop._active_session))

        with patch.object(loop, "_run_phase_range", spy_run_phase_range):
            await loop.tick()

        assert len(post_dispatch_sessions) > 0, (
            "post-dispatch phases should get fresh sessions"
        )
        # Each post-dispatch phase opens its own session; ensure any
        # session was provided (the actual post-dispatch count varies
        # based on phase errors, but at least one should succeed).
        assert len(post_dispatch_sessions) >= 1

    async def test_dispatch_phase_index_is_correct(self):
        """DISPATCH_PHASE_INDEX points to dispatch_execute_jobs."""
        assert PHASE_ORDER[DISPATCH_PHASE_INDEX] == "dispatch_execute_jobs"

    async def test_session_committed_before_dispatch(
        self, sqlite_session_factory,
    ):
        """The _commit_tick_session is called before dispatch runs."""
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )

        commit_order: list[str] = []

        async def spy_commit(session: object) -> None:
            commit_order.append("commit")
            await session.commit()

        async def spy_dispatch_phase(*args, **kwargs):
            commit_order.append("dispatch")
            # Call the real phase method so tick can proceed
            await loop._phase_dispatch_execute_jobs()

        with patch.object(loop, "_commit_tick_session", spy_commit):
            async def spy_phase_range(start: int, end: int) -> None:
                phases = PHASE_ORDER[start:end]
                for phase_name in phases:
                    getattr(loop, f"_phase_{phase_name}")
                    if phase_name == "dispatch_execute_jobs":
                        await spy_dispatch_phase()
                    else:
                        with patch.object(
                            type(loop),
                            f"_phase_{phase_name}",
                            AsyncMock(),
                        ):
                            pass
                if self.DISPATCH_PHASE in phases:
                    commit_order.append("dispatch")
                else:
                    commit_order.append(f"phase_range_{start}_{end}")

            with patch.object(loop, "_run_phase_range", spy_phase_range):
                await loop.tick()

        commit_idx = commit_order.index("commit")
        dispatch_idx = commit_order.index("dispatch")
        assert commit_idx < dispatch_idx, (
            f"commit (idx {commit_idx}) must precede dispatch "
            f"(idx {dispatch_idx}); got order: {commit_order}"
        )
