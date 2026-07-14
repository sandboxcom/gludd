"""E2E integration tests for AgentDispatcher — real async execution path.

Exercises the full dispatch pipeline with real AgentRegistry + coroutine executors:
  - dispatch_many concurrent execution with real async tasks
  - AgentRegistry registration → dispatch → status tracking
  - Semaphore concurrency limiting (per-agent max_concurrent)
  - Task completion / failure / blocked statuses
  - Error handling: executor exceptions → status='failed'
  - Pause controller integration: project pause → status='blocked'
  - Orchestration guard: nesting depth, capability escalation,
    rate limiting, spiral detection
  - dispatch_many empty input, timeout handling, model call semaphore

Does NOT duplicate unit-test coverage (test_dispatcher.py,
test_dispatcher_semaphore.py, test_dispatcher_coverage.py).
"""

from __future__ import annotations

import asyncio

from general_ludd.agents.dispatcher import (
    AgentDispatcher,
    AgentTask,
    AgentTaskResult,
)
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType
from general_ludd.config.user_config import OrchestrationGuardConfig
from general_ludd.controllers.pause_controller import PauseController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _subagent_cfg(
    name: str,
    *,
    enabled: bool = True,
    max_concurrent: int = 1,
    can_edit: bool = False,
    can_bash: bool = False,
    can_read: bool = True,
    can_dispatch: bool = False,
    allowed: list[str] | None = None,
) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Agent {name}",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(
            can_edit=can_edit,
            can_bash=can_bash,
            can_read=can_read,
            can_dispatch_subagents=can_dispatch,
            allowed_subagents=allowed or [],
        ),
        max_concurrent=max_concurrent,
        enabled=enabled,
    )


def _invoker_cfg(
    name: str,
    can_dispatch: bool = True,
    allowed: list[str] | None = None,
    *,
    can_edit: bool = True,
    can_bash: bool = True,
) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Invoker {name}",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_edit=can_edit,
            can_bash=can_bash,
            can_read=True,
            can_dispatch_subagents=can_dispatch,
            allowed_subagents=allowed or ["*"],
        ),
        enabled=True,
    )


def _make_registry() -> AgentRegistry:
    return AgentRegistry()


# ---------------------------------------------------------------------------
# dispatch_many concurrent execution
# ---------------------------------------------------------------------------


class TestDispatchManyConcurrent:
    async def test_all_tasks_complete_with_correct_outputs(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("w1", max_concurrent=3))
        reg.register(_subagent_cfg("w2", max_concurrent=3))

        async def _exec(task: AgentTask) -> str:
            await asyncio.sleep(0.01)
            return f"out:{task.agent_name}:{task.task_id}"

        disp = AgentDispatcher(reg, executor=_exec)

        tasks = [
            AgentTask(task_id="A", agent_name="w1", description="tA", prompt="pA", invoker_name="build"),
            AgentTask(task_id="B", agent_name="w2", description="tB", prompt="pB", invoker_name="build"),
            AgentTask(task_id="C", agent_name="w1", description="tC", prompt="pC", invoker_name="build"),
        ]
        results = await disp.dispatch_many(tasks)

        assert len(results) == 3
        assert all(r.status == "completed" for r in results)
        outputs = {r.task_id: r.output for r in results}
        assert outputs["A"] == "out:w1:A"
        assert outputs["B"] == "out:w2:B"
        assert outputs["C"] == "out:w1:C"

    async def test_concurrent_execution_is_truly_parallel(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("fast", max_concurrent=5))

        started: list[str] = []
        lock = asyncio.Lock()

        async def _exec(task: AgentTask) -> str:
            async with lock:
                started.append(task.task_id)
            await asyncio.sleep(0.05)
            return f"done:{task.task_id}"

        disp = AgentDispatcher(reg, executor=_exec)

        tasks = [
            AgentTask(task_id=f"t{i}", agent_name="fast", description=f"d{i}", prompt="p", invoker_name="build")
            for i in range(5)
        ]
        await disp.dispatch_many(tasks)

        assert len(started) == 5


# ---------------------------------------------------------------------------
# Semaphore concurrency limiting
# ---------------------------------------------------------------------------


