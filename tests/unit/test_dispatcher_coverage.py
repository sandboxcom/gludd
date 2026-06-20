"""Unit tests for dispatcher.py — covering gaps in the existing test suite.

Covers:
- dispatch_one: executor raises Exception → status='failed', output=str(exc)
- dispatch_many: one task raises, one succeeds (return_exceptions=False in gather
  means the exception propagates — but dispatch_one catches it itself, so
  dispatch_many always returns results, never propagates raw exceptions)
- active_count tracking decrements even on exception (finally block)
- _noop_executor is the default when executor=None
- dispatch_many([]) returns []

NOTE on gather behaviour: asyncio.gather(*coros) without return_exceptions=True
propagates the first exception. However because dispatch_one itself catches all
Exception subclasses and converts them to status='failed' results, gather never
sees an exception from any of these coroutines. dispatch_many therefore always
returns a list of AgentTaskResult — this is the resilient behaviour we assert.
"""

from __future__ import annotations

import asyncio

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _registry_with(*names: str, max_concurrent: int = 5) -> AgentRegistry:
    reg = AgentRegistry()
    for name in names:
        reg.register(AgentConfig(
            name=name,
            description=f"agent {name}",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(can_read=True),
            max_concurrent=max_concurrent,
        ))
    return reg


def _task(name: str, task_id: str = "t1") -> AgentTask:
    return AgentTask(
        task_id=task_id,
        agent_name=name,
        description="test",
        prompt="test prompt",
    )


# ---------------------------------------------------------------------------
# dispatch_one: executor raises Exception
# ---------------------------------------------------------------------------

class TestDispatchOneExecutorRaises:
    @pytest.mark.asyncio()
    async def test_executor_exception_returns_failed_result(self) -> None:
        """When the executor raises, dispatch_one returns status='failed'."""
        reg = _registry_with("worker")

        async def boom(task: AgentTask) -> str:
            raise RuntimeError("something went wrong")

        dispatcher = AgentDispatcher(registry=reg, executor=boom)
        result = await dispatcher.dispatch_one(_task("worker"))

        assert result.status == "failed"
        assert "something went wrong" in result.output

    @pytest.mark.asyncio()
    async def test_executor_exception_output_is_str_exc(self) -> None:
        """The output field contains str(exc) from the caught exception."""
        reg = _registry_with("worker")

        async def boom(task: AgentTask) -> str:
            raise ValueError("bad value 42")

        dispatcher = AgentDispatcher(registry=reg, executor=boom)
        result = await dispatcher.dispatch_one(_task("worker"))

        assert result.output == "bad value 42"
        assert result.status == "failed"

    @pytest.mark.asyncio()
    async def test_executor_exception_duration_recorded(self) -> None:
        """duration_seconds is set even when the executor raises."""
        reg = _registry_with("worker")

        async def slow_boom(task: AgentTask) -> str:
            await asyncio.sleep(0.01)
            raise RuntimeError("timed out internally")

        dispatcher = AgentDispatcher(registry=reg, executor=slow_boom)
        result = await dispatcher.dispatch_one(_task("worker"))

        assert result.status == "failed"
        assert result.duration_seconds >= 0.0  # always set in finally

    @pytest.mark.asyncio()
    async def test_active_count_decremented_after_exception(self) -> None:
        """active_count returns to 0 after an executor exception (finally block)."""
        reg = _registry_with("worker")

        async def boom(task: AgentTask) -> str:
            raise RuntimeError("explode")

        dispatcher = AgentDispatcher(registry=reg, executor=boom)
        assert dispatcher.active_count == 0

        await dispatcher.dispatch_one(_task("worker"))

        # Must be back to 0 — the finally block always decrements
        assert dispatcher.active_count == 0


# ---------------------------------------------------------------------------
# dispatch_many: one task raises, one succeeds (resilient gather path)
# ---------------------------------------------------------------------------

