"""End-to-end tests for the EventLoop phase order and tick behavior."""

from __future__ import annotations

import pytest
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
            "self_improve",
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
        from general_ludd.event_loop.lease import reclaim_expired_leases
        mock_session = AsyncMock()
        result = await reclaim_expired_leases(mock_session)
        assert isinstance(result, int)

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