class TestSemaphoreConcurrencyE2E:
    async def test_max_concurrent_is_respected_under_load(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("bottleneck", max_concurrent=2))

        peak: list[int] = [0]
        inflight: list[int] = [0]
        lock = asyncio.Lock()

        async def _exec(task: AgentTask) -> str:
            async with lock:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
            await asyncio.sleep(0.02)
            async with lock:
                inflight[0] -= 1
            return f"done:{task.task_id}"

        disp = AgentDispatcher(reg, executor=_exec)

        tasks = [
            AgentTask(task_id=f"b{i}", agent_name="bottleneck", description=f"d{i}", prompt="p", invoker_name="build")
            for i in range(8)
        ]
        await disp.dispatch_many(tasks)

        assert peak[0] <= 2, f"peak concurrency {peak[0]} exceeded limit 2"

    async def test_independent_agent_semaphores_run_concurrently(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("agent_a", max_concurrent=1))
        reg.register(_subagent_cfg("agent_b", max_concurrent=1))

        max_total: list[int] = [0]
        inflight: list[int] = [0]
        lock = asyncio.Lock()

        async def _exec(task: AgentTask) -> str:
            async with lock:
                inflight[0] += 1
                max_total[0] = max(max_total[0], inflight[0])
            await asyncio.sleep(0.02)
            async with lock:
                inflight[0] -= 1
            return f"done:{task.agent_name}:{task.task_id}"

        disp = AgentDispatcher(reg, executor=_exec)

        tasks = [
            AgentTask(task_id=f"a{i}", agent_name="agent_a", description="", prompt="p", invoker_name="build")
            for i in range(3)
        ] + [
            AgentTask(task_id=f"b{i}", agent_name="agent_b", description="", prompt="p", invoker_name="build")
            for i in range(3)
        ]
        await disp.dispatch_many(tasks)

        assert max_total[0] >= 2, f"expected >=2 concurrent across agents, got {max_total[0]}"


# ---------------------------------------------------------------------------
# Task completion status tracking
# ---------------------------------------------------------------------------


class TestTaskCompletionStatus:
    async def test_mixed_statuses_reflected_in_results(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("good"))
        reg.register(_subagent_cfg("bad", enabled=False))
        reg.register(_subagent_cfg("missing", enabled=False))  # won't be in registry

        async def _exec(task: AgentTask) -> str:
            return f"ok:{task.task_id}"

        disp = AgentDispatcher(reg, executor=_exec)

        tasks = [
            AgentTask(task_id="ok", agent_name="good", description="", prompt="p", invoker_name="build"),
            AgentTask(task_id="disabled", agent_name="bad", description="", prompt="p", invoker_name="build"),
            AgentTask(task_id="unknown", agent_name="nope_missing", description="", prompt="p", invoker_name="build"),
        ]
        results = await disp.dispatch_many(tasks)

        statuses = {r.task_id: r.status for r in results}
        assert statuses["ok"] == "completed"
        assert statuses["disabled"] == "failed"
        assert "disabled" in {r.task_id: r.output for r in results}["disabled"].lower()
        assert statuses["unknown"] == "failed"
        assert "not found" in {r.task_id: r.output for r in results}["unknown"].lower()


# ---------------------------------------------------------------------------
# Error handling: executor exceptions
# ---------------------------------------------------------------------------


class TestErrorHandlingE2E:
    async def test_gather_captures_exceptions_as_failed_results(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("stable"))
        reg.register(_subagent_cfg("flaky"))

        async def _exec(task: AgentTask) -> str:
            if task.agent_name == "flaky":
                raise RuntimeError("simulated crash in executor")
            return "success"

        disp = AgentDispatcher(reg, executor=_exec)

        tasks = [
            AgentTask(task_id="s", agent_name="stable", description="", prompt="p", invoker_name="build"),
            AgentTask(task_id="f", agent_name="flaky", description="", prompt="p", invoker_name="build"),
        ]
        results = await disp.dispatch_many(tasks)

        statuses = {r.task_id: r.status for r in results}
        assert statuses["s"] == "completed"
        assert statuses["f"] == "failed"
        fail_output = {r.task_id: r.output for r in results}["f"]
        assert "simulated crash" in fail_output

    async def test_concurrent_failures_do_not_block_others(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("mix", max_concurrent=3))

        async def _exec(task: AgentTask) -> str:
            if task.task_id in ("fail_a", "fail_b"):
                await asyncio.sleep(0.01)
                raise RuntimeError(f"executor error in {task.task_id}")
            await asyncio.sleep(0.01)
            return f"ok:{task.task_id}"

        disp = AgentDispatcher(reg, executor=_exec)

        tasks = [
            AgentTask(task_id="fail_a", agent_name="mix", description="", prompt="p", invoker_name="build"),
            AgentTask(task_id="ok_1", agent_name="mix", description="", prompt="p", invoker_name="build"),
            AgentTask(task_id="fail_b", agent_name="mix", description="", prompt="p", invoker_name="build"),
            AgentTask(task_id="ok_2", agent_name="mix", description="", prompt="p", invoker_name="build"),
        ]
        results = await disp.dispatch_many(tasks)

        statuses = {r.task_id: r.status for r in results}
        assert statuses["ok_1"] == "completed"
        assert statuses["ok_2"] == "completed"
        assert statuses["fail_a"] == "failed"
        assert statuses["fail_b"] == "failed"


