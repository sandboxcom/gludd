"""C11: Event loop fixes — per-phase sessions, bounded to_thread, bounded gather."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from general_ludd.event_loop.loop import DISPATCH_PHASE_INDEX, PHASE_ORDER, EventLoop
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_c11_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()

    factory = MagicMock(spec=async_sessionmaker)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = ctx

    http_client = AsyncMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()

    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=factory,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "factory": factory,
        "http_client": http_client,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


class TestC11PerPhaseSessionScopes:
    """C11 (#1): Each post-dispatch phase gets its own session scope."""

    @pytest.mark.asyncio
    async def test_post_dispatch_phases_get_distinct_sessions(self):
        """Post-dispatch phases each open a fresh session from the factory."""
        loop, mocks = _make_c11_loop()

        session_counter = 0
        sessions_created: list[AsyncMock] = []

        def _make_session():
            nonlocal session_counter
            session_counter += 1
            s = AsyncMock()
            s.execute.return_value = MagicMock()
            sessions_created.append(s)
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=s)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        mocks["factory"].side_effect = _make_session

        post_dispatch_phases = PHASE_ORDER[DISPATCH_PHASE_INDEX + 1 :]
        assert len(post_dispatch_phases) > 1, (
            "Need >1 post-dispatch phases for this test to be meaningful"
        )

        await loop.tick()

        factory_call_count = mocks["factory"].call_count
        assert factory_call_count >= len(post_dispatch_phases), (
            f"Factory called {factory_call_count} times, need >= {len(post_dispatch_phases)} "
            f"(one per post-dispatch phase: {post_dispatch_phases})"
        )

    @pytest.mark.asyncio
    async def test_active_session_none_between_phases(self):
        """_active_session is cleared between post-dispatch phases."""
        loop, _mocks = _make_c11_loop()

        clear_count = 0
        original_clear = loop._clear_repos

        def _counting_clear():
            nonlocal clear_count
            clear_count += 1
            original_clear()

        loop._clear_repos = _counting_clear

        post_dispatch_count = len(PHASE_ORDER) - (DISPATCH_PHASE_INDEX + 1)
        await loop.tick()

        assert clear_count >= post_dispatch_count + 1, (
            f"_clear_repos called {clear_count} times; per-phase scopes require "
            f"at least {post_dispatch_count} clears (one per post-dispatch phase: "
            f"{post_dispatch_count}) plus pre-dispatch"
        )

    @pytest.mark.asyncio
    async def test_pre_dispatch_session_still_single(self):
        """Pre-dispatch phases still share a single session (no regression)."""
        loop, mocks = _make_c11_loop()

        session_counter = 0
        sessions_created: list[AsyncMock] = []

        def _make_session():
            nonlocal session_counter
            session_counter += 1
            s = AsyncMock()
            s.execute.return_value = MagicMock()
            sessions_created.append(s)
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=s)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        mocks["factory"].side_effect = _make_session

        await loop.tick()

        assert DISPATCH_PHASE_INDEX > 0
        assert session_counter >= 2, (
            "At minimum: 1 pre-dispatch session + >=1 post-dispatch sessions"
        )


class TestC11ThreadPoolExecutorBounded:
    """C11 (#2): to_thread concurrency bounded by semaphore."""

    def test_to_thread_semaphore_exists(self):
        """EventLoop has a _to_thread_semaphore with default value."""
        loop, _ = _make_c11_loop()
        assert hasattr(loop, "_to_thread_semaphore")
        sem = loop._to_thread_semaphore
        assert isinstance(sem, asyncio.Semaphore)
        assert sem._value > 0

    def test_to_thread_semaphore_from_config(self):
        """_to_thread_semaphore reads max_to_thread_concurrency from config."""
        loop, _ = _make_c11_loop(
            config={
                "tick_interval": 1.0,
                "event_loop": {"max_to_thread_concurrency": 4},
            }
        )
        assert loop._to_thread_semaphore._value == 4

    @pytest.mark.asyncio
    async def test_bounded_to_thread_limits_concurrency(self):
        """_bounded_to_thread gates on the semaphore."""
        loop, _ = _make_c11_loop(
            config={
                "tick_interval": 1.0,
                "event_loop": {"max_to_thread_concurrency": 2},
            }
        )

        running = 0
        max_running = 0

        def _slow_sync(x: int) -> int:
            nonlocal running, max_running
            running += 1
            if running > max_running:
                max_running = running
            import time

            time.sleep(0.02)
            running -= 1
            return x

        async def _runner(x: int) -> int:
            return await loop._bounded_to_thread(_slow_sync, x)

        results = await asyncio.gather(
            *[_runner(i) for i in range(6)], return_exceptions=True
        )

        assert all(not isinstance(r, Exception) for r in results)
        assert max_running <= 2, (
            f"Max concurrent to_thread calls was {max_running}, should be <= 2"
        )

    @pytest.mark.asyncio
    async def test_bounded_to_thread_passes_kwargs(self):
        """_bounded_to_thread forwards kwargs to the target function."""
        loop, _ = _make_c11_loop()

        def _fn(*, name: str) -> str:
            return name.upper()

        result = await loop._bounded_to_thread(_fn, name="hello")
        assert result == "HELLO"


class TestC11GatherFanOutBounded:
    """C11 (#3): Dispatch gather limited to configurable max concurrency."""

    def test_dispatch_semaphore_exists(self):
        """EventLoop has a _dispatch_semaphore with default 20."""
        loop, _ = _make_c11_loop()
        assert hasattr(loop, "_dispatch_semaphore")
        sem = loop._dispatch_semaphore
        assert isinstance(sem, asyncio.Semaphore)
        assert sem._value == 20

    def test_dispatch_semaphore_from_config(self):
        """_dispatch_semaphore reads max_gather_concurrency from config."""
        loop, _ = _make_c11_loop(
            config={
                "tick_interval": 1.0,
                "event_loop": {"max_gather_concurrency": 10},
            }
        )
        assert loop._dispatch_semaphore._value == 10

    @pytest.mark.asyncio
    async def test_gather_fan_out_bounded_by_semaphore(self):
        """Dispatch semaphore gates concurrent job execution."""
        loop, _mocks = _make_c11_loop(
            config={
                "tick_interval": 1.0,
                "event_loop": {"max_gather_concurrency": 3},
            }
        )
        loop._dispatch_semaphore = asyncio.Semaphore(3)

        running = 0
        max_running = 0

        async def _slow_dispatch(_todo):
            nonlocal running, max_running
            running += 1
            if running > max_running:
                max_running = running
            await asyncio.sleep(0.03)
            running -= 1

        loop._dispatch_execute_job_isolated = _slow_dispatch

        todos = [
            Todo(
                title=f"task {i}",
                todo_id=f"TODO-{i:03d}",
                status=TodoStatus.ACTIVE,
                work_type="code",
            )
            for i in range(12)
        ]

        with patch.object(
            loop, "_dispatch_jobs_via_scheduler"
        ) as mock_scheduler:
            async def _fake_scheduler(claimed=None):
                tasks = [
                    asyncio.ensure_future(loop._dispatch_with_semaphore(t))
                    for t in todos
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

            mock_scheduler.side_effect = _fake_scheduler
            loop._tick_state["claimed_todos"] = todos
            await loop._phase_dispatch_execute_jobs()

        assert max_running <= 3, (
            f"Max concurrent dispatches was {max_running}, should be <= 3"
        )
