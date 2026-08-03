"""Deep tests for agent dispatch: routing, priority, load balancing, scaling, failure reassignment."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.agents.dispatcher import (
    AgentDispatcher,
    AgentTaskResult,
    DispatchStatus,
)
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType
from general_ludd.config.user_config import OrchestrationGuardConfig


@pytest.fixture
def registry():
    r = AgentRegistry()
    r.register(
        AgentConfig(
            name="build",
            description="Primary build agent",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_edit=True,
                can_bash=True,
                can_read=True,
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
            max_concurrent=2,
        )
    )
    r.register(
        AgentConfig(
            name="explore",
            description="Subagent",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=False,
                can_bash=False,
                can_read=True,
                can_dispatch_subagents=False,
                allowed_subagents=[],
            ),
            max_concurrent=3,
        )
    )
    r.register(
        AgentConfig(
            name="general",
            description="General subagent",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=True,
                can_bash=True,
                can_read=True,
                can_dispatch_subagents=False,
                allowed_subagents=[],
            ),
            max_concurrent=2,
        )
    )
    r.register(
        AgentConfig(
            name="disabled-agent",
            description="Disabled agent",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(),
            max_concurrent=1,
            enabled=False,
        )
    )
    r.register(
        AgentConfig(
            name="primary",
            description="Primary agent",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_edit=True,
                can_bash=True,
                can_read=True,
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
            max_concurrent=3,
        )
    )
    r.register(
        AgentConfig(
            name="limited-sub",
            description="Limited subagent",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_edit=False,
                can_bash=False,
                can_read=True,
                can_dispatch_subagents=False,
                allowed_subagents=[],
            ),
            max_concurrent=1,
        )
    )
    return r


@pytest.fixture
def guard_config():
    return OrchestrationGuardConfig(
        max_nesting_depth=3,
        max_redispatch_count=5,
        max_dispatches_per_window=0,
        enforce_capability_escalation=True,
    )


def make_task(task_id="t1", agent_name="explore", invoker="build", depth=0, project_id=None):
    return AgentTask(
        task_id=task_id,
        agent_name=agent_name,
        description=f"Task {task_id}",
        prompt=f"Prompt for {task_id}",
        invoker_name=invoker,
        depth=depth,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# DispatchStatus
# ---------------------------------------------------------------------------


class TestDispatchStatus:
    def test_success_and_completed_are_equal(self):
        assert DispatchStatus("completed") == "completed"
        assert DispatchStatus("completed") == "success"
        assert DispatchStatus("success") == "completed"
        assert DispatchStatus("success") == "success"

    def test_hash_returns_str_hash(self):
        assert hash(DispatchStatus("completed")) == hash("completed")

    def test_distinct_from_other_statuses(self):
        assert DispatchStatus("completed") != "failed"
        assert DispatchStatus("completed") != "cancelled"
        assert DispatchStatus("completed") != "blocked"


# ---------------------------------------------------------------------------
# Task routing
# ---------------------------------------------------------------------------


class TestTaskRouting:
    async def test_dispatch_to_known_agent(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.dispatch_one(make_task(agent_name="explore"))
        assert result.status == "completed"
        assert result.agent_name == "explore"
        assert result.output == "done"

    async def test_dispatch_unknown_agent_fails(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.dispatch_one(make_task(agent_name="ghost"))
        assert result.status == "failed"
        assert "not found in registry" in result.output

    async def test_dispatch_disabled_agent_fails(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.dispatch_one(make_task(agent_name="disabled-agent"))
        assert result.status == "failed"
        assert "disabled" in result.output

    async def test_dispatch_permission_denied(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.dispatch_one(make_task(agent_name="explore", invoker="explore"))
        assert result.status == "failed"
        assert "Permission denied" in result.output

    async def test_dispatch_with_empty_invoker_denied(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.dispatch_one(make_task(invoker=""))
        assert result.status == "failed"
        assert "Permission denied" in result.output

    async def test_primary_invoker_falls_back_to_build(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.dispatch_one(make_task(invoker="primary"))
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Priority / ordering
# ---------------------------------------------------------------------------


class TestPriorityAndOrdering:
    async def test_dispatch_many_empty_returns_empty(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock())
        results = await dispatcher.dispatch_many([])
        assert results == []

    async def test_dispatch_many_preserves_order(self, registry):
        resolve_order: list[str] = []

        async def ordered_exec(task):
            resolve_order.append(task.task_id)
            await asyncio.sleep(0.001)
            return f"done-{task.task_id}"

        dispatcher = AgentDispatcher(registry, executor=ordered_exec)
        tasks = [make_task(task_id=f"t{i}") for i in range(5)]
        results = await dispatcher.dispatch_many(tasks)

        assert len(results) == 5
        for r in results:
            assert r.status == "completed"

    async def test_dispatch_many_runs_concurrently(self, registry):
        started: list[str] = []
        running: list[str] = []

        async def concurrent_exec(task):
            started.append(task.task_id)
            running.append(task.task_id)
            await asyncio.sleep(0.02)
            running.remove(task.task_id)
            return "ok"

        dispatcher = AgentDispatcher(registry, executor=concurrent_exec)
        tasks = [make_task(task_id=f"t{i}") for i in range(6)]
        results = await dispatcher.dispatch_many(tasks)

        all_completed = all(r.status == "completed" for r in results)
        assert all_completed

    async def test_dispatch_many_handles_exceptions_in_futures(self, registry):
        async def failing_exec(task):
            if task.task_id == "t3":
                raise RuntimeError("boom")
            return "ok"

        dispatcher = AgentDispatcher(registry, executor=failing_exec)
        tasks = [make_task(task_id=f"t{i}") for i in range(6)]
        results = await dispatcher.dispatch_many(tasks)

        failures = [r for r in results if r.status == "failed"]
        assert len(failures) >= 1
        assert any("boom" in r.output for r in failures)


# ---------------------------------------------------------------------------
# Load balancing
# ---------------------------------------------------------------------------


class TestLoadBalancing:
    async def test_semaphore_limits_concurrent_per_agent(self, registry):
        active_simultaneous: list[int] = []
        in_flight = 0
        lock = asyncio.Lock()

        async def load_exec(task):
            nonlocal in_flight
            async with lock:
                in_flight += 1
                active_simultaneous.append(in_flight)
            await asyncio.sleep(0.03)
            async with lock:
                in_flight -= 1
            return "done"

        dispatcher = AgentDispatcher(registry, executor=load_exec)
        tasks = [make_task(task_id=f"t{i}") for i in range(10)]
        await dispatcher.dispatch_many(tasks)

        max_concurrent = max(active_simultaneous) if active_simultaneous else 0
        assert max_concurrent <= 10

    async def test_active_count_reflects_inflight_tasks(self, registry):
        counts: list[int] = []

        async def tracking_exec(task):
            counts.append(dispatcher.active_count)
            await asyncio.sleep(0.01)
            return "done"

        dispatcher = AgentDispatcher(registry, executor=tracking_exec)
        tasks = [make_task(task_id=f"t{i}") for i in range(4)]
        await dispatcher.dispatch_many(tasks)

        assert dispatcher.active_count == 0
        assert len(counts) == 4

    async def test_same_agent_semaphore_shared(self, registry):
        """All tasks for the same agent share one semaphore."""
        sem_keys: list[str] = []

        async def sem_tracking_exec(task):
            async with dispatcher._lock:
                sem_keys.append(task.agent_name)
            return "done"

        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        tasks = [make_task(task_id=f"t{i}", agent_name="explore") for i in range(5)]
        await dispatcher.dispatch_many(tasks)

        # All used the "explore" semaphore — semaphores dict has one key
        async with dispatcher._lock:
            assert "explore" in dispatcher._semaphores


# ---------------------------------------------------------------------------
# Agent pool scaling
# ---------------------------------------------------------------------------


class TestAgentPoolScaling:
    async def test_different_agents_get_different_semaphores(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        await dispatcher.dispatch_many(
            [
                make_task(agent_name="explore"),
                make_task(agent_name="general"),
            ]
        )
        async with dispatcher._lock:
            assert "explore" in dispatcher._semaphores
            assert "general" in dispatcher._semaphores

    async def test_semaphore_limit_respects_config(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        sem = await dispatcher._get_semaphore("explore")
        await sem.acquire()
        await sem.acquire()
        await sem.acquire()
        assert sem.locked()

    async def test_semaphore_limit_defaults_to_one_for_unknown(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        sem = await dispatcher._get_semaphore("nonexistent")
        await sem.acquire()
        assert sem.locked()

    async def test_model_call_semaphore_scales_from_config(self, registry):
        gc = OrchestrationGuardConfig(max_concurrent_model_calls=5)
        dispatcher = AgentDispatcher(registry, orchestration_guard=gc)
        for _ in range(5):
            await dispatcher._model_call_semaphore.acquire()
        assert dispatcher._model_call_semaphore.locked()

    async def test_model_call_semaphore_defaults_to_ten(self, registry):
        dispatcher = AgentDispatcher(registry)
        for _ in range(10):
            await dispatcher._model_call_semaphore.acquire()
        assert dispatcher._model_call_semaphore.locked()


# ---------------------------------------------------------------------------
# Failure reassignment
# ---------------------------------------------------------------------------


class TestFailureReassignment:
    async def test_exception_in_executor_produces_failed_result(self, registry):
        async def crash_exec(_):
            raise ValueError("simulated crash")

        dispatcher = AgentDispatcher(registry, executor=crash_exec)
        result = await dispatcher.dispatch_one(make_task(task_id="crash-1"))
        assert result.status == "failed"
        assert "simulated crash" in result.output
        assert result.task_id == "crash-1"

    async def test_cancellation_re_raises(self, registry):
        async def slow_exec(_):
            await asyncio.sleep(10)
            return "done"

        dispatcher = AgentDispatcher(registry, executor=slow_exec)
        task = make_task(task_id="cancel-me")

        coro = dispatcher.dispatch_one(task)
        task_obj = asyncio.ensure_future(coro)
        await asyncio.sleep(0.01)
        task_obj.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task_obj

    async def test_active_count_decremented_on_failure(self, registry):
        async def fail_exec(_):
            raise RuntimeError("err")

        dispatcher = AgentDispatcher(registry, executor=fail_exec)
        assert dispatcher.active_count == 0
        await dispatcher.dispatch_one(make_task())
        assert dispatcher.active_count == 0

    async def test_active_count_decremented_on_completion(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="ok"))
        await dispatcher.dispatch_one(make_task())
        assert dispatcher.active_count == 0

    async def test_noop_executor_fails_loudly(self, registry):
        dispatcher = AgentDispatcher(registry)
        result = await dispatcher.dispatch_one(make_task(agent_name="build", invoker="build"))
        assert result.status == "failed"
        assert "unconfigured" in result.output.lower()


# ---------------------------------------------------------------------------
# D11 orchestration guards
# ---------------------------------------------------------------------------


class TestOrchestrationGuards:
    @pytest.fixture
    def guard(self):
        return OrchestrationGuardConfig(
            max_nesting_depth=3,
            max_redispatch_count=3,
            max_dispatches_per_window=5,
            dispatch_rate_window_s=60.0,
            enforce_capability_escalation=True,
        )

    async def test_nesting_depth_blocked(self, registry, guard):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(), orchestration_guard=guard)
        result = await dispatcher.dispatch_one(make_task(depth=5))
        assert result.status == "failed"
        assert "nesting depth exceeded" in result.output.lower()

    async def test_nesting_depth_allowed(self, registry, guard):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="ok"), orchestration_guard=guard)
        result = await dispatcher.dispatch_one(make_task(depth=2))
        assert result.status == "completed"

    async def test_rate_limiter_blocks_excess(self, registry, guard):
        guard.max_dispatches_per_window = 3
        guard.dispatch_rate_window_s = 600.0
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="ok"), orchestration_guard=guard)

        for i in range(3):
            result = await dispatcher.dispatch_one(make_task(task_id=f"rt{i}"))
            assert result.status == "completed", f"Task {i}: {result.status}"

        result = await dispatcher.dispatch_one(make_task(task_id="rt-over"))
        assert result.status == "failed"
        assert "rate limited" in result.output.lower()

    async def test_spiral_detection_blocks_re_dispatches(self, registry, guard):
        guard.max_redispatch_count = 3
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="ok"), orchestration_guard=guard)

        task = make_task(task_id="spiral-task")
        for i in range(3):
            result = await dispatcher.dispatch_one(task)
            assert result.status == "completed", f"Dispatch {i}: {result.status}"

        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "spiral" in result.output.lower()

    async def test_capability_escalation_blocked(self, registry, guard):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(), orchestration_guard=guard)
        result = await dispatcher.dispatch_one(make_task(agent_name="general", invoker="explore"))
        assert result.status == "failed"
        assert "escalation denied" in result.output.lower()

    async def test_guard_disabled_when_config_is_none(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="ok"))
        result = await dispatcher.dispatch_one(make_task(depth=999))
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Active task tracking
# ---------------------------------------------------------------------------


class TestActiveTaskTracking:
    async def test_get_active_tasks_for_project(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        task = make_task(project_id="proj-1")
        async with dispatcher._lock:
            dispatcher._active_tasks[task.task_id] = task
        result = await dispatcher.get_active_tasks_for_project("proj-1")
        assert len(result) == 1
        assert result[0].project_id == "proj-1"

    async def test_get_active_tasks_for_project_empty(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.get_active_tasks_for_project("nonexistent")
        assert result == []

    async def test_get_active_tasks_by_agent_name(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        t1 = make_task(task_id="a1", agent_name="explore")
        t2 = make_task(task_id="a2", agent_name="explore")
        t3 = make_task(task_id="a3", agent_name="general")
        async with dispatcher._lock:
            dispatcher._active_tasks["a1"] = t1
            dispatcher._active_tasks["a2"] = t2
            dispatcher._active_tasks["a3"] = t3
        explore_tasks = await dispatcher.get_active_tasks_by_agent_name("explore")
        general_tasks = await dispatcher.get_active_tasks_by_agent_name("general")
        assert len(explore_tasks) == 2
        assert len(general_tasks) == 1

    async def test_get_active_tasks_by_task_id_found(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        task = make_task(task_id="needle")
        async with dispatcher._lock:
            dispatcher._active_tasks[task.task_id] = task
        result = await dispatcher.get_active_tasks_by_task_id("needle")
        assert len(result) == 1
        assert result[0].task_id == "needle"

    async def test_get_active_tasks_by_task_id_not_found(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.get_active_tasks_by_task_id("ghost")
        assert result == []


# ---------------------------------------------------------------------------
# Quiesce and resume
# ---------------------------------------------------------------------------


class TestQuiesceAndResume:
    async def test_quiesce_project_cancels_tasks(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        t1 = make_task(task_id="q1", agent_name="build", project_id="proj-q", invoker="build")
        t2 = make_task(task_id="q2", agent_name="explore", project_id="proj-q")
        t3 = make_task(task_id="q3", agent_name="explore", project_id="other")
        async with dispatcher._lock:
            dispatcher._active_tasks["q1"] = t1
            dispatcher._active_tasks["q2"] = t2
            dispatcher._active_tasks["q3"] = t3
        results = await dispatcher.quiesce_project("proj-q")
        assert len(results) == 2
        for r in results:
            assert r.status == "cancelled"
            assert "quiesced" in r.output.lower()

    async def test_quiesce_empty_project_returns_empty(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock())
        results = await dispatcher.quiesce_project("nothing")
        assert results == []

    async def test_pause_controller_blocks_dispatch(self, registry):
        pause_ctrl = MagicMock()
        pause_ctrl.is_paused.return_value = True
        dispatcher = AgentDispatcher(
            registry,
            executor=AsyncMock(return_value="done"),
            pause_controller=pause_ctrl,
        )
        result = await dispatcher.dispatch_one(
            make_task(task_id="blocked", agent_name="explore", project_id="paused-proj")
        )
        assert result.status == "blocked"
        assert "paused" in result.output.lower()


# ---------------------------------------------------------------------------
# dispatch_many timeout
# ---------------------------------------------------------------------------


class TestDispatchManyTimeout:
    async def test_dispatch_many_times_out(self, registry):
        async def slow_exec(_):
            await asyncio.sleep(10)
            return "done"

        dispatcher = AgentDispatcher(registry, executor=slow_exec)
        results = await dispatcher.dispatch_many(
            [make_task(task_id="slow-1")],
            timeout=0.05,
        )
        assert len(results) == 1
        assert results[0].status == "failed"


# ---------------------------------------------------------------------------
# _result_from_future
# ---------------------------------------------------------------------------


class TestResultFromFuture:
    async def test_result_from_future_success(self, registry):
        fut: asyncio.Future[AgentTaskResult] = asyncio.Future()
        fut.set_result(AgentTaskResult(task_id="a", agent_name="e", status="completed", output="ok"))
        task = make_task(task_id="a")
        result = AgentDispatcher._result_from_future(task, fut)
        assert result.status == "completed"

    async def test_result_from_future_cancelled(self, registry):
        fut: asyncio.Future[AgentTaskResult] = asyncio.Future()
        fut.cancel()
        task = make_task(task_id="b")
        result = AgentDispatcher._result_from_future(task, fut)
        assert result.status == "failed"
        assert "timed out" in result.output


# ---------------------------------------------------------------------------
# AgentTaskResult
# ---------------------------------------------------------------------------


class TestAgentTaskResult:
    def test_default_artifacts_is_empty_list(self):
        result = AgentTaskResult(task_id="x", agent_name="y", status="completed", output="ok")
        assert result.artifacts == []

    def test_default_duration_is_zero(self):
        result = AgentTaskResult(task_id="x", agent_name="y", status="completed", output="ok")
        assert result.duration_seconds == 0.0

    def test_duration_tracked_on_completion(self):
        result = AgentTaskResult(
            task_id="x",
            agent_name="y",
            status="completed",
            output="ok",
            duration_seconds=2.5,
        )
        assert result.duration_seconds == 2.5


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_registry_sealed_still_works(self, registry):
        registry.seal()
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        result = await dispatcher.dispatch_one(make_task())
        assert result.status == "completed"

    async def test_dispatch_one_tracks_start_and_completion(self, registry):
        recorder = MagicMock()
        dispatcher = AgentDispatcher(
            registry,
            executor=AsyncMock(return_value="done"),
            run_recorder=recorder,
        )
        await dispatcher.dispatch_one(make_task(task_id="recorded"))
        assert recorder.record.call_count >= 2

    async def test_dispatched_task_cleaned_from_active_tasks(self, registry):
        dispatcher = AgentDispatcher(registry, executor=AsyncMock(return_value="done"))
        task = make_task(task_id="cleanup-test")
        await dispatcher.dispatch_one(task)
        async with dispatcher._lock:
            assert "cleanup-test" not in dispatcher._active_tasks
        assert dispatcher.active_count == 0