# ---------------------------------------------------------------------------
# Pause controller integration
# ---------------------------------------------------------------------------


class TestPauseControllerE2E:
    async def test_paused_project_blocks_dispatch(self) -> None:
        pc = PauseController()
        pc.pause("project", "proj-paused", reason="testing pause gate")

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        executor_ran: list[str] = []

        async def _exec(task: AgentTask) -> str:
            executor_ran.append(task.task_id)
            return "ran"

        disp = AgentDispatcher(reg, executor=_exec, pause_controller=pc)

        task = AgentTask(
            task_id="t-paused",
            agent_name="worker",
            description="",
            prompt="p",
            invoker_name="build",
            project_id="proj-paused",
        )
        result = await disp.dispatch_one(task)

        assert result.status == "blocked"
        assert "paused" in result.output.lower()
        assert len(executor_ran) == 0, "executor must not run for paused project"

    async def test_active_project_still_dispatches(self) -> None:
        pc = PauseController()
        pc.pause("project", "proj-paused", reason="some project is paused")

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return f"exec:{task.task_id}"

        disp = AgentDispatcher(reg, executor=_exec, pause_controller=pc)

        task = AgentTask(
            task_id="t-ok",
            agent_name="worker",
            description="",
            prompt="p",
            invoker_name="build",
            project_id="proj-active",
        )
        result = await disp.dispatch_one(task)

        assert result.status == "completed"
        assert "exec:t-ok" in result.output

    async def test_task_without_project_id_not_affected_by_pause(self) -> None:
        pc = PauseController()
        pc.pause("project", "any", reason="paused")

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, pause_controller=pc)

        task = AgentTask(
            task_id="t-noproj",
            agent_name="worker",
            description="",
            prompt="p",
            invoker_name="build",
        )
        result = await disp.dispatch_one(task)

        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Orchestration guard: nesting depth
# ---------------------------------------------------------------------------


class TestNestingDepthGuard:
    async def test_depth_exceeds_limit_is_denied(self) -> None:
        guard = OrchestrationGuardConfig(max_nesting_depth=2)

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        disp = AgentDispatcher(reg, orchestration_guard=guard)

        task = AgentTask(
            task_id="deep",
            agent_name="worker",
            description="",
            prompt="p",
            invoker_name="build",
            depth=5,
        )
        result = await disp.dispatch_one(task)

        assert result.status == "failed"
        assert "Max nesting depth exceeded" in result.output

    async def test_depth_within_limit_passes(self) -> None:
        guard = OrchestrationGuardConfig(max_nesting_depth=3)

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        task = AgentTask(
            task_id="shallow",
            agent_name="worker",
            description="",
            prompt="p",
            invoker_name="build",
            depth=1,
        )
        result = await disp.dispatch_one(task)

        assert result.status == "completed"

    async def test_depth_guard_without_config_always_passes(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec)

        task = AgentTask(
            task_id="deep-no-guard",
            agent_name="worker",
            description="",
            prompt="p",
            invoker_name="build",
            depth=999,
        )
        result = await disp.dispatch_one(task)

        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Orchestration guard: capability escalation
# ---------------------------------------------------------------------------


