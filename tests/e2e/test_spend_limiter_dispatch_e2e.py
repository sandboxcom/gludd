"""E2E regression: SpendLimiter rolling budget cap wired into the real dispatch path.

Residual D-#49 (TASKS.md Phase D): "spend_limiter rolling budget cap: wired
into dispatch path; e2e regression pending."

Wiring under test (see ``event_loop/loop.py:_dispatch_execute_job`` around the
``limiter.try_charge(...)`` gate near line 1840, and
``controllers/spend_limiter.py``):

  * A ``SpendLimiter`` is constructed by the daemon from persisted
    ``spend_records`` rows via ``daemon._restore_persisted_spend`` (real wall
    clock, real DB) BEFORE the event loop's first tick, so the rolling window
    reflects genuine spend history across a restart.
  * ``EventLoop._dispatch_execute_job`` calls ``limiter.try_charge(projected,
    kind="token")`` as the FIRST thing it does. When the atomic check-and-record
    refuses the charge (window spend + projected > limit), dispatch returns
    immediately WITHOUT touching the todo's persisted status/version and
    WITHOUT calling the runner -- the todo is simply left as-is for a later
    tick / lease-reaper pass to retry (it is never marked complete/failed, so
    it is not "lost").
  * When the charge is accepted, dispatch proceeds normally (the runner is
    invoked) and the real limiter's rolling window grows by the accepted cost.

Unlike ``tests/unit/test_spend_limiter_dispatch_wiring.py`` (a MagicMock
limiter + MagicMock todo asserting the call shape), these tests exercise the
REAL ``SpendLimiter`` hydrated from REAL ``spend_records`` rows seeded through
``SpendRepository`` into a real (in-memory sqlite) database, and dispatch a
REAL ``TodoModel`` row fetched through ``TodoRepository`` -- proving the full
spend_records -> SpendLimiter -> EventLoop dispatch-gate chain, not just the
call contract. Only the model gateway / Ansible runner are fakes (per the
task's e2e scope: no live network calls).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.daemon import _restore_persisted_spend
from general_ludd.db.models import Base
from general_ludd.db.repository import SpendRepository, TodoRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import TodoStatus


@pytest_asyncio.fixture
async def db_factory():
    """Real (in-memory) sqlite DB with the full schema, shared via StaticPool."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield factory
    finally:
        await engine.dispose()


def _make_runner() -> MagicMock:
    """Fake Ansible/model-execution runner (no live network / subprocess)."""
    runner = MagicMock()
    runner.run_playbook.return_value = {"rc": 0, "output": "done"}
    runner.prepare_job_dirs.return_value = {"root": "/tmp/test-job"}
    runner.write_vars.return_value = None
    return runner


def _make_loop(spend_limiter, runner) -> EventLoop:
    loop = EventLoop(
        config={"default_playbook": "noop.yml", "budget": {"per_dispatch_usd": 0.01}},
        daemon_state={},
        spend_limiter=spend_limiter,
        runner=runner,
    )
    # Mirror tests/unit/test_spend_limiter_dispatch_wiring.py's minimal shape:
    # this exact set of attribute overrides is what lets model_gateway=None,
    # task_return_repo=None, variable_repo=None dispatch cleanly through to
    # runner.run_playbook without touching MCP/adaptive-router/prompt-registry
    # machinery unrelated to the spend gate under test here.
    loop._active_session = None
    loop._mcp_tool_registry = None
    loop._budget_guard = None
    loop._prompt_registry = None
    loop._config_snapshot = {"default_playbook": "noop.yml"}
    loop._tick_state = {}
    loop._project_workspace = {}
    loop._http_client = None
    loop._adaptive_router = None
    loop._project_secrets_manager = None
    loop._model_gateway = None
    return loop


