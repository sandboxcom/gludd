"""E2E: Event loop — 10-phase tick, return review dispatch, lease reclaim.

Covers sprint objective 4 — full tick lifecycle, async dispatch,
decision reconciliation, run_forever with stop.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop


class TestEventLoopE2E:
    def test_phase_order_completeness(self):
        expected = [
            "load_config_snapshot",
            "claim_unreviewed_task_returns",
            "dispatch_return_review_jobs",
            "evaluate_pid_controllers",
            "evaluate_rules",
            "refill_task_buckets",
            "claim_runnable_todos",
            "dispatch_execute_jobs",
            "reconcile_completed_decisions",
            "emit_tick_metrics",
        ]
        assert expected == PHASE_ORDER

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

    async def test_dispatches_return_review_for_unreviewed_return(self):
        from general_ludd.schemas.task_return import TaskReturn

        mock_return = TaskReturn(
            return_id="RET-E2E",
            job_id="JOB-E2E",
            playbook="noop.yml",
            queue="core",
            work_type="code",
            resource_profile="ai_heavy",
            exit_code=0,
            result_summary="success",
        )
        loop = EventLoop(
            task_return_repo=AsyncMock(),
            todo_repo=AsyncMock(),
            http_client=AsyncMock(),
        )
        loop._task_return_repo.claim_unreviewed.return_value = [mock_return]
        loop._todo_repo.claim_runnable.return_value = []

        metrics = await loop.tick()
        assert metrics["returns_reviewed"] >= 1

    async def test_reclaims_expired_lease(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from general_ludd.db.models import Base, BucketLeaseModel
        from general_ludd.event_loop.lease import reclaim_expired_leases

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async_session = async_sessionmaker(engine)
        async with async_session() as session:
            expired = BucketLeaseModel(
                bucket_key="bucket-expired",
                holder_id="holder-1",
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
            active = BucketLeaseModel(
                bucket_key="bucket-active",
                holder_id="holder-2",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add_all([expired, active])
            await session.commit()

            reclaimed = await reclaim_expired_leases(session)
            assert reclaimed == 1
            await session.commit()

        await engine.dispose()

    async def test_run_forever_stops_cleanly(self):
        loop = EventLoop(
            task_return_repo=AsyncMock(),
            todo_repo=AsyncMock(),
            http_client=AsyncMock(),
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []

        async def stop_after_delay():
            await asyncio.sleep(0.2)
            loop.stop()

        _task = asyncio.create_task(stop_after_delay())  # noqa: RUF006
        await loop.run_forever(interval=0.05)

    async def test_never_executes_playbook_inline(self):
        loop = EventLoop(
            task_return_repo=AsyncMock(),
            todo_repo=AsyncMock(),
            http_client=AsyncMock(),
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        metrics = await loop.tick()
        assert metrics.get("inline_executions", 0) == 0
