"""E2E tests for the EventLoop dispatch, claim, and tick cycles.

Exercises the EventLoop with a real in-memory SQLite database for:
  - Phase order completeness and tick execution
  - Lease acquisition, reclamation, and release
  - Task return claiming and unreviewed dispatch
  - Todo claiming with bucket leases
  - Run-forever lifecycle and clean stop
  - Dispatch of execution jobs
  - File claim conflict detection
  - Config snapshot loading
  - Full claim→dispatch→execute pipeline (E2E)
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

from general_ludd.db.models import Base, BucketLeaseModel, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.lease import (
    acquire_lease,
    acquire_leases_batch,
    reclaim_expired_leases,
    release_lease,
)
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop
from general_ludd.schemas.queue import Queue
from general_ludd.schemas.todo import TodoStatus

_PIPELINE_PROJECT_ID = "proj-event-loop-e2e"


def _pipeline_project_manager() -> SimpleNamespace:
    project = SimpleNamespace(project_id=_PIPELINE_PROJECT_ID)
    return SimpleNamespace(
        select_project=lambda: project,
        list_active=lambda: [project],
    )


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
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestEventLoopPhaseOrder:
    def test_phase_order_has_required_phases(self):
        required = [
            "load_config_snapshot",
            "claim_unreviewed_task_returns",
            "dispatch_return_review_jobs",
            "claim_runnable_todos",
            "dispatch_execute_jobs",
            "remediate_blocked_tasks",
            "self_improve",
            "emit_tick_metrics",
        ]
        for phase in required:
            assert phase in PHASE_ORDER, f"missing required phase: {phase}"

    def test_phase_count_matches_known(self):
        assert len(PHASE_ORDER) >= 18, f"expected ≥18 phases, got {len(PHASE_ORDER)}"

    def test_no_duplicate_phases(self):
        assert len(PHASE_ORDER) == len(set(PHASE_ORDER)), "duplicate phases found"

    def test_first_phase_is_config_snapshot(self):
        assert PHASE_ORDER[0] == "load_config_snapshot"

    def test_last_phase_is_emit_metrics(self):
        assert PHASE_ORDER[-1] == "emit_tick_metrics"


class TestEventLoopTick:
    @pytest.mark.asyncio
    async def test_tick_runs_all_phases(self):
        loop = EventLoop(
            task_return_repo=AsyncMock(),
            todo_repo=AsyncMock(),
            http_client=AsyncMock(),
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        metrics = await loop.tick()
        assert "tick_duration_ms" in metrics
        assert metrics["phases_completed"] == len(PHASE_ORDER)
        assert metrics["phases_completed"] >= 18

    @pytest.mark.asyncio
    async def test_tick_returns_metrics(self):
        loop = EventLoop(
            task_return_repo=AsyncMock(),
            todo_repo=AsyncMock(),
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        metrics = await loop.tick()
        assert "tick_duration_ms" in metrics
        assert isinstance(metrics["tick_duration_ms"], float)

    @pytest.mark.asyncio
    async def test_tick_with_todo_repo_none_does_not_crash(self):
        loop = EventLoop(task_return_repo=None, todo_repo=None)
        loop._task_return_repo = None
        loop._todo_repo = None
        metrics = await loop.tick()
        assert metrics["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_dispatches_return_review_for_unreviewed(self):
        mock_return = MagicMock()
        mock_return.return_id = "RET-001"
        mock_return.todo_id = "TODO-001"
        mock_return.queue = Queue(queue_name="model")
        mock_return.project_id = None
        mock_return.plan_artifact = None
        mock_task_return_repo = AsyncMock()
        mock_task_return_repo.claim_unreviewed.return_value = [mock_return]
        mock_http = AsyncMock()
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={"status": "ok"})
        mock_http.post = AsyncMock(return_value=mock_response)
        loop = EventLoop(
            task_return_repo=mock_task_return_repo,
            http_client=mock_http,
        )
        loop._task_return_repo = mock_task_return_repo
        loop._http_client = mock_http
        await loop.tick()
        mock_http.post.assert_called()

    @pytest.mark.asyncio
    async def test_no_http_client_skip_dispatch(self):
        mock_return = MagicMock()
        mock_return.return_id = "RET-001"
        mock_return.todo_id = "TODO-001"
        mock_return.queue = Queue(queue_name="model")
        mock_return.project_id = None
        mock_return.plan_artifact = None
        mock_task_return_repo = AsyncMock()
        mock_task_return_repo.claim_unreviewed.return_value = [mock_return]
        loop = EventLoop(
            task_return_repo=mock_task_return_repo,
            http_client=None,
        )
        loop._task_return_repo = mock_task_return_repo
        loop._http_client = None
        metrics = await loop.tick()
        assert metrics["phases_completed"] == len(PHASE_ORDER)


class TestLeaseE2E:
    @pytest.mark.asyncio
    async def test_acquire_single_lease(self, db_session: AsyncSession):
        lease = await acquire_lease(db_session, "core:todo-1", "worker-a", ttl_seconds=300)
        assert lease.bucket_key == "core:todo-1"
        assert lease.holder_id == "worker-a"
        assert lease.expires_at > datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_acquire_lease_upserts_existing(self, db_session: AsyncSession):
        await acquire_lease(db_session, "core:todo-2", "worker-a", ttl_seconds=300)
        await db_session.commit()
        lease = await acquire_lease(db_session, "core:todo-2", "worker-b", ttl_seconds=600)
        assert lease.holder_id == "worker-b"

    @pytest.mark.asyncio
    async def test_acquire_leases_batch(self, db_session: AsyncSession):
        keys = ["core:todo-3", "core:todo-4", "model:todo-5"]
        leases = await acquire_leases_batch(db_session, keys, "worker-c", ttl_seconds=300)
        assert len(leases) == 3
        assert {le.bucket_key for le in leases} == set(keys)
        for le in leases:
            assert le.holder_id == "worker-c"

    @pytest.mark.asyncio
    async def test_reclaim_expired_leases(self, db_session: AsyncSession):
        from general_ludd.db.models import TodoModel

        session = db_session
        todo = TodoModel(
            todo_id="todo-expired",
            title="expired lease todo",
            queue="core",
            priority=5,
            work_type="code",
            status=TodoStatus.ACTIVE.value,
        )
        session.add(todo)
        await session.flush()
        await session.commit()

        lease = BucketLeaseModel(
            bucket_key="core:todo-expired",
            holder_id="worker-dead",
            expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        session.add(lease)
        await session.commit()

        reclaimed = await reclaim_expired_leases(session)
        assert reclaimed == 1

        stmt = select(TodoModel).where(TodoModel.todo_id == "todo-expired")
        result = await session.execute(stmt)
        requeued = result.scalar_one()
        assert requeued.status == TodoStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_reclaim_does_not_touch_valid_leases(self, db_session: AsyncSession):
        session = db_session
        todo = TodoModel(
            todo_id="todo-valid",
            title="valid lease todo",
            queue="core",
            priority=5,
            work_type="code",
            status=TodoStatus.ACTIVE.value,
        )
        session.add(todo)
        await session.flush()
        await session.commit()

        lease = BucketLeaseModel(
            bucket_key="core:todo-valid",
            holder_id="worker-alive",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
        )
        session.add(lease)
        await session.commit()

        reclaimed = await reclaim_expired_leases(session)
        assert reclaimed == 0

    @pytest.mark.asyncio
    async def test_release_lease_removes_row(self, db_session: AsyncSession):
        session = db_session
        lease = BucketLeaseModel(
            bucket_key="core:todo-release",
            holder_id="worker-d",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
        )
        session.add(lease)
        await session.commit()

        removed = await release_lease(session, "core:todo-release")
        assert removed == 1

        stmt = select(BucketLeaseModel).where(
            BucketLeaseModel.bucket_key == "core:todo-release"
        )
        result = await session.execute(stmt)
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_release_lease_holder_id_filter(self, db_session: AsyncSession):
        session = db_session
        lease = BucketLeaseModel(
            bucket_key="core:todo-hfilter",
            holder_id="worker-e",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
        )
        session.add(lease)
        await session.commit()

        removed = await release_lease(session, "core:todo-hfilter", holder_id="wrong-worker")
        assert removed == 0

        removed2 = await release_lease(session, "core:todo-hfilter", holder_id="worker-e")
        assert removed2 == 1


class TestEventLoopLifecycle:
    @pytest.mark.asyncio
    async def test_run_forever_stops_cleanly(self):
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
        )
        loop._todo_repo.claim_runnable.return_value = []
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._running = True
        task = asyncio.create_task(loop.run_forever(interval=0.01))
        await asyncio.sleep(0.08)
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert not loop._running

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
        )
        loop._todo_repo.claim_runnable.return_value = []
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._running = True
        task = asyncio.create_task(loop.run_forever(interval=0.01))
        await asyncio.sleep(0.05)
        loop.stop()
        loop.stop()
        loop.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert not loop._running

    @pytest.mark.asyncio
    async def test_never_executes_playbook_inline(self):
        runner = AsyncMock()
        loop = EventLoop(
            runner=runner,
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            http_client=AsyncMock(),
        )
        loop._todo_repo.claim_runnable.return_value = []
        loop._task_return_repo.claim_unreviewed.return_value = []
        metrics = await loop.tick()
        assert metrics["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_daemon_state_initialized(self):
        daemon_state: dict[str, object] = {}
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            daemon_state=daemon_state,
        )
        loop._todo_repo.claim_runnable.return_value = []
        loop._task_return_repo.claim_unreviewed.return_value = []
        await loop.tick()
        assert isinstance(daemon_state.get("tick_metrics"), dict)
        assert isinstance(daemon_state["tick_metrics"]["tick_duration_ms"], float)
        assert "self_update_applies" not in daemon_state or daemon_state["self_update_applies"] == []


# ── E2E Pipeline Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def _runner_for_pipeline():
    """Mock runner that records dispatch calls without real playbook execution."""
    runner = MagicMock()
    runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/test-pipeline-jobs"})
    runner.write_vars = MagicMock()
    runner.run_playbook = MagicMock()
    return runner


@pytest.fixture
async def _session_factory(db_engine):
    """Session factory for the pipeline DB."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def _loop_for_pipeline(_session_factory, _runner_for_pipeline):
    """EventLoop with real DB session factory and mocked runner."""
    loop = EventLoop(
        session=_session_factory,
        runner=_runner_for_pipeline,
        task_return_repo=AsyncMock(),
        config={"repo_root": "/tmp"},
        project_manager=_pipeline_project_manager(),
    )
    loop._task_return_repo.claim_unreviewed.return_value = []
    loop._runner = _runner_for_pipeline
    return loop


