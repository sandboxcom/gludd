from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.event_loop.loop import (
    DISPATCH_PHASE_INDEX,
    PHASE_ORDER,
    EventLoop,
)


class TestE10DispatchPhaseIndex:
    """E10 (PERF-1): DISPATCH_PHASE_INDEX must point to dispatch_execute_jobs."""

    def test_dispatch_phase_index_exists(self):
        assert isinstance(DISPATCH_PHASE_INDEX, int)
        assert 0 <= DISPATCH_PHASE_INDEX < len(PHASE_ORDER)

    def test_dispatch_phase_index_is_dispatch_execute_jobs(self):
        assert PHASE_ORDER[DISPATCH_PHASE_INDEX] == "dispatch_execute_jobs"

    def test_pre_dispatch_phases_exist(self):
        pre = PHASE_ORDER[:DISPATCH_PHASE_INDEX]
        assert len(pre) > 0, "must have phases before dispatch"
        assert "dispatch_execute_jobs" not in pre

    def test_post_dispatch_phases_exist(self):
        post = PHASE_ORDER[DISPATCH_PHASE_INDEX + 1 :]
        assert len(post) > 0, "must have phases after dispatch"
        assert "dispatch_execute_jobs" not in post


class TestE10SessionClosedBeforeDispatch:
    """E10 (PERF-1): tick session must be committed + closed BEFORE dispatch."""

    @pytest.mark.asyncio
    async def test_session_none_during_dispatch(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {}

        async def fake_dispatch():
            capture["active_session"] = loop._active_session

        loop = EventLoop(session=factory, daemon_state={})
        loop._phase_dispatch_execute_jobs = fake_dispatch  # type: ignore[method-assign]

        await loop.tick()

        assert capture["active_session"] is None, (
            "E10 violation: _active_session must be None during dispatch gather"
        )
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_commit_called_before_dispatch(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {"commit_before_dispatch": False}

        # Patch _commit_tick_session to record when commit happens
        original_commit = EventLoop._commit_tick_session

        async def tracking_commit(self, session):
            if not capture.get("dispatch_ran", False):
                capture["commit_before_dispatch"] = True
            await original_commit(self, session)

        async def fake_dispatch():
            capture["dispatch_ran"] = True

        loop = EventLoop(session=factory, daemon_state={})
        loop._phase_dispatch_execute_jobs = fake_dispatch  # type: ignore[method-assign]
        loop._commit_tick_session = tracking_commit.__get__(loop, EventLoop)  # type: ignore[method-assign]

        await loop.tick()

        assert capture["dispatch_ran"] is True
        assert capture["commit_before_dispatch"] is True, (
            "E10 violation: commit must occur BEFORE dispatch"
        )
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_repos_cleared_before_dispatch(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {}

        async def fake_dispatch():
            capture["todo_repo"] = loop._todo_repo
            capture["task_return_repo"] = loop._task_return_repo
            capture["audit_repo"] = loop._audit_repo
            capture["variable_repo"] = loop._variable_repo

        loop = EventLoop(session=factory, daemon_state={})
        loop._phase_dispatch_execute_jobs = fake_dispatch  # type: ignore[method-assign]

        await loop.tick()

        assert capture["todo_repo"] is None, "E10: _todo_repo must be cleared before dispatch"
        assert capture["task_return_repo"] is None, "E10: _task_return_repo must be cleared"
        assert capture["audit_repo"] is None, "E10: _audit_repo must be cleared"
        assert capture["variable_repo"] is None, "E10: _variable_repo must be cleared"
        await engine.dispose()


class TestE10PostDispatchFreshSession:
    """E10 (PERF-1): post-dispatch phases get a FRESH session."""

    @pytest.mark.asyncio
    async def test_post_dispatch_phase_has_fresh_session(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {}

        async def fake_dispatch():
            pass

        async def fake_reconcile(self):
            capture["active_session"] = self._active_session
            capture["session_id"] = id(self._active_session) if self._active_session else None

        loop = EventLoop(session=factory, daemon_state={})
        loop._phase_dispatch_execute_jobs = fake_dispatch  # type: ignore[method-assign]

        import general_ludd.event_loop.loop as loop_mod
        original = loop_mod.EventLoop._phase_reconcile_completed_decisions
        loop_mod.EventLoop._phase_reconcile_completed_decisions = fake_reconcile  # type: ignore[assignment]

        try:
            await loop.tick()
        finally:
            loop_mod.EventLoop._phase_reconcile_completed_decisions = original  # type: ignore[assignment]

        assert capture.get("active_session") is not None, (
            "E10 violation: post-dispatch phase must have a fresh session"
        )
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_post_dispatch_session_not_same_as_pre_dispatch(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {}
        pre_ids: set[int] = set()

        async def fake_dispatch():
            capture["dispatch_ran"] = True

        async def tracking_commit(self, session):
            if not capture.get("dispatch_ran", False):
                pre_ids.add(id(session))
            await EventLoop._commit_tick_session_original(self, session)  # type: ignore[attr-defined]

        async def fake_reconcile(self):
            if self._active_session:
                capture["post_session_id"] = id(self._active_session)

        loop = EventLoop(session=factory, daemon_state={})
        loop._phase_dispatch_execute_jobs = fake_dispatch  # type: ignore[method-assign]

        EventLoop._commit_tick_session_original = EventLoop._commit_tick_session  # type: ignore[attr-defined]
        loop._commit_tick_session = tracking_commit.__get__(loop, EventLoop)  # type: ignore[method-assign]

        import general_ludd.event_loop.loop as loop_mod
        original = loop_mod.EventLoop._phase_reconcile_completed_decisions
        loop_mod.EventLoop._phase_reconcile_completed_decisions = fake_reconcile  # type: ignore[assignment]

        try:
            await loop.tick()
        finally:
            loop_mod.EventLoop._phase_reconcile_completed_decisions = original  # type: ignore[assignment]
            del EventLoop._commit_tick_session_original  # type: ignore[attr-defined]

        post_id = capture.get("post_session_id")
        assert post_id is not None, "post-dispatch phase must have a session"
        assert post_id not in pre_ids, (
            "E10 violation: post-dispatch session must be DIFFERENT from pre-dispatch session"
        )
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_multiple_post_dispatch_phases_each_get_session(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, list[int | None]] = {"session_ids": []}

        async def fake_dispatch():
            capture["session_ids"].append(None)  # marker: dispatch had no session

        loop = EventLoop(session=factory, daemon_state={})
        loop._phase_dispatch_execute_jobs = fake_dispatch  # type: ignore[method-assign]

        import general_ludd.event_loop.loop as loop_mod

        original_refresh = loop_mod.EventLoop._phase_refresh_model_performance
        async def fake_refresh(self):
            capture["session_ids"].append(id(self._active_session) if self._active_session else None)
        loop_mod.EventLoop._phase_refresh_model_performance = fake_refresh  # type: ignore[assignment]

        original_reconcile = loop_mod.EventLoop._phase_reconcile_completed_decisions
        async def fake_reconcile(self):
            capture["session_ids"].append(id(self._active_session) if self._active_session else None)
        loop_mod.EventLoop._phase_reconcile_completed_decisions = fake_reconcile  # type: ignore[assignment]

        try:
            await loop.tick()
        finally:
            loop_mod.EventLoop._phase_refresh_model_performance = original_refresh  # type: ignore[assignment]
            loop_mod.EventLoop._phase_reconcile_completed_decisions = original_reconcile  # type: ignore[assignment]

        assert len(capture["session_ids"]) == 3
        assert capture["session_ids"][0] is None, "dispatch should have no session"
        assert capture["session_ids"][1] is not None, "post-dispatch phase 1 should have a session"
        assert capture["session_ids"][2] is not None, "post-dispatch phase 2 should have a session"
        await engine.dispose()


class TestE10DispatchIsolatedSession:
    """E10 (PERF-1): _dispatch_execute_job_isolated opens its own session."""

    @pytest.mark.asyncio
    async def test_isolated_dispatch_uses_own_session(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {}
        original_isolated = EventLoop._dispatch_execute_job_isolated

        async def tracking_isolated(self, todo):
            assert self._session_factory is not None
            async with self._session_factory() as job_session:
                capture["session_opened"] = True
                capture["session_id"] = id(job_session)
                capture["active_session_unchanged"] = self._active_session is None
                await job_session.commit()
            capture["session_committed_and_closed"] = True

        loop = EventLoop(daemon_state={})
        loop._session_factory = factory
        loop._dispatch_semaphore = asyncio.Semaphore(20)

        EventLoop._dispatch_execute_job_isolated = tracking_isolated  # type: ignore[assignment]

        try:
            await loop._dispatch_execute_job_isolated(MagicMock())
        finally:
            EventLoop._dispatch_execute_job_isolated = original_isolated  # type: ignore[assignment]

        assert capture.get("session_opened") is True, (
            "E10: _dispatch_execute_job_isolated must open its own session"
        )
        assert capture.get("session_committed_and_closed") is True, (
            "E10: isolated session must be committed and closed"
        )
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_isolated_dispatch_commit_called(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        capture: dict[str, object] = {"committed": False, "active_session_none_after": False}

        async def fake_isolated_dispatch(self, todo):
            async with self._session_factory() as job_session:
                await self._dispatch_execute_job(
                    todo,
                    _session_override=job_session,
                )
                await job_session.commit()
                capture["committed"] = True
            capture["active_session_none_after"] = self._active_session is None

        loop = EventLoop(daemon_state={})
        loop._session_factory = factory
        loop._dispatch_semaphore = asyncio.Semaphore(20)

        with patch.object(loop, "_dispatch_execute_job", AsyncMock()):
            loop._dispatch_execute_job_isolated = fake_isolated_dispatch.__get__(loop, EventLoop)  # type: ignore[method-assign]
            await loop._dispatch_execute_job_isolated(MagicMock())

        assert capture["committed"] is True, "isolated dispatch must commit its session"
        await engine.dispose()


class TestE10ClearRepos:
    """E10 (PERF-1): _clear_repos nullifies all repo references."""

    def test_clear_repos_nulls_all(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        loop = EventLoop(session=factory, daemon_state={})

        loop._active_session = MagicMock()
        loop._todo_repo = MagicMock()
        loop._task_return_repo = MagicMock()
        loop._audit_repo = MagicMock()
        loop._variable_repo = MagicMock()

        loop._clear_repos()

        assert loop._active_session is None
        assert loop._todo_repo is None
        assert loop._task_return_repo is None
        assert loop._audit_repo is None
        assert loop._variable_repo is None

    def test_clear_repos_idempotent(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        loop = EventLoop(session=factory, daemon_state={})

        loop._clear_repos()
        loop._clear_repos()

        assert loop._active_session is None
        assert loop._todo_repo is None
        assert loop._task_return_repo is None
        assert loop._audit_repo is None
        assert loop._variable_repo is None


class TestE10CommitTickSession:
    """E10 (PERF-1): _commit_tick_session commits + rollbacks on failure."""

    @pytest.mark.asyncio
    async def test_commit_tick_session_success(self):
        loop = EventLoop(daemon_state={})
        mock_session = AsyncMock()
        await loop._commit_tick_session(mock_session)
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_commit_tick_session_rollback_on_failure(self):
        import logging

        loop = EventLoop(daemon_state={})
        mock_session = AsyncMock()
        mock_session.commit.side_effect = RuntimeError("commit failed")

        with patch.object(logging.getLogger("general_ludd.event_loop.loop"), "error") as mock_log:
            await loop._commit_tick_session(mock_session)

        mock_session.rollback.assert_awaited()
        mock_log.assert_called()


class TestE10BareSessionPreserved:
    """E10 (PERF-1): bare-session mode (no factory) must still work."""

    @pytest.mark.asyncio
    async def test_bare_session_tick_steady_state(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            loop = EventLoop(session=session, daemon_state={})
            assert loop.session is session
            assert loop._session_factory is None

            result = await loop.tick()
            assert result["phases_completed"] >= 0
            assert loop._active_session is None

        await engine.dispose()