class TestSpendLimiterDispatchE2E:
    @pytest.mark.asyncio
    async def test_over_budget_project_todo_not_dispatched_and_redispatchable(
        self, db_factory
    ) -> None:
        """(a) An over-budget project's todo is NOT dispatched and is left
        re-dispatchable (its persisted status/version are untouched -- it is
        not silently lost/terminalized)."""
        factory = db_factory
        now = time.time()

        async with factory() as session:
            spend_repo = SpendRepository(session)
            # Real spend_records row that alone blows the 1.00 USD cap.
            await spend_repo.add(
                ts=now, cost_usd=5.00, kind="token",
                project_id="proj-over-budget", model="claude-expensive",
            )
            todo_repo = TodoRepository(session)
            todo = await todo_repo.create(
                todo_data={
                    "todo_id": "TODO-OVER-1",
                    "project_id": "proj-over-budget",
                    "title": "Over budget task",
                    "description": "",
                    "queue": "core",
                    "priority": 50,
                    "work_type": "code",
                    "status": TodoStatus.ACTIVE.value,  # simulate already-claimed
                    "resource_profile": "low_resource",
                }
            )
            await session.commit()
            todo_id = todo.todo_id
            version_before = todo.version
            status_before = todo.status

        # Real SpendLimiter, rehydrated from the REAL seeded spend_records row
        # via the SAME production rehydration path the daemon runs at startup
        # (daemon._restore_persisted_spend) -- not a hand-fed in-memory record.
        limiter = SpendLimiter(limit_usd=1.00, window_seconds=3600, clock=time.time)
        await _restore_persisted_spend(limiter, factory, window_seconds=3600)
        # Sanity: the seeded DB row really did drive the limiter's window.
        assert limiter.window_spend() >= 5.00
        spend_before_dispatch = limiter.window_spend()

        runner = _make_runner()
        loop = _make_loop(spend_limiter=limiter, runner=runner)

        async with factory() as job_session:
            todo_repo2 = TodoRepository(job_session)
            todo_row = await todo_repo2.get_by_id(todo_id)
            assert todo_row is not None

            with patch.object(
                loop, "_resolve_adaptive_prompt",
                new=AsyncMock(return_value=(None, None, None)),
            ):
                await loop._dispatch_execute_job(todo_row)

        assert not runner.run_playbook.called, (
            "Over-budget dispatch must NOT reach run_playbook."
        )
        # A refused try_charge() must NOT also record spend (no double-dip on
        # the window from a rejected charge).
        assert limiter.window_spend() == pytest.approx(spend_before_dispatch)

        # (a) Not lost: re-fetch via a BRAND NEW session/repo and confirm the
        # todo's persisted status/version are exactly as they were before the
        # dispatch attempt -- it was never transitioned to a terminal state
        # (complete/failed/cancelled) nor deleted, so a later tick / the
        # existing lease-reaper mechanism can dispatch it again.
        async with factory() as verify_session:
            verify_repo = TodoRepository(verify_session)
            refetched = await verify_repo.get_by_id(todo_id)
            assert refetched is not None, "Todo must not be deleted/lost."
            assert refetched.status == status_before == TodoStatus.ACTIVE.value
            assert refetched.version == version_before

    @pytest.mark.asyncio
    async def test_under_budget_project_todo_is_dispatched(self, db_factory) -> None:
        """(b) An under-budget project's todo IS dispatched (runner invoked),
        and the real limiter's rolling window grows by the accepted charge."""
        factory = db_factory
        now = time.time()

        async with factory() as session:
            spend_repo = SpendRepository(session)
            # A small real pre-existing spend so the window isn't merely
            # untouched -- it reflects genuine (small) history.
            await spend_repo.add(
                ts=now, cost_usd=0.02, kind="token",
                project_id="proj-under-budget", model="claude-cheap",
            )
            todo_repo = TodoRepository(session)
            todo = await todo_repo.create(
                todo_data={
                    "todo_id": "TODO-UNDER-1",
                    "project_id": "proj-under-budget",
                    "title": "Under budget task",
                    "description": "",
                    "queue": "core",
                    "priority": 50,
                    "work_type": "code",
                    "status": TodoStatus.ACTIVE.value,
                    "resource_profile": "low_resource",
                }
            )
            await session.commit()
            todo_id = todo.todo_id

        limiter = SpendLimiter(limit_usd=100.00, window_seconds=3600, clock=time.time)
        await _restore_persisted_spend(limiter, factory, window_seconds=3600)
        assert 0.0 < limiter.window_spend() < 100.0
        spend_before_dispatch = limiter.window_spend()

        runner = _make_runner()
        loop = _make_loop(spend_limiter=limiter, runner=runner)

        async with factory() as job_session:
            todo_repo2 = TodoRepository(job_session)
            todo_row = await todo_repo2.get_by_id(todo_id)
            assert todo_row is not None

            with patch.object(
                loop, "_resolve_adaptive_prompt",
                new=AsyncMock(return_value=(None, None, None)),
            ):
                await loop._dispatch_execute_job(todo_row)

        assert runner.run_playbook.called, (
            "Under-budget dispatch must reach run_playbook."
        )
        # try_charge() really recorded the accepted charge against the REAL
        # rolling window (not a mocked no-op).
        assert limiter.window_spend() > spend_before_dispatch

    @pytest.mark.asyncio
    async def test_spend_records_rows_drive_the_try_charge_decision(
        self, db_factory
    ) -> None:
        """(c) The persisted spend_records rows -- not some in-memory
        side-channel -- are what drive try_charge()'s accept/refuse decision.

        Same seeded row, same limit: hydrating the window from the DB
        produces a refusal; a limiter with nothing hydrated (no rows) accepts
        the identical projected charge. The row content is the only variable.
        """
        factory = db_factory
        now = time.time()

        async with factory() as session:
            spend_repo = SpendRepository(session)
            await spend_repo.add(
                ts=now, cost_usd=2.50, kind="token",
                project_id="proj-drives-decision", model="m1",
            )
            await session.commit()

        hydrated = SpendLimiter(limit_usd=1.00, window_seconds=3600, clock=time.time)
        await _restore_persisted_spend(hydrated, factory, window_seconds=3600)
        assert hydrated.window_spend() == pytest.approx(2.50)
        assert hydrated.try_charge(0.01, kind="token") is False, (
            "The seeded 2.50 USD spend_records row must drive a refusal "
            "against a 1.00 USD cap."
        )

        # Same limit, but nothing hydrated (as if the spend_records table were
        # empty) -- the identical projected charge must be accepted.
        empty = SpendLimiter(limit_usd=1.00, window_seconds=3600, clock=time.time)
        assert empty.window_spend() == 0.0
        assert empty.try_charge(0.01, kind="token") is True, (
            "With no persisted spend, the same charge must be accepted."
        )