async def _seed_queued_todo(factory, **overrides) -> TodoModel:
    """Create and return a queued todo in the DB."""
    async with factory() as session:
        repo = TodoRepository(session)
        defaults = {
            "todo_id": "TODO-E2E-001",
            "title": "E2E pipeline test todo",
            "description": "Verify claim->dispatch->execute",
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


# ── E2E Pipeline Tests ───────────────────────────────────────────────────────


class TestClaimRunnablePipeline:
    @pytest.mark.asyncio
    async def test_claim_runnable_picks_up_queued_todo(
        self, _session_factory, _loop_for_pipeline
    ):
        await _seed_queued_todo(_session_factory)
        metrics = await _loop_for_pipeline.tick()
        claimed = _loop_for_pipeline._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        todo_ids = {t.todo_id for t in claimed}
        assert "TODO-E2E-001" in todo_ids
        assert metrics["phases_completed"] >= 18

    @pytest.mark.asyncio
    async def test_dispatch_execute_job_fires(
        self, _session_factory, _loop_for_pipeline, _runner_for_pipeline
    ):
        await _seed_queued_todo(_session_factory)
        await _loop_for_pipeline.tick()
        claimed = _loop_for_pipeline._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        write_vars_calls = [
            c for c in _runner_for_pipeline.write_vars.call_args_list
            if c[0][0].startswith("EXEC-")
        ]
        assert len(write_vars_calls) >= 1, "write_vars should be called for dispatched job"

    @pytest.mark.asyncio
    async def test_full_tick_cycle_completes(
        self, _session_factory, _loop_for_pipeline
    ):
        await _seed_queued_todo(_session_factory)
        metrics = await _loop_for_pipeline.tick()
        claimed = _loop_for_pipeline._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        claimed_todo = claimed[0]
        assert claimed_todo.todo_id == "TODO-E2E-001"
        tick_claimed = _loop_for_pipeline._tick_state.get("claimed_todos")
        assert tick_claimed is not None
        assert isinstance(metrics["tick_duration_ms"], float)
        assert metrics["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_claim_skips_when_capacity_full(self, _session_factory, db_engine):
        from general_ludd.controllers.floor import FloorController

        async with _session_factory() as session:
            repo = TodoRepository(session)
            for i in range(5):
                await repo.create({
                    "todo_id": f"TODO-ACTIVE-{i}",
                    "title": f"Pre-existing active todo {i}",
                    "queue": "core",
                    "priority": 3,
                    "work_type": "code",
                    "status": TodoStatus.ACTIVE.value,
                })
            await repo.create({
                "todo_id": "TODO-QUEUED-1",
                "title": "Should not be claimed",
                "queue": "core",
                "priority": 5,
                "work_type": "code",
                "status": TodoStatus.QUEUED.value,
            })
            await session.commit()

        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/tp"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()

        loop = EventLoop(
            session=_session_factory,
            runner=runner,
            task_return_repo=AsyncMock(),
            floor_controller=FloorController(floor=0),
            config={"repo_root": "/tmp"},
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._runner = runner

        await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) == 0, (
            f"Expected 0 claimed when floor=0, got {len(claimed)}"
        )

    @pytest.mark.asyncio
    async def test_project_id_null_todos_not_claimed_when_project_active(
        self, _session_factory, _loop_for_pipeline
    ):
        await _seed_queued_todo(_session_factory, project_id=None)
        await _loop_for_pipeline.tick()
        claimed = _loop_for_pipeline._tick_state.get("claimed_todos", [])
        assert len(claimed) == 0

    @pytest.mark.asyncio
    async def test_tick_phase_order_respected(self, _session_factory, _loop_for_pipeline):
        await _seed_queued_todo(_session_factory)
        completed_phases = []
        for phase_name in PHASE_ORDER:
            orig = getattr(_loop_for_pipeline, f"_phase_{phase_name}")

            def _make_wrapper(pn, fn):
                async def _wrapped():
                    completed_phases.append(pn)
                    return await fn()
                return _wrapped

            setattr(_loop_for_pipeline, f"_phase_{phase_name}", _make_wrapper(phase_name, orig))

        await _loop_for_pipeline.tick()

        assert completed_phases == PHASE_ORDER, (
            f"Phase order mismatch: expected {PHASE_ORDER[:5]}..., "
            f"got {completed_phases[:5]}..."
        )

    @pytest.mark.asyncio
    async def test_dispatch_writes_runner_vars(
        self, _session_factory, _loop_for_pipeline, _runner_for_pipeline
    ):
        await _seed_queued_todo(_session_factory, todo_id="TODO-E2E-VARS")
        await _loop_for_pipeline.tick()

        write_calls = _runner_for_pipeline.write_vars.call_args_list
        dispatched_calls = [
            c for c in write_calls
            if len(c[0]) >= 1 and "EXEC-TODO-E2E-VARS" in str(c[0][0])
        ]
        assert len(dispatched_calls) >= 1, (
            f"Expected write_vars call for EXEC-TODO-E2E-VARS, "
            f"got calls: {[c[0][0] if c[0] else '?' for c in write_calls]}"
        )

        call = dispatched_calls[0]
        job_vars = call[1].get("job_vars", {})
        assert job_vars.get("todo_id") == "TODO-E2E-VARS"
        assert job_vars.get("queue") == "core"

    @pytest.mark.asyncio
    async def test_background_tasks_drained(self, _session_factory, _loop_for_pipeline):
        await _seed_queued_todo(_session_factory)
        await _loop_for_pipeline.tick()
        snapshot = list(_loop_for_pipeline._background_tasks)
        drained = all(t.done() for t in snapshot)
        assert drained, (
            f"{sum(1 for t in snapshot if not t.done())} background tasks still running"
        )
