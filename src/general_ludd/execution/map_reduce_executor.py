"""AG.11: Map-reduce graph patterns using LangGraph fan-out (Send API).

Provides a ``MapReduceExecutor`` that takes a list of sub-tasks, fans them
out to parallel execution nodes via LangGraph's Send API, and collects results
in a reducer node.

Features:
- Fan-out via :func:`langgraph.types.Send` to parallel executor nodes
- Per-task timeout (sub-tasks killed, others continue)
- Partial failure handling (one task fails, others complete)
- Graph builder accessible via :func:`map_reduce_builder`
- Sync convenience function :func:`run_map_reduce`
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langgraph.graph import StateGraph
from langgraph.types import Send

HandlerFn = Callable[[Any], Any]


class SubTaskStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class SubTask:
    task_id: str
    payload: Any = None
    timeout: float | None = None
    max_retries: int = 0


@dataclass
class SubTaskResult:
    task_id: str
    status: SubTaskStatus
    output: str | None = None
    error: str | None = None


def _results_reducer(
    left: list[SubTaskResult],
    right: list[SubTaskResult],
) -> list[SubTaskResult]:
    return left + right


class _MapReduceState(TypedDict, total=False):
    tasks: list[SubTask]
    results: Annotated[list[SubTaskResult], _results_reducer]


def map_reduce_builder(
    handler: HandlerFn,
) -> StateGraph:
    """Build a compiled LangGraph StateGraph with map-reduce fan-out.

    Uses LangGraph's Send API to fan out sub-tasks from ``_dispatcher``
    to parallel ``_worker`` nodes via a conditional edge, then collects
    results in ``_reducer``.

    State channels (TypedDict via reducer annotations):
    - ``tasks``: list of ``SubTask`` (overwrite reducer).
    - ``results``: list of ``SubTaskResult`` (append reducer).
    - ``_current_task``: the SubTask assigned to this worker invocation.
    """
    builder = StateGraph(_MapReduceState)

    async def _worker(state: _MapReduceState) -> dict[str, Any]:
        task = state.get("_current_task")
        if task is None:
            return {"results": [
                SubTaskResult(
                    task_id="unknown",
                    status=SubTaskStatus.FAILED,
                    error="No _current_task in worker state",
                )
            ]}

        results: list[SubTaskResult] = []
        try:
            if task.timeout is not None:
                raw = await asyncio.wait_for(
                    _invoke_handler(handler, task.payload),
                    timeout=task.timeout,
                )
            else:
                raw = await _invoke_handler(handler, task.payload)
            results = [
                SubTaskResult(
                    task_id=task.task_id,
                    status=SubTaskStatus.COMPLETED,
                    output=str(raw) if raw is not None else "",
                )
            ]
        except TimeoutError:
            results = [
                SubTaskResult(
                    task_id=task.task_id,
                    status=SubTaskStatus.TIMED_OUT,
                )
            ]
        except Exception as exc:
            results = [
                SubTaskResult(
                    task_id=task.task_id,
                    status=SubTaskStatus.FAILED,
                    error=str(exc),
                )
            ]

        return {"results": results}

    async def _dispatcher(state: _MapReduceState) -> dict[str, Any]:
        return {}

    def _route(state: _MapReduceState) -> list[Send]:
        return [
            Send("_worker", {"_current_task": task})
            for task in state["tasks"]
        ]

    async def _reducer(state: _MapReduceState) -> dict[str, Any]:
        return {}

    builder.add_node("_dispatcher", _dispatcher)
    builder.add_node("_worker", _worker)
    builder.add_node("_reducer", _reducer)

    builder.set_entry_point("_dispatcher")
    builder.add_conditional_edges("_dispatcher", _route, ["_worker"])
    builder.add_edge("_worker", "_reducer")
    builder.set_finish_point("_reducer")

    return builder.compile()


async def _invoke_handler(handler: HandlerFn, payload: Any) -> Any:
    result = handler(payload)
    if asyncio.iscoroutine(result):
        result = await result
    return result


class MapReduceExecutor:
    """Execute a list of SubTasks in parallel using asyncio.gather.

    Does NOT require a running LangGraph runtime — uses asyncio directly
    for parallel execution with per-task timeouts and partial-failure
    handling.  For the full LangGraph Send-API fan-out, use
    :func:`map_reduce_builder` and invoke the compiled graph.

    Attributes:
        handler: Async or sync callable that processes a single payload.
        default_timeout: Per-task timeout when none is set on the SubTask.
    """

    def __init__(
        self,
        handler: HandlerFn,
        default_timeout: float | None = None,
    ) -> None:
        self._handler = handler
        self._default_timeout = default_timeout

    async def execute(self, tasks: list[SubTask]) -> list[SubTaskResult]:
        """Run all tasks in parallel and return one result per task.

        Args:
            tasks: The sub-tasks to execute.

        Returns:
            A list of SubTaskResult, one per input task, preserving
            the input order.
        """

        async def _run_one(task: SubTask) -> SubTaskResult:
            timeout = task.timeout if task.timeout is not None else self._default_timeout
            try:
                if timeout is not None:
                    raw = await asyncio.wait_for(
                        _invoke_handler(self._handler, task.payload),
                        timeout=timeout,
                    )
                else:
                    raw = await _invoke_handler(self._handler, task.payload)
                return SubTaskResult(
                    task_id=task.task_id,
                    status=SubTaskStatus.COMPLETED,
                    output=str(raw) if raw is not None else "",
                )
            except TimeoutError:
                return SubTaskResult(
                    task_id=task.task_id,
                    status=SubTaskStatus.TIMED_OUT,
                )
            except Exception as exc:
                return SubTaskResult(
                    task_id=task.task_id,
                    status=SubTaskStatus.FAILED,
                    error=str(exc),
                )

        coros = [_run_one(t) for t in tasks]
        return list(await asyncio.gather(*coros))


async def run_map_reduce(
    tasks: list[SubTask],
    handler: HandlerFn,
    timeout: float | None = None,
) -> list[SubTaskResult]:
    """Convenience wrapper around :class:`MapReduceExecutor`.

    Args:
        tasks: Sub-tasks to fan out.
        handler: Processing function for each sub-task's payload.
        timeout: Default per-task timeout in seconds.
    """
    executor = MapReduceExecutor(handler=handler, default_timeout=timeout)
    return await executor.execute(tasks)
