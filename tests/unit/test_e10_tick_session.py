from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class TestE10TickSessionClosedBeforeDispatch:
    """E10 / PERF-1: tick session must be committed/closed BEFORE the dispatch
    gather so the writer lock is released during the potentially-30-minute
    dispatch window."""

    @pytest.mark.asyncio
    async def test_session_closed_before_dispatch_gather(self):
        """Assert that when a tick opens its own session via session_factory,
        the session is committed and _active_session is cleared BEFORE
        _phase_dispatch_execute_jobs runs."""
        from general_ludd.event_loop.loop import EventLoop

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {}

        async def fake_dispatch():
            capture["active_session_during_dispatch"] = loop._active_session
            capture["dispatched"] = True

        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=factory,
            daemon_state={},
        )
        assert loop._session_factory is not None
        assert loop.session is None

        loop._phase_dispatch_execute_jobs = fake_dispatch  # type: ignore[method-assign]

        await loop.tick()

        assert capture.get("dispatched") is True, "dispatch phase must have run"
        assert capture.get("active_session_during_dispatch") is None, (
            "E10 violation: _active_session must be None during dispatch gather; "
            f"got {capture.get('active_session_during_dispatch')!r}"
        )

        assert loop._active_session is None, "session must be cleared after tick"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_fresh_session_for_post_dispatch_phases(self):
        """Assert that reconcile runs with a FRESH session (not the pre-dispatch
        one).  We capture the session identity before dispatch and compare it
        to the session identity during reconcile."""
        from general_ludd.event_loop.loop import EventLoop

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {}

        async def fake_dispatch():
            pass

        orig_reconcile = None

        async def fake_reconcile(self):  # type: ignore[no-untyped-def]
            capture["active_session_during_reconcile"] = self._active_session
            capture["reconciled"] = True

        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=factory,
            daemon_state={},
        )
        loop._phase_dispatch_execute_jobs = fake_dispatch  # type: ignore[method-assign]

        import general_ludd.event_loop.loop as loop_mod
        orig_reconcile = loop_mod.EventLoop._phase_reconcile_completed_decisions  # type: ignore[assignment]
        loop_mod.EventLoop._phase_reconcile_completed_decisions = fake_reconcile  # type: ignore[assignment]

        try:
            await loop.tick()
        finally:
            if orig_reconcile is not None:
                loop_mod.EventLoop._phase_reconcile_completed_decisions = orig_reconcile  # type: ignore[assignment]

        assert capture.get("reconciled") is True, "reconcile phase must have run"
        active = capture.get("active_session_during_reconcile")
        assert active is not None, (
            "E10 violation: reconcile must have a fresh session; "
            f"got _active_session={active!r}"
        )

        assert loop._active_session is None, "session must be cleared after tick"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_session_per_tick_still_works(self):
        """Regression: existing session-per-tick behaviour must be preserved —
        tick opens, uses, and closes its session."""
        from general_ludd.event_loop.loop import EventLoop

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=factory,
            daemon_state={},
        )
        assert loop.session is None
        assert loop._session_factory is not None

    @pytest.mark.asyncio
    async def test_tick_with_bare_session_still_works(self):
        """Regression: passing a bare session (no session_factory) must still
        work — dispatch runs sequentially inside the shared session."""
        from general_ludd.event_loop.loop import EventLoop

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            loop = EventLoop(session=session, daemon_state={})
            assert loop.session is session
            assert loop._session_factory is None

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_phase_exception_does_not_kill_tick(self):
        """Regression: a phase exception must not kill the tick."""
        import logging
        from unittest.mock import patch

        from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop

        loop = EventLoop(daemon_state={})
        loop._phase_claim_runnable_todos = MagicMock(side_effect=ValueError("boom"))  # type: ignore[method-assign]

        with patch.object(logging.getLogger("general_ludd.event_loop.loop"), "error") as mock_log:
            result = await loop.tick()

        assert result["phases_completed"] == len(PHASE_ORDER) - 1
        mock_log.assert_called()

    @pytest.mark.asyncio
    async def test_tick_returns_metrics(self):
        """Regression: tick must return expected metrics keys."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(daemon_state={})
        result = await loop.tick()
        assert "total_ticks" in result
        assert "phases_completed" in result
        assert "tick_duration_ms" in result

    @pytest.mark.asyncio
    async def test_active_session_cleared_after_tick(self):
        """Regression: _active_session must be None after tick completes."""
        from general_ludd.event_loop.loop import EventLoop

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=factory,
            daemon_state={},
        )
        assert not hasattr(loop, "_active_session") or loop._active_session is None
        await loop.tick()
        assert not hasattr(loop, "_active_session") or loop._active_session is None
        await engine.dispose()
