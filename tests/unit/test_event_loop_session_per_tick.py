from __future__ import annotations

import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.event_loop.loop import EventLoop


@pytest.fixture
def sqlite_session_factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory


class TestEventLoopSessionPerTick:
    async def test_tick_opens_session_from_factory(self, sqlite_session_factory):
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )
        assert loop.session is None
        assert loop._session_factory is not None

    async def test_tick_with_live_session_still_works(self, sqlite_session_factory):
        async with sqlite_session_factory() as session:
            loop = EventLoop(session=session, daemon_state={})
            assert loop.session is session
            assert loop._session_factory is None

    async def test_phase_exception_does_not_kill_tick(self):
        loop = EventLoop(daemon_state={})
        loop._phase_claim_runnable_todos = MagicMock(side_effect=ValueError("boom"))

        with patch.object(logging.getLogger("general_ludd.event_loop.loop"), "error") as mock_log:
            result = await loop.tick()

        assert result["phases_completed"] == 11
        mock_log.assert_called()

    async def test_tick_returns_metrics(self):
        loop = EventLoop(daemon_state={})
        result = await loop.tick()
        assert "total_ticks" in result
        assert "phases_completed" in result
        assert "tick_duration_ms" in result

    async def test_active_session_set_and_cleared(self, sqlite_session_factory):
        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=sqlite_session_factory,
            daemon_state={},
        )
        assert not hasattr(loop, "_active_session") or loop._active_session is None
        await loop.tick()
        assert not hasattr(loop, "_active_session") or loop._active_session is None


class TestDaemonLoopDeathLogged:
    async def test_run_forever_death_is_logged(self):
        loop = EventLoop(daemon_state={})
        loop._running = True
        loop.tick = AsyncMock(side_effect=RuntimeError("fatal"))

        with patch.object(logging.getLogger("general_ludd.event_loop.loop"), "error") as mock_log, \
                contextlib.suppress(RuntimeError):
            await loop.run_forever(interval=0.01)

        mock_log.assert_called()