class TestCapabilityEscalationGuard:
    async def test_child_has_cap_parent_lacks_is_denied(self) -> None:
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)

        reg = _make_registry()
        reg.register(_invoker_cfg("readonly", can_edit=False, can_bash=False, allowed=["*"]))
        reg.register(_subagent_cfg("editor", can_edit=True, can_bash=True))

        disp = AgentDispatcher(reg, orchestration_guard=guard)

        task = AgentTask(
            task_id="esc",
            agent_name="editor",
            description="",
            prompt="p",
            invoker_name="readonly",
        )
        result = await disp.dispatch_one(task)

        assert result.status == "failed"
        assert "Capability escalation denied" in result.output
        assert "can_edit" in result.output

    async def test_child_with_same_caps_passes(self) -> None:
        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)

        reg = _make_registry()
        reg.register(_invoker_cfg("full", can_edit=True, can_bash=True, allowed=["*"]))
        reg.register(_subagent_cfg("worker", can_edit=False, can_bash=False))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        task = AgentTask(
            task_id="safe",
            agent_name="worker",
            description="",
            prompt="p",
            invoker_name="full",
        )
        result = await disp.dispatch_one(task)

        assert result.status == "completed"

    async def test_capability_guard_disabled_passes(self) -> None:
        guard = OrchestrationGuardConfig(enforce_capability_escalation=False)

        reg = _make_registry()
        reg.register(_invoker_cfg("readonly", can_edit=False, can_bash=False, allowed=["*"]))
        reg.register(_subagent_cfg("editor", can_edit=True, can_bash=True))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        task = AgentTask(
            task_id="esc-disabled",
            agent_name="editor",
            description="",
            prompt="p",
            invoker_name="readonly",
        )
        result = await disp.dispatch_one(task)

        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Orchestration guard: rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiterGuard:
    async def test_rate_limit_exceeded_in_window(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=3,
            dispatch_rate_window_s=10.0,
        )

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        results: list[AgentTaskResult] = []
        for i in range(5):
            task = AgentTask(
                task_id=f"rl{i}",
                agent_name="worker",
                description="",
                prompt="p",
                invoker_name="build",
            )
            results.append(await disp.dispatch_one(task))

        statuses = [r.status for r in results]
        assert statuses[:3] == ["completed", "completed", "completed"]
        assert "failed" in statuses[3:], statuses
        failed = [r for r in results if r.status == "failed"]
        assert any("rate limited" in r.output.lower() for r in failed)

    async def test_rate_limit_respects_window_expiry(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=2,
            dispatch_rate_window_s=0.01,
        )

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        results: list[AgentTaskResult] = []
        for i in range(4):
            task = AgentTask(
                task_id=f"rw{i}",
                agent_name="worker",
                description="",
                prompt="p",
                invoker_name="build",
            )
            results.append(await disp.dispatch_one(task))
            await asyncio.sleep(0.015)  # exceed window_s

        assert all(r.status == "completed" for r in results)

    async def test_rate_limit_off_when_zero(self) -> None:
        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=0,
            dispatch_rate_window_s=1.0,
        )

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        for i in range(10):
            task = AgentTask(
                task_id=f"off{i}",
                agent_name="worker",
                description="",
                prompt="p",
                invoker_name="build",
            )
            result = await disp.dispatch_one(task)
            assert result.status == "completed"


# ---------------------------------------------------------------------------
# Orchestration guard: spiral detection
# ---------------------------------------------------------------------------


class TestSpiralDetectionGuard:
    async def test_redispatch_same_task_id_exceeds_limit(self) -> None:
        guard = OrchestrationGuardConfig(max_redispatch_count=3)

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        results: list[AgentTaskResult] = []
        for _ in range(5):
            task = AgentTask(
                task_id="spiral-me",  # same ID every time
                agent_name="worker",
                description="",
                prompt="p",
                invoker_name="build",
            )
            results.append(await disp.dispatch_one(task))

        statuses = [r.status for r in results]
        assert statuses[:3] == ["completed", "completed", "completed"]
        assert statuses[3] == "failed", f"expected 4th dispatch to fail: {statuses}"
        assert statuses[4] == "failed"
        failed = results[3]
        assert "Spiral detected" in failed.output

    async def test_spiral_guard_off_when_zero(self) -> None:
        guard = OrchestrationGuardConfig(max_redispatch_count=0)

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        for _ in range(6):
            task = AgentTask(
                task_id="no-spiral-guard",
                agent_name="worker",
                description="",
                prompt="p",
                invoker_name="build",
            )
            result = await disp.dispatch_one(task)
            assert result.status == "completed"

    async def test_different_task_ids_count_independently(self) -> None:
        guard = OrchestrationGuardConfig(max_redispatch_count=2)

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("worker"))

        async def _exec(task: AgentTask) -> str:
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        results: list[AgentTaskResult] = []
        for i in range(3):
            task = AgentTask(
                task_id=f"distinct-{i}",
                agent_name="worker",
                description="",
                prompt="p",
                invoker_name="build",
            )
            results.append(await disp.dispatch_one(task))

        assert all(r.status == "completed" for r in results)


# ---------------------------------------------------------------------------
# dispatch_many edge cases
# ---------------------------------------------------------------------------


