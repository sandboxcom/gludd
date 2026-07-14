"""Unit tests for AG.11: Map-reduce graph patterns (LangGraph fan-out).

Tests the MapReduceExecutor that fans out sub-tasks to parallel exec nodes
via LangGraph's Send API and collects results in a reducer.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from general_ludd.execution.map_reduce_executor import (
    MapReduceExecutor,
    SubTask,
    SubTaskResult,
    SubTaskStatus,
    map_reduce_builder,
    run_map_reduce,
)


class TestSubTaskModel:
    def test_subtask_construction_minimal(self):
        t = SubTask(task_id="task-1")
        assert t.task_id == "task-1"
        assert t.payload is None
        assert t.timeout is None
        assert t.max_retries == 0

    def test_subtask_construction_full(self):
        t = SubTask(
            task_id="task-1",
            payload={"input": "hello"},
            timeout=5.0,
            max_retries=2,
        )
        assert t.task_id == "task-1"
        assert t.payload == {"input": "hello"}
        assert t.timeout == 5.0
        assert t.max_retries == 2


class TestSubTaskResultModel:
    def test_result_success(self):
        r = SubTaskResult(
            task_id="task-1",
            status=SubTaskStatus.COMPLETED,
            output="result-1",
        )
        assert r.task_id == "task-1"
        assert r.status == SubTaskStatus.COMPLETED
        assert r.output == "result-1"
        assert r.error is None

    def test_result_failure(self):
        r = SubTaskResult(
            task_id="task-2",
            status=SubTaskStatus.FAILED,
            error="something broke",
        )
        assert r.task_id == "task-2"
        assert r.status == SubTaskStatus.FAILED
        assert r.output is None
        assert r.error == "something broke"

    def test_result_timed_out(self):
        r = SubTaskResult(
            task_id="task-3",
            status=SubTaskStatus.TIMED_OUT,
        )
        assert r.status == SubTaskStatus.TIMED_OUT


class TestMapReduceSingleTask:
    """AG.11 requirement: Single task -> single result."""

    @pytest.mark.asyncio
    async def test_single_task_returns_single_result(self):
        async def handler(payload: Any) -> str:
            return "done"

        executor = MapReduceExecutor(handler=handler)
        task = SubTask(task_id="task-1")
        results = await executor.execute([task])

        assert len(results) == 1
        assert results[0].task_id == "task-1"
        assert results[0].status == SubTaskStatus.COMPLETED
        assert results[0].output == "done"

    @pytest.mark.asyncio
    async def test_single_task_with_payload_passed_to_handler(self):
        seen: list[Any] = []

        async def handler(payload: Any) -> str:
            seen.append(payload)
            return str(payload)

        executor = MapReduceExecutor(handler=handler)
        task = SubTask(task_id="task-1", payload={"x": 42})
        results = await executor.execute([task])

        assert results[0].output == "{'x': 42}"
        assert seen == [{"x": 42}]


class TestMapReduceMultipleTasks:
    """AG.11 requirement: Multiple tasks -> all complete."""

    @pytest.mark.asyncio
    async def test_multiple_tasks_all_complete(self):
        async def handler(payload: Any) -> str:
            await asyncio.sleep(0.01)
            return f"result-{payload}"

        tasks = [
            SubTask(task_id="t-1", payload=1),
            SubTask(task_id="t-2", payload=2),
            SubTask(task_id="t-3", payload=3),
        ]
        executor = MapReduceExecutor(handler=handler)
        results = await executor.execute(tasks)

        assert len(results) == 3
        for r in results:
            assert r.status == SubTaskStatus.COMPLETED
        outputs = {r.task_id: r.output for r in results}
        assert outputs["t-1"] == "result-1"
        assert outputs["t-2"] == "result-2"
        assert outputs["t-3"] == "result-3"

    @pytest.mark.asyncio
    async def test_multiple_tasks_run_concurrently(self):
        started: list[str] = []
        completed: list[str] = []

        async def handler(payload: Any) -> str:
            started.append(payload)
            await asyncio.sleep(0.02)
            completed.append(payload)
            return str(payload)

        tasks = [
            SubTask(task_id="a", payload="a"),
            SubTask(task_id="b", payload="b"),
            SubTask(task_id="c", payload="c"),
        ]
        executor = MapReduceExecutor(handler=handler)
        await executor.execute(tasks)

        assert len(started) == 3
        assert set(started) == {"a", "b", "c"}
        assert len(completed) == 3
        assert set(completed) == {"a", "b", "c"}


class TestMapReduceTimeout:
    """AG.11 requirement: Timeout kills slow tasks, others continue."""

    @pytest.mark.asyncio
    async def test_timeout_kills_slow_task_others_continue(self):
        async def handler(payload: Any) -> str:
            if payload == "slow":
                await asyncio.sleep(1.0)
                return "never"
            await asyncio.sleep(0.01)
            return f"fast-{payload}"

        tasks = [
            SubTask(task_id="fast-1", payload="a", timeout=0.05),
            SubTask(task_id="slow", payload="slow", timeout=0.05),
            SubTask(task_id="fast-2", payload="c", timeout=0.05),
        ]
        executor = MapReduceExecutor(handler=handler)
        results = await executor.execute(tasks)

        assert len(results) == 3
        fast_results = [r for r in results if r.task_id.startswith("fast")]
        slow_result = [r for r in results if r.task_id == "slow"]

        assert len(fast_results) == 2
        for r in fast_results:
            assert r.status == SubTaskStatus.COMPLETED
            assert r.output.startswith("fast-")

        assert len(slow_result) == 1
        assert slow_result[0].status == SubTaskStatus.TIMED_OUT


class TestMapReducePartialFailure:
    """AG.11 requirement: Partial failure handled gracefully."""

    @pytest.mark.asyncio
    async def test_one_task_fails_others_continue(self):
        call_order: list[str] = []

        async def handler(payload: Any) -> str:
            call_order.append(payload)
            if payload == "fail":
                raise RuntimeError("boom")
            await asyncio.sleep(0.01)
            return f"ok-{payload}"

        tasks = [
            SubTask(task_id="good-1", payload="a"),
            SubTask(task_id="bad", payload="fail"),
            SubTask(task_id="good-2", payload="c"),
        ]
        executor = MapReduceExecutor(handler=handler)
        results = await executor.execute(tasks)

        assert len(results) == 3
        good = [r for r in results if r.task_id.startswith("good")]
        bad = [r for r in results if r.task_id == "bad"]

        assert len(good) == 2
        for r in good:
            assert r.status == SubTaskStatus.COMPLETED
            assert r.output.startswith("ok-")

        assert len(bad) == 1
        assert bad[0].status == SubTaskStatus.FAILED
        assert "boom" in (bad[0].error or "")

        assert len(call_order) == 3

    @pytest.mark.asyncio
    async def test_execute_returns_all_results_even_when_some_fail(self):
        async def handler(payload: Any) -> str:
            if payload == 2:
                raise ValueError("two is bad")
            return f"v-{payload}"

        tasks = [SubTask(task_id=f"t-{i}", payload=i) for i in range(5)]
        executor = MapReduceExecutor(handler=handler)
        results = await executor.execute(tasks)

        assert len(results) == 5
        succeeded = [r for r in results if r.status == SubTaskStatus.COMPLETED]
        failed = [r for r in results if r.status == SubTaskStatus.FAILED]
        assert len(succeeded) == 4
        assert len(failed) == 1
        assert failed[0].task_id == "t-2"


class TestMapReduceEdgeCases:
    """AG.11 edge cases: empty list, None output, default timeout, ordering, sync/async."""

    @pytest.mark.asyncio
    async def test_empty_task_list_returns_empty_results(self):
        async def handler(payload: Any) -> str:
            return "should-not-be-called"

        executor = MapReduceExecutor(handler=handler)
        results = await executor.execute([])
        assert results == []

    @pytest.mark.asyncio
    async def test_none_output_from_handler_becomes_empty_string(self):
        async def handler(payload: Any) -> str | None:
            return None

        executor = MapReduceExecutor(handler=handler)
        task = SubTask(task_id="nil")
        results = await executor.execute([task])

        assert len(results) == 1
        assert results[0].status == SubTaskStatus.COMPLETED
        assert results[0].output == ""

    @pytest.mark.asyncio
    async def test_default_executor_timeout_kills_slow_tasks(self):
        async def handler(payload: Any) -> str:
            if payload == "slow":
                await asyncio.sleep(1.0)
                return "never"
            return f"fast-{payload}"

        executor = MapReduceExecutor(handler=handler, default_timeout=0.05)
        tasks = [
            SubTask(task_id="fast", payload="a"),
            SubTask(task_id="slow", payload="slow"),
        ]
        results = await executor.execute(tasks)

        assert len(results) == 2
        fast = [r for r in results if r.task_id == "fast"]
        slow = [r for r in results if r.task_id == "slow"]
        assert fast[0].status == SubTaskStatus.COMPLETED
        assert slow[0].status == SubTaskStatus.TIMED_OUT

    @pytest.mark.asyncio
    async def test_result_ordering_preserves_input_order(self):
        async def handler(payload: Any) -> str:
            return str(payload)

        tasks = [SubTask(task_id=f"t-{i}", payload=i) for i in range(10)]
        executor = MapReduceExecutor(handler=handler)
        results = await executor.execute(tasks)

        assert len(results) == 10
        for i, r in enumerate(results):
            assert r.task_id == f"t-{i}"
            assert r.status == SubTaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_mixed_sync_and_async_handlers(self):
        async def async_handler(payload: Any) -> str:
            await asyncio.sleep(0.01)
            return f"async-{payload}"

        def sync_handler(payload: Any) -> str:
            return f"sync-{payload}"

        class SwitchingExecutor(MapReduceExecutor):
            def __init__(self) -> None:
                super().__init__(handler=lambda x: x)

            async def execute(self, tasks: list[SubTask]) -> list[SubTaskResult]:
                self._handler = async_handler
                results_async = await super().execute(tasks)
                self._handler = sync_handler
                results_sync = await super().execute(tasks)
                return results_async + results_sync

        tasks = [SubTask(task_id="t", payload="x")]
        executor = SwitchingExecutor()
        results = await executor.execute(tasks)

        assert len(results) == 2
        assert results[0].output == "async-x"
        assert results[1].output == "sync-x"


class TestMapReduceBuilder:
    """AG.11 requirement: Build a LangGraph graph via map_reduce_builder."""

    @pytest.mark.asyncio
    async def test_builder_returns_callable_graph(self):
        async def handler(payload: Any) -> str:
            return f"built-{payload}"

        graph = map_reduce_builder(handler)
        result = await graph.ainvoke(
            {
                "tasks": [
                    SubTask(task_id="x", payload="one"),
                    SubTask(task_id="y", payload="two"),
                ],
            }
        )

        results = result["results"]
        assert len(results) == 2
        for r in results:
            assert r.status == SubTaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_run_map_reduce_convenience(self):
        async def handler(payload: Any) -> str:
            return str(payload).upper()

        tasks = [
            SubTask(task_id="a", payload="hello"),
            SubTask(task_id="b", payload="world"),
        ]
        results = await run_map_reduce(tasks, handler)
        assert len(results) == 2
        assert results[0].output == "HELLO"
        assert results[1].output == "WORLD"
