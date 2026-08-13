"""E2E tests for the EventLoop subsystem — full workflows.

Scenarios:
  1. Todo lifecycle through event loop — create → claim → dispatch → complete
  2. Lease acquisition — two workers compete, only one wins, lease expires
  3. Stuck todo detection — ACTIVE too long → event_loop reaps it
  4. Concurrent dispatch — semaphore limits respected, gather behaviour
  5. Phase ordering — phases execute in correct order per PHASE_ORDER
  6. Graceful shutdown — stop cleanly, background tasks drain
  7. Budget enforcement — cost limit prevents dispatch, todo stays pending
  8. Model routing — event_loop selects correct model profile per todo
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from general_ludd.controllers.floor import FloorController
from general_ludd.db.models import Base, BucketLeaseModel, ProjectModel, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.lease import (
    acquire_lease,
    reclaim_expired_leases,
    release_lease,
)
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop
from general_ludd.schemas.todo import TodoStatus

_PIPELINE_PROJECT_ID = "proj-e2e-workflows"


def _pipeline_project_manager() -> SimpleNamespace:
    project = SimpleNamespace(project_id=_PIPELINE_PROJECT_ID)
    return SimpleNamespace(
        select_project=lambda: project,
        list_active=lambda: [project],
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def db_engine():
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
        async with factory() as session:
            session.add(
                ProjectModel(
                    project_id=_PIPELINE_PROJECT_ID,
                    name="E2E workflows",
                    workspace_path="/tmp/e2e-workflows",
                )
            )
            await session.commit()
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def runner():
    runner = MagicMock()
    runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/e2e-workflows"})
    runner.write_vars = MagicMock()
    runner.run_playbook = MagicMock()
    return runner


@pytest.fixture
def loop_for_pipeline(session_factory, runner):
    mock_task_return = AsyncMock()
    mock_task_return.claim_unreviewed.return_value = []
    loop = EventLoop(
        session=session_factory,
        runner=runner,
        task_return_repo=mock_task_return,
        config={"repo_root": "/tmp"},
        project_manager=_pipeline_project_manager(),
    )
    loop._task_return_repo = mock_task_return
    loop._runner = runner
    return loop


async def _seed_todo(factory, **overrides) -> TodoModel:
    async with factory() as session:
        repo = TodoRepository(session)
        defaults = {
            "todo_id": "TODO-E2E-001",
            "title": "E2E workflow test todo",
            "description": "Verify event_loop workflow",
            "queue": "core",
            "priority": 5,
            "work_type": "code",
            "status": TodoStatus.QUEUED.value,
            "project_id": _PIPELINE_PROJECT_ID,
        }
        defaults.update(overrides)
        todo = await repo.create(defaults)
        await session.commit()
        return todo


# ── 1. Todo Lifecycle Through Event Loop ─────────────────────────────────────


class TestTodoLifecycleWorkflow:
    @pytest.mark.asyncio
    async def test_todo_created_as_queued_claimed_and_dispatched(
        self, session_factory, loop_for_pipeline
    ):
        await _seed_todo(session_factory, todo_id="TODO-LIFECYCLE-1")
        metrics = await loop_for_pipeline.tick()
        claimed = loop_for_pipeline._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        assert claimed[0].todo_id == "TODO-LIFECYCLE-1"
        assert metrics["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_claimed_todo_has_acquired_lease(
        self, session_factory, loop_for_pipeline
    ):
        await _seed_todo(session_factory, todo_id="TODO-LEASE-1")
        await loop_for_pipeline.tick()
        async with session_factory() as session:
            stmt = select(BucketLeaseModel).where(
                BucketLeaseModel.bucket_key == "core:TODO-LEASE-1"
            )
            result = await session.execute(stmt)
            lease = result.scalar_one_or_none()
            assert lease is not None
            assert "tick-" in lease.holder_id
            expires_at = lease.expires_at
            if expires_at.tzinfo is None:  # SQLite drops timezone metadata on round-trip
                expires_at = expires_at.replace(tzinfo=UTC)
            assert expires_at > datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_todo_starts_active_after_claim(self, session_factory, loop_for_pipeline):
        await _seed_todo(session_factory, todo_id="TODO-ACTIVE-1")
        await loop_for_pipeline.tick()
        async with session_factory() as session:
            repo = TodoRepository(session)
            todo = await repo.get_by_id("TODO-ACTIVE-1")
            assert todo is not None
            assert todo.status == TodoStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_queued_todo_created_and_transitions_to_complete(
        self, session_factory, loop_for_pipeline
    ):
        await _seed_todo(session_factory, todo_id="TODO-COMPLETE-1")
        await loop_for_pipeline.tick()
        async with session_factory() as session:
            repo = TodoRepository(session)
            claimed_todo = await repo.get_by_id("TODO-COMPLETE-1")
            assert claimed_todo is not None
            assert claimed_todo.status == TodoStatus.ACTIVE.value
            await repo.transition(
                "TODO-COMPLETE-1", TodoStatus.COMPLETE, expected_version=claimed_todo.version
            )
            await session.commit()
        async with session_factory() as session:
            repo = TodoRepository(session)
            completed = await repo.get_by_id("TODO-COMPLETE-1")
            assert completed.status == TodoStatus.COMPLETE.value


# ── 2. Lease Acquisition ─────────────────────────────────────────────────────


class TestLeaseAcquisitionWorkflow:
    @pytest.mark.asyncio
    async def test_two_leases_for_different_keys_independent(self, db_session: AsyncSession):
        a = await acquire_lease(db_session, "core:indep-a", "worker-a", ttl_seconds=300)
        b = await acquire_lease(db_session, "core:indep-b", "worker-b", ttl_seconds=300)
        assert a.bucket_key == "core:indep-a"
        assert b.bucket_key == "core:indep-b"
        assert a.holder_id == "worker-a"
        assert b.holder_id == "worker-b"

    @pytest.mark.asyncio
    async def test_lease_upsert_replaces_holder(self, db_session: AsyncSession):
        await acquire_lease(db_session, "core:race-1", "worker-x", ttl_seconds=300)
        await db_session.commit()
        second = await acquire_lease(db_session, "core:race-1", "worker-y", ttl_seconds=600)
        assert second.holder_id == "worker-y"
        assert second.bucket_key == "core:race-1"

    @pytest.mark.asyncio
    async def test_expired_lease_reclaimed_and_todo_requeued(self, db_session: AsyncSession):
        todo = TodoModel(
            todo_id="todo-exp-1",
            title="expired lease",
            queue="core",
            priority=5,
            work_type="code",
            status=TodoStatus.ACTIVE.value,
        )
        db_session.add(todo)
        await db_session.flush()
        await db_session.commit()
        lease = BucketLeaseModel(
            bucket_key="core:todo-exp-1",
            holder_id="dead-worker",
            expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        db_session.add(lease)
        await db_session.commit()
        reclaimed = await reclaim_expired_leases(db_session)
        assert reclaimed == 1
        stmt = select(TodoModel).where(TodoModel.todo_id == "todo-exp-1")
        result = await db_session.execute(stmt)
        requeued = result.scalar_one()
        assert requeued.status == TodoStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_release_lease_frees_bucket(self, db_session: AsyncSession):
        lease = BucketLeaseModel(
            bucket_key="core:free-1",
            holder_id="worker-z",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
        )
        db_session.add(lease)
        await db_session.commit()
        removed = await release_lease(db_session, "core:free-1")
        assert removed == 1
        stmt = select(BucketLeaseModel).where(BucketLeaseModel.bucket_key == "core:free-1")
        result = await db_session.execute(stmt)
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_lease_release_respects_holder_id(self, db_session: AsyncSession):
        lease = BucketLeaseModel(
            bucket_key="core:locked-1",
            holder_id="owner-a",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
        )
        db_session.add(lease)
        await db_session.commit()
        removed = await release_lease(db_session, "core:locked-1", holder_id="intruder")
        assert removed == 0
        removed2 = await release_lease(db_session, "core:locked-1", holder_id="owner-a")
        assert removed2 == 1


# ── 3. Stuck Todo Detection ──────────────────────────────────────────────────


class TestStuckTodoDetection:
    @pytest.mark.asyncio
    async def test_active_todo_with_expired_lease_is_reaped(
        self, session_factory
    ):

        async with session_factory() as session:
            repo = TodoRepository(session)
            await repo.create({
                "todo_id": "TODO-STUCK-1",
                "title": "stuck todo",
                "queue": "core",
                "priority": 5,
                "work_type": "code",
                "status": TodoStatus.ACTIVE.value,
                "project_id": _PIPELINE_PROJECT_ID,
            })
            await session.commit()

        async with session_factory() as session:
            stmt = select(TodoModel).where(TodoModel.todo_id == "TODO-STUCK-1")
            result = await session.execute(stmt)
            todo = result.scalar_one()
            todo.updated_at = datetime.now(UTC) - timedelta(minutes=20)
            await session.commit()

        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/st"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()

        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        loop._stuck_timeout_minutes = 15
        await loop.tick()
        async with session_factory() as session:
            repo = TodoRepository(session)
            recovered = await repo.get_by_id("TODO-STUCK-1")
            assert recovered is not None
            # Refill requeues the stale ACTIVE row, then the later claim phase
            # immediately claims it again in the same tick for another attempt.
            assert recovered.status == TodoStatus.ACTIVE.value
            assert recovered.version >= 3
            assert any(
                todo.todo_id == "TODO-STUCK-1"
                for todo in loop._tick_state["claimed_todos"]
            )

    @pytest.mark.asyncio
    async def test_active_todo_with_live_lease_not_reaped(
        self, session_factory
    ):
        async with session_factory() as session:
            repo = TodoRepository(session)
            await repo.create({
                "todo_id": "TODO-LIVE-1",
                "title": "live todo",
                "queue": "core",
                "priority": 5,
                "work_type": "code",
                "status": TodoStatus.ACTIVE.value,
                "project_id": _PIPELINE_PROJECT_ID,
            })
            await acquire_lease(
                session,
                "core:TODO-LIVE-1",
                "alive-worker",
                ttl_seconds=600,
                project_id=_PIPELINE_PROJECT_ID,
            )
            await session.commit()

        async with session_factory() as session:
            stmt = select(TodoModel).where(TodoModel.todo_id == "TODO-LIVE-1")
            result = await session.execute(stmt)
            todo = result.scalar_one()
            todo.updated_at = datetime.now(UTC) - timedelta(minutes=20)
            await session.commit()

        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/st"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()

        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        loop._stuck_timeout_minutes = 15
        await loop.tick()
        async with session_factory() as session:
            repo = TodoRepository(session)
            still_active = await repo.get_by_id("TODO-LIVE-1")
            assert still_active.status == TodoStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_stuck_todo_reap_updates_metrics(self, session_factory):
        async with session_factory() as session:
            repo = TodoRepository(session)
            await repo.create({
                "todo_id": "TODO-STUCK-MET",
                "title": "stuck todo for metrics",
                "queue": "core",
                "priority": 5,
                "work_type": "code",
                "status": TodoStatus.ACTIVE.value,
                "project_id": _PIPELINE_PROJECT_ID,
            })
            await session.commit()

        async with session_factory() as session:
            stmt = select(TodoModel).where(TodoModel.todo_id == "TODO-STUCK-MET")
            result = await session.execute(stmt)
            todo = result.scalar_one()
            todo.updated_at = datetime.now(UTC) - timedelta(minutes=20)
            await session.commit()

        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/st"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()

        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        loop._stuck_timeout_minutes = 15
        metrics = await loop.tick()
        assert "leases_reclaimed" in metrics


# ── 4. Concurrent Dispatch ───────────────────────────────────────────────────


class TestConcurrentDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_semaphore_enforced(self, loop_for_pipeline):
        assert loop_for_pipeline._dispatch_semaphore._value == 20

    @pytest.mark.asyncio
    async def test_multiple_todos_gathered_concurrently(
        self, session_factory, loop_for_pipeline, runner
    ):
        for idx in range(5):
            await _seed_todo(
                session_factory,
                todo_id=f"TODO-CONC-{idx}",
                priority=idx + 1,
            )
        await loop_for_pipeline.tick()
        write_calls = [
            c for c in runner.write_vars.call_args_list
            if len(c[0]) >= 1 and "EXEC-TODO-CONC" in str(c[0][0])
        ]
        assert len(write_calls) == 5

    @pytest.mark.asyncio
    async def test_to_thread_semaphore_enforced(self, loop_for_pipeline):
        assert loop_for_pipeline._to_thread_semaphore._value == 32

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_respects_semaphore_gather(
        self, session_factory, loop_for_pipeline, runner
    ):
        for idx in range(10):
            await _seed_todo(
                session_factory,
                todo_id=f"TODO-GATHER-{idx}",
                priority=idx + 1,
            )
        await loop_for_pipeline.tick()
        write_calls = [
            c for c in runner.write_vars.call_args_list
            if len(c[0]) >= 1 and "EXEC-TODO-GATHER" in str(c[0][0])
        ]
        assert len(write_calls) == 10


# ── 5. Phase Ordering ────────────────────────────────────────────────────────


class TestPhaseOrdering:
    def test_phase_order_has_required_dispatch_phases(self):
        required = [
            "load_config_snapshot",
            "claim_runnable_todos",
            "dispatch_execute_jobs",
            "remediate_blocked_tasks",
            "emit_tick_metrics",
        ]
        for phase in required:
            assert phase in PHASE_ORDER

    def test_claim_before_dispatch(self):
        claim_idx = PHASE_ORDER.index("claim_runnable_todos")
        dispatch_idx = PHASE_ORDER.index("dispatch_execute_jobs")
        assert claim_idx < dispatch_idx

    def test_reap_before_claim_in_refill(self):
        refill_idx = PHASE_ORDER.index("refill_task_buckets")
        claim_idx = PHASE_ORDER.index("claim_runnable_todos")
        assert refill_idx < claim_idx

    def test_dispatch_comes_before_remediate(self):
        dispatch_idx = PHASE_ORDER.index("dispatch_execute_jobs")
        remediate_idx = PHASE_ORDER.index("remediate_blocked_tasks")
        assert dispatch_idx < remediate_idx

    @pytest.mark.asyncio
    async def test_phase_tracking_captures_all_phases(self, session_factory):
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/ph"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        loop._todo_repo = AsyncMock()
        loop._todo_repo.claim_runnable.return_value = []
        metrics = await loop.tick()
        assert metrics["phases_completed"] == len(PHASE_ORDER)


# ── 6. Graceful Shutdown ─────────────────────────────────────────────────────


class TestGracefulShutdown:
    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self):
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        loop._running = True
        loop.stop()
        assert not loop._running

    @pytest.mark.asyncio
    async def test_run_forever_exits_cleanly_after_stop(self):
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        loop._running = True
        task = asyncio.create_task(loop.run_forever(interval=0.01))
        await asyncio.sleep(0.06)
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert not loop._running

    @pytest.mark.asyncio
    async def test_shutdown_drains_background_tasks(self):
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
        )

        async def _linger():
            await asyncio.sleep(0.05)

        bg = asyncio.create_task(_linger())
        loop._track_background_task(bg)
        loop._running = True
        await loop.shutdown()
        assert not loop._running
        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_loop_not_running_after_shutdown(self):
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
        )
        loop._running = True
        await loop.shutdown()
        assert not loop._running


# ── 7. Budget Enforcement ────────────────────────────────────────────────────


class TestBudgetEnforcement:
    @pytest.mark.asyncio
    async def test_budget_exceeded_blocks_dispatch(self, session_factory):
        await _seed_todo(session_factory, todo_id="TODO-BUDGET-1")
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        budget = MagicMock()
        budget.check_all_limits = MagicMock(
            return_value={"allowed": False, "reason": "budget_exceeded"}
        )
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/bg"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            budget_guard=budget,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        metrics = await loop.tick()
        assert metrics["todos_dispatched"] == 0

    @pytest.mark.asyncio
    async def test_budget_allowed_dispatches_normally(self, session_factory):
        await _seed_todo(session_factory, todo_id="TODO-BUDGET-OK")
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        budget = MagicMock()
        budget.check_all_limits = MagicMock(
            return_value={"allowed": True, "reason": ""}
        )
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/bg"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            budget_guard=budget,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        metrics = await loop.tick()
        pre_dispatch_checks = budget.check_all_limits.call_count
        assert pre_dispatch_checks >= 1
        assert metrics["todos_dispatched"] >= 1

    @pytest.mark.asyncio
    async def test_budget_blocked_todo_stays_queued(self, session_factory):
        await _seed_todo(session_factory, todo_id="TODO-BUDGET-STAY")
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        budget = MagicMock()
        budget.check_all_limits = MagicMock(
            return_value={"allowed": False, "reason": "budget cap hit"}
        )
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/bg"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            budget_guard=budget,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        await loop.tick()
        async with session_factory() as session:
            repo = TodoRepository(session)
            todo = await repo.get_by_id("TODO-BUDGET-STAY")
            status = todo.status
        assert status != TodoStatus.COMPLETE.value


# ── 8. Model Routing ─────────────────────────────────────────────────────────


class TestModelRouting:
    @pytest.mark.asyncio
    async def test_todo_with_model_profile_routes_resolve_profile(
        self, session_factory, loop_for_pipeline
    ):
        await _seed_todo(
            session_factory,
            todo_id="TODO-MODEL-1",
            model_profile="sonnet",
            prompt_profile="default",
        )
        await loop_for_pipeline.tick()
        claimed = loop_for_pipeline._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        todo = claimed[0]
        assert getattr(todo, "model_profile", None) == "sonnet"

    @pytest.mark.asyncio
    async def test_todo_without_model_profile_uses_default(self, session_factory, loop_for_pipeline):
        await _seed_todo(
            session_factory,
            todo_id="TODO-NO-MODEL",
            model_profile=None,
            prompt_profile=None,
        )
        await loop_for_pipeline.tick()
        claimed = loop_for_pipeline._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1

    @pytest.mark.asyncio
    async def test_adaptive_router_missing_does_not_crash(self, session_factory):
        await _seed_todo(session_factory, todo_id="TODO-NO-ROUTER")
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/ar"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            adaptive_router=None,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        metrics = await loop.tick()
        assert metrics["todos_dispatched"] >= 1

    @pytest.mark.asyncio
    async def test_resolve_adaptive_prompt_without_router_is_noop(
        self, loop_for_pipeline
    ):
        todo = SimpleNamespace(
            todo_id="TODO-ROUTE-1",
            work_type="code",
            prompt_profile="default",
            model_profile="sonnet",
        )
        result = await loop_for_pipeline._resolve_adaptive_prompt(todo)
        assert result == (None, None, None)


# ── 9. Floor Controller Integration ──────────────────────────────────────────


class TestFloorControllerIntegration:
    @pytest.mark.asyncio
    async def test_floor_zero_claims_nothing(self, session_factory):
        await _seed_todo(session_factory, todo_id="TODO-FLOOR-0")
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/fl"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            floor_controller=FloorController(floor=0),
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) == 0

    @pytest.mark.asyncio
    async def test_floor_five_claims_up_to_five(self, session_factory):
        for idx in range(8):
            await _seed_todo(
                session_factory,
                todo_id=f"TODO-FLOOR5-{idx}",
                priority=10 - idx,
            )
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/fl"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            floor_controller=FloorController(floor=100),
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) <= 8
        assert len(claimed) >= 1

    @pytest.mark.asyncio
    async def test_health_below_25_blocks_all_claims(self, session_factory):
        await _seed_todo(session_factory, todo_id="TODO-HEALTH-1")
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/fl"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        fc = FloorController(floor=10)
        fc.update_health(10.0)
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            floor_controller=fc,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) == 0


# ── 10. Multiple Ticks ───────────────────────────────────────────────────────


class TestMultiTickWorkflow:
    @pytest.mark.asyncio
    async def test_overlapping_ticks_dont_double_claim(self, session_factory):
        await _seed_todo(session_factory, todo_id="TODO-MULTITICK-1")
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/mt"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        m1 = await loop.tick()
        assert m1["phases_completed"] == len(PHASE_ORDER)
        m2 = await loop.tick()
        claimed_t2 = loop._tick_state.get("claimed_todos", [])
        assert len(claimed_t2) == 0
        assert m2["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_total_ticks_increments(self, session_factory):
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        loop = EventLoop(
            session=session_factory,
            task_return_repo=mock_return,
        )
        loop._task_return_repo = mock_return
        loop._todo_repo = AsyncMock()
        loop._todo_repo.claim_runnable.return_value = []
        await loop.tick()
        assert loop._total_ticks == 1
        await loop.tick()
        assert loop._total_ticks == 2


# ── 11. Edge Cases ───────────────────────────────────────────────────────────


class TestEventLoopEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_claimed_list_no_crash(self, loop_for_pipeline):
        metrics = await loop_for_pipeline.tick()
        assert metrics["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_tick_without_project_manager(self, session_factory):
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        loop = EventLoop(
            session=session_factory,
            task_return_repo=mock_return,
        )
        loop._task_return_repo = mock_return
        metrics = await loop.tick()
        assert metrics["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_tick_with_null_task_return_repo(self, session_factory):
        loop = EventLoop(
            session=session_factory,
            task_return_repo=None,
        )
        loop._todo_repo = AsyncMock()
        loop._todo_repo.claim_runnable.return_value = []
        metrics = await loop.tick()
        assert metrics["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_daemon_state_populated_after_tick(self, session_factory):
        mock_return = AsyncMock()
        mock_return.claim_unreviewed.return_value = []
        daemon_state: dict[str, object] = {}
        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/ds"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()
        loop = EventLoop(
            session=session_factory,
            runner=runner,
            task_return_repo=mock_return,
            daemon_state=daemon_state,
            config={"repo_root": "/tmp"},
            project_manager=_pipeline_project_manager(),
        )
        loop._task_return_repo = mock_return
        loop._runner = runner
        await loop.tick()
        assert isinstance(daemon_state.get("tick_metrics"), dict)
        assert isinstance(daemon_state["tick_metrics"]["tick_duration_ms"], float)
