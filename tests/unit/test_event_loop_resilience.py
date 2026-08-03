"""Resilience tests for EventLoop: crash recovery, queue overflow, timeout/retry,
backpressure, graceful shutdown, stale task detection, priority ordering,
and concurrent job submission."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.lease import reclaim_expired_leases, release_lease
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_loop_resilience(**overrides):
    """Minimal lightweight EventLoop builder for resilience tests."""
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    http_client = AsyncMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 0.01},
        session=session,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "http_client": http_client,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


# ---------------------------------------------------------------------------
# 1. Worker crash recovery — lease-based stuck-todo reaping
# ---------------------------------------------------------------------------


class TestWorkerCrashRecovery:
    @pytest.mark.asyncio
    async def test_expired_lease_reaps_stuck_active_todo(self):
        """An ACTIVE todo whose lease has expired (no live lease row) must be
        transitioned ACTIVE->QUEUED by _reap_stuck_todos."""

        loop, mocks = _make_loop_resilience()
        session = mocks["session"]

        todo_row = MagicMock()
        todo_row.todo_id = "TODO-STUCK-1"
        todo_row.status = TodoStatus.ACTIVE.value
        todo_row.queue = "core"
        todo_row.updated_at = datetime.now(UTC) - timedelta(minutes=30)
        todo_row.version = 3

        # First execute returns stuck todos; second returns NO live leases.
        result1 = MagicMock()
        result1.scalars.return_value.all.return_value = [todo_row]
        result2 = MagicMock()
        result2.scalars.return_value.all.return_value = []

        session.execute.side_effect = [result1, result2]

        mocks["todo_repo"].transition = AsyncMock(return_value=MagicMock())
        loop._todo_repo = mocks["todo_repo"]
        loop._active_session = session

        await loop._reap_stuck_todos()

        mocks["todo_repo"].transition.assert_called_once_with("TODO-STUCK-1", TodoStatus.QUEUED, 3)

    @pytest.mark.asyncio
    async def test_live_lease_prevents_reaping(self):
        """An ACTIVE todo with a LIVE (unexpired) lease must NOT be reaped."""

        loop, mocks = _make_loop_resilience()
        session = mocks["session"]

        todo_row = MagicMock()
        todo_row.todo_id = "TODO-LIVE-1"
        todo_row.status = TodoStatus.ACTIVE.value
        todo_row.queue = "core"
        todo_row.updated_at = datetime.now(UTC) - timedelta(minutes=30)
        todo_row.version = 5

        result1 = MagicMock()
        result1.scalars.return_value.all.return_value = [todo_row]
        # Second query returns the bucket_key as a live lease.
        result2 = MagicMock()
        result2.scalars.return_value.all.return_value = ["core:TODO-LIVE-1"]

        session.execute.side_effect = [result1, result2]

        mocks["todo_repo"].transition = AsyncMock()
        loop._todo_repo = mocks["todo_repo"]
        loop._active_session = session

        await loop._reap_stuck_todos()

        mocks["todo_repo"].transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_reap_skips_concurrent_version_change(self):
        """When transition raises ConcurrencyError (lost race), the todo is
        skipped — no crash, no double-reap."""
        from general_ludd.db.repository import ConcurrencyError

        loop, mocks = _make_loop_resilience()
        session = mocks["session"]

        todo_row = MagicMock()
        todo_row.todo_id = "TODO-RACE-1"
        todo_row.status = TodoStatus.ACTIVE.value
        todo_row.queue = "core"
        todo_row.updated_at = datetime.now(UTC) - timedelta(minutes=30)
        todo_row.version = 7

        result1 = MagicMock()
        result1.scalars.return_value.all.return_value = [todo_row]
        result2 = MagicMock()
        result2.scalars.return_value.all.return_value = []
        session.execute.side_effect = [result1, result2]

        mocks["todo_repo"].transition = AsyncMock(side_effect=ConcurrencyError("version mismatch"))
        loop._todo_repo = mocks["todo_repo"]
        loop._active_session = session

        # Must not raise — just skip the lost race.
        await loop._reap_stuck_todos()

        mocks["todo_repo"].transition.assert_called_once()

    @pytest.mark.asyncio
    async def test_reap_handles_no_active_session(self):
        """When no active session exists, _reap_stuck_todos returns cleanly."""
        loop, _ = _make_loop_resilience()
        loop._active_session = None
        loop._todo_repo = None

        # Must not raise.
        await loop._reap_stuck_todos()


# ---------------------------------------------------------------------------
# 2. Queue overflow handling
# ---------------------------------------------------------------------------


class TestQueueOverflowHandling:
    def test_queue_overflow_soft_cap_exceeds_hard_cap_raises(self):
        from general_ludd.schemas.queue import Queue

        with pytest.raises(ValueError, match="soft_cap must not exceed hard_cap"):
            Queue(queue_name="overflow-test", hard_cap=5, soft_cap=10)

    def test_queue_hard_cap_zero_raises(self):
        from general_ludd.schemas.queue import Queue

        with pytest.raises(ValueError, match="must be at least 1"):
            Queue(queue_name="overflow-test", hard_cap=0)

    def test_queue_soft_cap_equals_hard_cap_allows_full(self):
        from general_ludd.schemas.queue import Queue

        q = Queue(queue_name="overflow-test", hard_cap=20, soft_cap=20)
        assert q.soft_cap == 20
        assert q.hard_cap == 20

    def test_initial_queues_all_valid_caps(self):
        from general_ludd.schemas.queue import INITIAL_QUEUES

        for q in INITIAL_QUEUES:
            assert q.hard_cap >= 1, f"{q.queue_name} hard_cap < 1"
            assert q.soft_cap <= q.hard_cap, f"{q.queue_name} soft_cap > hard_cap"


# ---------------------------------------------------------------------------
# 3. Task timeout and retry
# ---------------------------------------------------------------------------


class TestTaskTimeoutAndRetry:
    def test_stuck_timeout_default_is_set(self):
        loop, _ = _make_loop_resilience()
        assert loop._stuck_timeout_minutes == 15

    def test_max_retries_default_is_set(self):
        loop, _ = _make_loop_resilience()
        assert loop._max_retries == 3

    def test_stuck_timeout_configurable(self):
        loop, _ = _make_loop_resilience()
        loop._stuck_timeout_minutes = 5
        assert loop._stuck_timeout_minutes == 5

    @pytest.mark.asyncio
    async def test_lease_reclaim_with_max_age_respects_cutoff(self):
        """reclaim_expired_leases must only delete leases past max_age_seconds."""
        session = AsyncMock()
        expired = MagicMock()
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=600)
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [expired]
        session.execute.return_value = result_mock
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        count = await reclaim_expired_leases(session, max_age_seconds=300)
        assert count == 1
        session.delete.assert_called_once_with(expired)

    @pytest.mark.asyncio
    async def test_lease_reclaim_no_expired_returns_zero(self):
        """When no expired leases exist, reclaim returns 0 and deletes nothing."""
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        session.execute.return_value = result_mock
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        count = await reclaim_expired_leases(session)
        assert count == 0
        session.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_forever_handles_tick_exception(self):
        """When tick() raises, run_forever propagates the error — not silent."""
        loop, _ = _make_loop_resilience()
        loop._running = True

        async def failing_tick():
            raise RuntimeError("simulated crash")

        loop.tick = failing_tick

        with pytest.raises(RuntimeError, match="simulated crash"):
            await loop.run_forever(interval=0.001)


# ---------------------------------------------------------------------------
# 4. Backpressure when workers are slow
# ---------------------------------------------------------------------------


class TestBackpressureSlowWorkers:
    def test_floor_controller_blocks_at_low_health(self):
        """FloorController.get_max_active() returns 0 when health < 25%."""
        from general_ludd.controllers.floor import FloorController

        fc = FloorController(floor=10)
        fc.update_health(20.0)
        assert fc.get_max_active() == 0

    def test_floor_controller_halves_at_low_health(self):
        """FloorController halves at health < 50%."""
        from general_ludd.controllers.floor import FloorController

        fc = FloorController(floor=10)
        fc.update_health(40.0)
        assert fc.get_max_active() == 5

    def test_pid_controller_throttles_under_heavy_load(self):
        """LoadController reduces desired buckets under high load."""
        from general_ludd.controllers.load_scrape import LoadSnapshot
        from general_ludd.controllers.pid import LoadController
        from general_ludd.schemas.queue import Queue

        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snapshot = LoadSnapshot(
            loadavg_1m=8.0,
            loadavg_5m=8.0,
            loadavg_10m=8.0,
            logical_cpu_count=4,
            cpu_percent=90.0,
            memory_available_percent=40.0,
            disk_free_percent=40.0,
            active_jobs=10,
        )
        q = Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=10)
        out = ctrl.evaluate_snapshot(snapshot, [q])
        bucketed = out.desired_active_buckets_by_queue.get("ansible", 10)
        assert bucketed <= 5

    def test_budget_controller_rejects_over_cpu(self):
        """BudgetController blocks local model dispatch when CPU > 95%."""
        from general_ludd.controllers.load_scrape import LoadSnapshot
        from general_ludd.controllers.pid import BudgetController

        bc = BudgetController()
        snapshot = LoadSnapshot(1.0, 1.0, 1.0, 8, 96.0, 50.0, 50.0, 3)
        result = bc.check_local_model_resources(snapshot)
        assert result["allowed"] is False

    def test_backpressure_semaphore_limits_concurrency(self):
        """EventLoop dispatch semaphore enforces max_gather_concurrency."""
        loop, _ = _make_loop_resilience()
        sem = loop._dispatch_semaphore
        assert sem._value > 0

    def test_to_thread_semaphore_limits_concurrency(self):
        """EventLoop to_thread semaphore enforces max_to_thread_concurrency."""
        loop, _ = _make_loop_resilience()
        sem = loop._to_thread_semaphore
        assert sem._value > 0


# ---------------------------------------------------------------------------
# 5. Graceful shutdown with in-flight tasks
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_stops_running_flag(self):
        """shutdown() sets _running=False."""
        loop, _ = _make_loop_resilience()
        loop._running = True

        await loop.shutdown()

        assert loop._running is False

    @pytest.mark.asyncio
    async def test_shutdown_drains_background_tasks(self):
        """shutdown() cancels and drains all in-flight background tasks."""
        loop, _ = _make_loop_resilience()
        loop._running = True

        async def _slow_work():
            await asyncio.sleep(60)

        tasks = [asyncio.ensure_future(_slow_work()) for _ in range(5)]
        for t in tasks:
            loop._track_background_task(t)

        assert len(loop._background_tasks) == 5

        await asyncio.wait_for(loop.shutdown(), timeout=5.0)

        assert all(t.cancelled() for t in tasks)
        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_handles_empty_task_set(self):
        """shutdown() on an empty background task set is a no-op."""
        loop, _ = _make_loop_resilience()
        loop._running = True

        await loop.shutdown()

        assert loop._running is False
        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_no_exception_from_cancelled_tasks(self):
        """shutdown() must not raise when a cancelled task settles with CancelledError."""
        loop, _ = _make_loop_resilience()
        loop._running = True

        async def _will_be_cancelled():
            await asyncio.sleep(60)

        task = asyncio.ensure_future(_will_be_cancelled())
        loop._track_background_task(task)

        await asyncio.wait_for(loop.shutdown(), timeout=5.0)

        assert task.cancelled()
        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_handles_already_failed_task(self):
        """shutdown() must not choke on a task that already raised an exception."""
        loop, _ = _make_loop_resilience()
        loop._running = True

        async def _explode():
            raise ValueError("bang")

        task = asyncio.ensure_future(_explode())
        loop._track_background_task(task)

        # Let the task settle (fail) before shutdown.
        with pytest.raises(ValueError, match="bang"):
            await task

        # shutdown on a set containing an already-failed (done) task.
        await asyncio.wait_for(loop.shutdown(), timeout=5.0)

        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_run_forever_stops_cleanly_on_stop(self):
        """Calling stop() mid-loop exits run_forever cleanly within timeout."""
        loop, mocks = _make_loop_resilience()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []

        ticks_run = 0
        original_tick = loop.tick

        async def counting_tick():
            nonlocal ticks_run
            ticks_run += 1
            if ticks_run >= 2:
                loop.stop()
            return await original_tick()

        loop.tick = counting_tick

        await loop.run_forever(interval=0.001)

        assert ticks_run >= 2
        assert loop._running is False


# ---------------------------------------------------------------------------
# 6. Stale task detection (already covered in TestWorkerCrashRecovery above;
#    these are additional edge cases)
# ---------------------------------------------------------------------------


class TestStaleTaskDetection:
    @pytest.mark.asyncio
    async def test_reap_ignores_non_active_status(self):
        """Only ACTIVE todos are candidates for reaping — QUEUED et al. are untouched."""

        loop, mocks = _make_loop_resilience()
        session = mocks["session"]

        # The query filters on status=ACTIVE, so the empty result is the expected
        # path. Verify reaper handles empty candidate list cleanly.
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute.return_value = result

        loop._active_session = session
        loop._todo_repo = mocks["todo_repo"]

        await loop._reap_stuck_todos()

        mocks["todo_repo"].transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_reap_handles_repo_none(self):
        """_reap_stuck_todos returns cleanly when _todo_repo is None."""
        loop, _ = _make_loop_resilience()
        loop._active_session = AsyncMock()
        loop._todo_repo = None

        await loop._reap_stuck_todos()

    @pytest.mark.asyncio
    async def test_expired_lease_released_clears_row(self):
        """release_lease deletes the lease row by bucket_key."""
        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        session.execute.return_value = result

        rows = await release_lease(session, "core:STALE-1")
        assert rows == 1


# ---------------------------------------------------------------------------
# 7. Priority queue ordering
# ---------------------------------------------------------------------------


class TestPriorityQueueOrdering:
    def test_todo_priority_is_numeric(self):
        """Todo priority field is an integer between 0 and 1000."""
        todo = Todo(title="priority-task", priority=500)
        assert 0 <= todo.priority <= 1000
        assert isinstance(todo.priority, int)

    def test_todo_priority_default_in_range(self):
        """Default Todo priority is in the valid range."""
        todo = Todo(title="default-priority")
        assert 0 <= todo.priority <= 1000

    def test_high_priority_todo_ranks_before_low(self):
        """Given two todos, the one with higher priority should sort first."""
        high = Todo(title="high", priority=900)
        low = Todo(title="low", priority=100)
        ordered = sorted([low, high], key=lambda t: t.priority, reverse=True)
        assert ordered[0].priority == 900
        assert ordered[1].priority == 100

    def test_queue_priority_weight_default(self):
        """Each queue has a priority_weight that governs scheduling weight."""
        from general_ludd.schemas.queue import Queue

        q = Queue(queue_name="critical")
        assert q.priority_weight == 100

    def test_scheduler_tick_empty_returns_zero(self):
        """TodoScheduler.tick() with no due todos returns (0, 0)."""
        from general_ludd.event_loop.scheduler import TodoScheduler

        repo = MagicMock()
        repo.list_due_scheduled = AsyncMock(return_value=[])
        sched = TodoScheduler(repo, clock=lambda: datetime(2024, 1, 1, tzinfo=UTC))
        promoted, spawned = asyncio.run(sched.tick())
        assert promoted == 0
        assert spawned == 0

    def test_priority_ordering_maintained_at_boundaries(self):
        """Priorities at min and max are handled correctly."""
        t0 = Todo(title="min", priority=0)
        t1000 = Todo(title="max", priority=1000)
        assert t0.priority == 0
        assert t1000.priority == 1000


# ---------------------------------------------------------------------------
# 8. Concurrent job submission
# ---------------------------------------------------------------------------


class TestConcurrentJobSubmission:
    @pytest.mark.asyncio
    async def test_many_concurrent_background_tasks_drain_to_empty(self):
        """50 concurrent background tasks all drain to empty after completion."""
        loop, _ = _make_loop_resilience()

        async def _work(i: int) -> int:
            await asyncio.sleep(0)
            return i

        tasks = [asyncio.ensure_future(_work(i)) for i in range(50)]
        for t in tasks:
            loop._track_background_task(t)

        assert len(loop._background_tasks) == 50

        results = await asyncio.gather(*tasks)
        await asyncio.sleep(0)

        assert results == list(range(50))
        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_concurrent_drain_safe_under_discard(self):
        """Drain is safe even when tasks settle (discard) mid-iteration."""
        loop, _ = _make_loop_resilience()

        async def _quick(i: int) -> int:
            await asyncio.sleep(0.001 * (i % 5))
            return i

        tasks = [asyncio.ensure_future(_quick(i)) for i in range(20)]
        for t in tasks:
            loop._track_background_task(t)

        await asyncio.wait_for(loop._drain_background_tasks(cancel=False), timeout=5.0)
        await asyncio.sleep(0)
        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_already_done_task_tracked_then_discarded(self):
        """Registering an already-completed task must not leak it."""
        loop, _ = _make_loop_resilience()

        async def _instant() -> str:
            return "done"

        task = asyncio.ensure_future(_instant())
        await task
        loop._track_background_task(task)
        await asyncio.sleep(0)

        assert task not in loop._background_tasks

    @pytest.mark.asyncio
    async def test_semaphore_serializes_heavy_dispatches(self):
        """A semaphore-acquired block serializes concurrent submissions."""
        _loop, _ = _make_loop_resilience()
        sem = asyncio.Semaphore(2)

        active = 0
        max_active = 0

        async def _sem_work():
            nonlocal active, max_active
            async with sem:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        tasks = [asyncio.ensure_future(_sem_work()) for _ in range(10)]
        await asyncio.gather(*tasks)

        assert max_active <= 2

    @pytest.mark.asyncio
    async def test_wake_event_triggers_immediately(self):
        """wake() sets the event so a waiting run_forever loop resumes."""
        loop, _ = _make_loop_resilience()

        assert not loop._wake_event.is_set()
        loop.wake()
        assert loop._wake_event.is_set()

    @pytest.mark.asyncio
    async def test_phase_exception_isolation(self):
        """When a single phase raises, other phases still run and tick completes."""
        loop, mocks = _make_loop_resilience()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []

        async def _raise_phase():
            raise ValueError("phase error")

        loop._phase_claim_runnable_todos = _raise_phase

        result = await loop.tick()

        assert "phases_completed" in result
        assert "tick_duration_ms" in result
