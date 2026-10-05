"""End-to-end tests for the EventLoop phase order and tick behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop


class TestEventLoopE2E:
    def test_phase_order_completeness(self):
        expected = [
            "load_config_snapshot",
            "evaluate_pid_controllers",
            "refill_task_buckets",
            "run_scheduler",
            "self_improve",
            "poll_issue_sources",
            "sdlc_gate",
            "reconcile_compute_demand",
            "claim_unreviewed_task_returns",
            "dispatch_return_review_jobs",
            "claim_runnable_todos",
            "evaluate_rules",
            "dispatch_execute_jobs",
            "reconcile_completed_decisions",
            "refresh_model_performance",
            "check_compute_utilization",
            "check_service_credits",
            "flush_spend_ledger",
            "remediate_blocked_tasks",
            "consolidate_memory",
            "service_discovery",
            "reap_expired_sts_tokens",
            "purge_old_task_decisions",
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
        mock_return = AsyncMock()
        mock_return.return_id = "RET-001"
        mock_return.todo_id = "TODO-001"
        mock_return.queue = "model"
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

    async def test_reclaims_expired_lease(self):
        """H15 (W2.5): expired bucket leases are purged; fresh ones survive."""
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import BucketLeaseModel, ProjectModel, TodoModel
        from general_ludd.db.repository import ProjectRepository
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )
        from general_ludd.event_loop.lease import (
            acquire_lease,
            reclaim_expired_leases,
        )
        from general_ludd.projects.manager import persist_project
        from general_ludd.schemas.project_identity import ProjectWorkIdentity
        from general_ludd.schemas.todo import TodoStatus

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            async with factory() as session:
                project_id = "proj-reclaim-e2e"
                todo_id = "TODO-RECLAIM-E2E"
                lease_key = ProjectWorkIdentity(
                    project_id,
                    todo_id,
                    "core",
                ).lease_bucket_key
                await persist_project(
                    ProjectRepository(session),
                    project_id=project_id,
                    name="Lease reclaim E2E",
                    weight=100.0,
                    dispatch_mode="active",
                )
                session.add(
                    TodoModel(
                        todo_id=todo_id,
                        title="already completed owner",
                        queue="core",
                        priority=5,
                        work_type="maintenance",
                        status=TodoStatus.COMPLETE.value,
                        project_id=project_id,
                    )
                )
                session.add(
                    BucketLeaseModel(
                        bucket_key=lease_key,
                        holder_id="worker-old",
                        expires_at=datetime.now(UTC) - timedelta(seconds=10),
                        project_id=project_id,
                        todo_version=1,
                    )
                )
                await session.commit()
                assert await session.get(ProjectModel, project_id) is not None

                reclaimed = await reclaim_expired_leases(session)
                assert isinstance(reclaimed, int)
                assert reclaimed == 1
                await acquire_lease(
                    session,
                    lease_key,
                    "worker-new",
                    ttl_seconds=300,
                    project_id=project_id,
                    todo_version=1,
                )

                remaining = (
                    (await session.execute(select(BucketLeaseModel))).scalars().all()
                )
                holders = {r.holder_id for r in remaining}
                assert holders == {"worker-new"}
        finally:
            await engine.dispose()

    async def test_run_forever_stops_cleanly(self):
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
        )
        loop._todo_repo.claim_runnable.return_value = []
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._running = True
        import asyncio
        task = asyncio.create_task(loop.run_forever(interval=0.01))
        await asyncio.sleep(0.05)
        loop.stop()
        await asyncio.wait_for(task, timeout=1.0)
        assert not loop._running

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