class TestDispatchManyEdgeCases:
    async def test_empty_task_list_returns_empty(self) -> None:
        reg = _make_registry()
        disp = AgentDispatcher(reg)
        results = await disp.dispatch_many([])
        assert results == []

    async def test_timeout_exceeded_cancels_pending_tasks(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("slow", max_concurrent=5))

        async def _slow_exec(task: AgentTask) -> str:
            await asyncio.sleep(10.0)  # exceeds timeout
            return "never"

        disp = AgentDispatcher(reg, executor=_slow_exec)

        tasks = [
            AgentTask(task_id=f"s{i}", agent_name="slow", description="", prompt="p", invoker_name="build")
            for i in range(3)
        ]
        results = await disp.dispatch_many(tasks, timeout=0.05)

        assert len(results) == 3
        assert all(r.status == "failed" for r in results)
        assert any("timed out" in r.output.lower() for r in results)

    async def test_invoker_permission_denied_is_failed_or_completed_result(self) -> None:
        reg = _make_registry()
        reg.register(_subagent_cfg("worker"))
        reg.register(_invoker_cfg("build", allowed=["other_agent"]))

        disp = AgentDispatcher(reg)

        tasks = [
            AgentTask(task_id="denied", agent_name="worker", description="", prompt="p", invoker_name="build"),
        ]
        results = await disp.dispatch_many(tasks)

        assert len(results) == 1
        assert results[0].status == "failed"
        assert "permission denied" in results[0].output.lower()


# ---------------------------------------------------------------------------
# Model call semaphore
# ---------------------------------------------------------------------------


class TestModelCallSemaphore:
    async def test_model_call_semaphore_limits_concurrent_executor_calls(self) -> None:
        """The dispatcher wraps executor execution in _model_call_semaphore.
        With a limit of 2, only 2 executors run concurrently even if
        per-agent semaphore limits are higher."""
        guard = OrchestrationGuardConfig(max_concurrent_model_calls=2)

        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("w1", max_concurrent=5))
        reg.register(_subagent_cfg("w2", max_concurrent=5))

        peak: list[int] = [0]
        inflight: list[int] = [0]
        lock = asyncio.Lock()

        async def _exec(task: AgentTask) -> str:
            async with lock:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
            await asyncio.sleep(0.03)
            async with lock:
                inflight[0] -= 1
            return f"ok:{task.task_id}"

        disp = AgentDispatcher(reg, executor=_exec, orchestration_guard=guard)

        tasks = [
            AgentTask(
                task_id=f"mc{i}",
                agent_name=("w1" if i % 2 == 0 else "w2"),
                description="",
                prompt="p",
                invoker_name="build",
            )
            for i in range(8)
        ]
        await disp.dispatch_many(tasks)

        assert peak[0] <= 2, f"model call semaphore not respected: peak={peak[0]}"


# ---------------------------------------------------------------------------
# Active task tracking
# ---------------------------------------------------------------------------


class TestActiveTaskTracking:
    async def test_active_count_reflects_live_tasks(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("w1", max_concurrent=5))

        active_during_exec: list[int] = []
        lock = asyncio.Lock()

        async def _exec(task: AgentTask) -> str:
            async with lock:
                active_during_exec.append(disp.active_count)
            await asyncio.sleep(0.02)
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec)

        tasks = [
            AgentTask(task_id=f"ac{i}", agent_name="w1", description="", prompt="p", invoker_name="build")
            for i in range(3)
        ]
        await disp.dispatch_many(tasks)

        assert disp.active_count == 0, "active_count must be 0 after all tasks finish"
        assert max(active_during_exec) >= 1, "at least one task was running"

    async def test_get_active_tasks_for_project_filters_correctly(self) -> None:
        reg = _make_registry()
        reg.register(_invoker_cfg("build"))
        reg.register(_subagent_cfg("w1", max_concurrent=5))

        hold = asyncio.Event()

        async def _exec(task: AgentTask) -> str:
            await hold.wait()
            return "ok"

        disp = AgentDispatcher(reg, executor=_exec)

        t1 = AgentTask(
            task_id="p1", agent_name="w1", description="", prompt="p",
            invoker_name="build", project_id="alpha",
        )
        t2 = AgentTask(
            task_id="p2", agent_name="w1", description="", prompt="p",
            invoker_name="build", project_id="beta",
        )
        t3 = AgentTask(
            task_id="p3", agent_name="w1", description="", prompt="p",
            invoker_name="build", project_id="alpha",
        )

        tasks = [t1, t2, t3]
        fut = asyncio.ensure_future(disp.dispatch_many(tasks))

        await asyncio.sleep(0.05)

        alpha_tasks = await disp.get_active_tasks_for_project("alpha")
        beta_tasks = await disp.get_active_tasks_for_project("beta")

        assert len(alpha_tasks) == 2, f"expected 2 alpha tasks, got {len(alpha_tasks)}"
        assert {t.task_id for t in alpha_tasks} == {"p1", "p3"}
        assert len(beta_tasks) == 1
        assert beta_tasks[0].task_id == "p2"

        hold.set()
        await fut