class TestDispatchManyOneRaises:
    @pytest.mark.asyncio()
    async def test_one_fails_other_succeeds(self) -> None:
        """dispatch_many returns all results even when one executor raises."""
        reg = _registry_with("worker", max_concurrent=5)

        async def selective_executor(task: AgentTask) -> str:
            if task.task_id == "fail_me":
                raise RuntimeError("forced failure")
            return f"ok:{task.task_id}"

        dispatcher = AgentDispatcher(registry=reg, executor=selective_executor)
        tasks = [
            AgentTask(task_id="fail_me", agent_name="worker", description="", prompt=""),
            AgentTask(task_id="pass_me", agent_name="worker", description="", prompt=""),
        ]

        results = await dispatcher.dispatch_many(tasks)

        assert len(results) == 2

        by_id = {r.task_id: r for r in results}
        assert by_id["fail_me"].status == "failed"
        assert "forced failure" in by_id["fail_me"].output

        assert by_id["pass_me"].status == "completed"
        assert by_id["pass_me"].output == "ok:pass_me"

    @pytest.mark.asyncio()
    async def test_all_fail_returns_all_failed(self) -> None:
        """dispatch_many returns failed results for all tasks when all executors raise."""
        reg = _registry_with("worker", max_concurrent=5)

        async def always_fail(task: AgentTask) -> str:
            raise ValueError(f"fail:{task.task_id}")

        dispatcher = AgentDispatcher(registry=reg, executor=always_fail)
        tasks = [
            AgentTask(task_id=f"t{i}", agent_name="worker", description="", prompt="")
            for i in range(4)
        ]
        results = await dispatcher.dispatch_many(tasks)

        assert len(results) == 4
        assert all(r.status == "failed" for r in results)

    @pytest.mark.asyncio()
    async def test_dispatch_many_empty_list(self) -> None:
        """dispatch_many([]) returns [] without error."""
        reg = _registry_with("worker")
        dispatcher = AgentDispatcher(registry=reg)
        results = await dispatcher.dispatch_many([])
        assert results == []

    @pytest.mark.asyncio()
    async def test_dispatch_many_result_order_matches_input(self) -> None:
        """Results are returned in the same order as the input tasks."""
        reg = _registry_with("worker", max_concurrent=10)

        async def executor(task: AgentTask) -> str:
            # Simulate variable delay: later tasks finish first
            delay = 0.05 if task.task_id == "t0" else 0.001
            await asyncio.sleep(delay)
            return task.task_id

        dispatcher = AgentDispatcher(registry=reg, executor=executor)
        tasks = [
            AgentTask(task_id=f"t{i}", agent_name="worker", description="", prompt="")
            for i in range(3)
        ]
        results = await dispatcher.dispatch_many(tasks)

        # Order must follow input, not completion order
        assert [r.task_id for r in results] == ["t0", "t1", "t2"]


# ---------------------------------------------------------------------------
# invoker_name field (correct field name vs stale test_dispatcher.py)
# ---------------------------------------------------------------------------

class TestDispatchInvokerNameField:
    @pytest.mark.asyncio()
    async def test_invoker_name_field_accepted(self) -> None:
        """AgentTask.invoker_name is the correct field name (not 'invoker')."""
        reg = _registry_with("target")

        # Also register an invoker that CAN dispatch target
        reg.register(AgentConfig(
            name="caller",
            description="",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=["target"],
            ),
        ))

        async def ok_executor(task: AgentTask) -> str:
            return "done"

        dispatcher = AgentDispatcher(registry=reg, executor=ok_executor)
        task = AgentTask(
            task_id="t1",
            agent_name="target",
            description="",
            prompt="",
            invoker_name="caller",  # correct field name
        )
        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"

    @pytest.mark.asyncio()
    async def test_invoker_name_empty_string_skips_permission_check(self) -> None:
        """Empty invoker_name (default) skips the permission check (back-compat)."""
        reg = _registry_with("worker")
        dispatcher = AgentDispatcher(registry=reg)
        task = AgentTask(
            task_id="t1",
            agent_name="worker",
            description="",
            prompt="",
            # invoker_name defaults to "" — condition: if task.invoker_name and ...
        )
        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"
