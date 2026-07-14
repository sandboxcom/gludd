"""Structural tests for execution/map_reduce_executor.py."""

from __future__ import annotations

from general_ludd.execution.map_reduce_executor import (
    MapReduceExecutor,
    SubTask,
    SubTaskResult,
    SubTaskStatus,
    _invoke_handler,
    _results_reducer,
    map_reduce_builder,
    run_map_reduce,
)


class TestSubTaskStatus:
    def test_expected_members(self):
        assert SubTaskStatus.PENDING.value == "pending"
        assert SubTaskStatus.COMPLETED.value == "completed"
        assert SubTaskStatus.FAILED.value == "failed"
        assert SubTaskStatus.TIMED_OUT.value == "timed_out"

    def test_member_count(self):
        assert len(list(SubTaskStatus)) == 4


class TestSubTask:
    def test_default_values(self):
        task = SubTask(task_id="t1")
        assert task.task_id == "t1"
        assert task.payload is None
        assert task.timeout is None
        assert task.max_retries == 0

    def test_full_values(self):
        task = SubTask(task_id="t1", payload={"x": 1}, timeout=10.0, max_retries=3)
        assert task.task_id == "t1"
        assert task.payload == {"x": 1}
        assert task.timeout == 10.0
        assert task.max_retries == 3


class TestSubTaskResult:
    def test_completed_result(self):
        result = SubTaskResult(
            task_id="t1",
            status=SubTaskStatus.COMPLETED,
            output="done",
        )
        assert result.task_id == "t1"
        assert result.status == SubTaskStatus.COMPLETED
        assert result.output == "done"
        assert result.error is None

    def test_failed_result(self):
        result = SubTaskResult(
            task_id="t1",
            status=SubTaskStatus.FAILED,
            error="something broke",
        )
        assert result.status == SubTaskStatus.FAILED
        assert result.output is None
        assert result.error == "something broke"

    def test_timed_out_result(self):
        result = SubTaskResult(
            task_id="t1",
            status=SubTaskStatus.TIMED_OUT,
        )
        assert result.status == SubTaskStatus.TIMED_OUT
        assert result.output is None
        assert result.error is None


class TestResultsReducer:
    def test_appends_two_lists(self):
        left = [SubTaskResult(task_id="a", status=SubTaskStatus.COMPLETED)]
        right = [SubTaskResult(task_id="b", status=SubTaskStatus.FAILED)]
        merged = _results_reducer(left, right)
        assert len(merged) == 2
        assert merged[0].task_id == "a"
        assert merged[1].task_id == "b"

    def test_empty_left(self):
        right = [SubTaskResult(task_id="a", status=SubTaskStatus.COMPLETED)]
        merged = _results_reducer([], right)
        assert merged == right


class TestInvokeHandler:
    def test_sync_handler_returns_result(self):
        import asyncio
        result = asyncio.run(_invoke_handler(lambda x: x * 2, 21))
        assert result == 42

    def test_sync_handler_returns_none(self):
        import asyncio
        result = asyncio.run(_invoke_handler(lambda x: None, "anything"))
        assert result is None


class TestMapReduceBuilder:
    def test_returns_compiled_graph(self):
        graph = map_reduce_builder(lambda x: x)
        assert graph is not None
        assert hasattr(graph, "ainvoke")


class TestMapReduceExecutor:
    def test_construct_with_handler(self):
        executor = MapReduceExecutor(handler=lambda x: x)
        assert executor._handler is not None

    def test_construct_with_timeout(self):
        executor = MapReduceExecutor(handler=lambda x: x, default_timeout=5.0)
        assert executor._default_timeout == 5.0

    def test_construct_without_timeout(self):
        executor = MapReduceExecutor(handler=lambda x: x)
        assert executor._default_timeout is None

    def test_execute_returns_results(self):
        import asyncio
        executor = MapReduceExecutor(handler=lambda x: x)
        tasks = [SubTask(task_id="t1", payload="hello")]
        results = asyncio.run(executor.execute(tasks))
        assert len(results) == 1
        assert results[0].task_id == "t1"
        assert results[0].status == SubTaskStatus.COMPLETED
        assert results[0].output == "hello"

    def test_execute_empty_tasks(self):
        import asyncio
        executor = MapReduceExecutor(handler=lambda x: x)
        results = asyncio.run(executor.execute([]))
        assert results == []

    def test_execute_failing_handler(self):
        import asyncio

        def failing(_):
            raise ValueError("test failure")

        executor = MapReduceExecutor(handler=failing)
        tasks = [SubTask(task_id="t1")]
        results = asyncio.run(executor.execute(tasks))
        assert results[0].status == SubTaskStatus.FAILED
        assert "test failure" in str(results[0].error)

    def test_execute_preserves_order(self):
        import asyncio
        executor = MapReduceExecutor(handler=lambda x: x)
        tasks = [
            SubTask(task_id="a", payload="first"),
            SubTask(task_id="b", payload="second"),
            SubTask(task_id="c", payload="third"),
        ]
        results = asyncio.run(executor.execute(tasks))
        assert results[0].task_id == "a"
        assert results[1].task_id == "b"
        assert results[2].task_id == "c"


class TestRunMapReduce:
    def test_convenience_function(self):
        import asyncio
        tasks = [SubTask(task_id="t1", payload="hello")]
        results = asyncio.run(run_map_reduce(tasks, handler=lambda x: x))
        assert len(results) == 1
        assert results[0].status == SubTaskStatus.COMPLETED
        assert results[0].output == "hello"
