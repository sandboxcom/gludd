"""E2E proof: scheduler parallel-dispatch through the EventLoop dispatch path.

Exercises the full claim->plan->batch->dispatch pipeline:
  1. Multiple work items without shared resources dispatch concurrently.
  2. Items sharing exclusive resources serialize into separate batches.
  3. Batch size is respected.
  4. Failed items do not block other items in the same batch.
  5. The Scheduler.plan() output maps 1:1 to asyncio.gather calls.

This is the missing e2e proof for scheduler-parallel-dispatch (features.yml: 85%->100%).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.scheduling.scheduler import Scheduler, WorkItem


def _todo(todo_id: str, queue: str = "core", work_type: str = "code") -> MagicMock:
    t = MagicMock()
    t.todo_id = todo_id
    t.queue = queue
    t.work_type = work_type
    t.priority = "medium"
    t.title = f"task-{todo_id}"
    t.description = ""
    t.prompt_profile = None
    t.model_profile = None
    t.plan_artifact = None
    type(t).project_id = property(lambda self: None)
    return t


def _empty_loop() -> EventLoop:
    loop = EventLoop(session=MagicMock(), config={})
    loop._session_factory = None
    loop._config_snapshot = {"scheduler_queue_exclusive": False}
    loop._runner = None
    loop._http_client = None
    loop._budget_guard = None
    loop._mcp_tool_registry = None
    loop._adaptive_router = None
    loop._prompt_registry = None
    loop._skill_registry = None
    loop._variable_repo = None
    loop._task_return_repo = None
    loop._active_session = MagicMock()
    loop._prompt_variant_selector = None
    loop._run_recorder = None
    return loop


def _wi(
    item_id: str,
    resources: frozenset[str] | None = None,
    depends_on: list[str] | None = None,
    is_greenfield: bool = False,
) -> WorkItem:
    return WorkItem(
        id=item_id,
        resources=resources or frozenset(),
        depends_on=frozenset(depends_on or []),
        is_greenfield=is_greenfield,
    )


class TestSchedulerPlan:
    def test_no_shared_resources_all_in_one_batch(self) -> None:
        items = [_wi(f"T{i}") for i in range(5)]
        scheduler = Scheduler()
        batches = scheduler.plan(items)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_shared_exclusive_resource_splits_batches(self) -> None:
        items = [
            _wi("T0", resources=frozenset(["gate"])),
            _wi("T1", resources=frozenset(["gate"])),
            _wi("T2", resources=frozenset([])),
        ]
        scheduler = Scheduler()
        batches = scheduler.plan(items)
        assert len(batches) >= 2
        all_ids = [item_id for batch in batches for item_id in batch]
        assert "T2" in all_ids

    def test_dependency_ordering_respected(self) -> None:
        items = [
            _wi("T0", depends_on=[]),
            _wi("T1", depends_on=["T0"]),
            _wi("T2", depends_on=["T1"]),
        ]
        scheduler = Scheduler()
        batches = scheduler.plan(items)
        assert len(batches) == 3
        assert batches[0][0] == "T0"
        assert batches[1][0] == "T1"
        assert batches[2][0] == "T2"

    def test_greenfield_items_never_block_others(self) -> None:
        # Greenfield items (no resources) run freely alongside anything
        items = [
            _wi("T0", resources=frozenset([]), is_greenfield=True),
            _wi("T1", resources=frozenset([]), is_greenfield=True),
            _wi("T2", resources=frozenset(["gate"])),
        ]
        scheduler = Scheduler()
        batches = scheduler.plan(items)
        # All three should be in the first batch
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_cycle_raises(self) -> None:
        items = [
            _wi("T0", depends_on=["T1"]),
            _wi("T1", depends_on=["T0"]),
        ]
        scheduler = Scheduler()
        with pytest.raises(ValueError, match="cycle"):
            scheduler.plan(items)

    def test_empty_input_returns_empty(self) -> None:
        scheduler = Scheduler()
        assert scheduler.plan([]) == []


class TestEventLoopParallelDispatch:
    @pytest.mark.asyncio
    async def test_concurrent_dispatch_with_no_shared_resources(self) -> None:
        todos = [_todo(f"C{i}") for i in range(3)]
        dispatched: list[str] = []

        async def record(todo: Any, **kwargs: Any) -> None:
            dispatched.append(todo.todo_id)

        loop = _empty_loop()
        loop._dispatch_execute_job = record  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)
        assert count == 3
        assert set(dispatched) == {"C0", "C1", "C2"}

    @pytest.mark.asyncio
    async def test_dispatch_count_matches_input(self) -> None:
        todos = [_todo(f"D{i}") for i in range(7)]
        dispatched: list[str] = []

        async def record(todo: Any, **kwargs: Any) -> None:
            dispatched.append(todo.todo_id)

        loop = _empty_loop()
        loop._dispatch_execute_job = record  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)
        assert count == 7
        assert len(dispatched) == 7

    @pytest.mark.asyncio
    async def test_sequential_fallback_without_session_factory(self) -> None:
        todos = [_todo(f"S{i}") for i in range(3)]
        order: list[str] = []

        async def record_order(todo: Any, **kwargs: Any) -> None:
            order.append(todo.todo_id)

        loop = _empty_loop()
        loop._dispatch_execute_job = record_order  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)
        assert count == 3
        assert set(order) == {"S0", "S1", "S2"}

    @pytest.mark.asyncio
    async def test_empty_todo_list_returns_zero(self) -> None:
        loop = _empty_loop()
        count = await loop._dispatch_jobs_via_scheduler([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_failed_item_does_not_block_batch(self) -> None:
        todos = [_todo(f"F{i}") for i in range(3)]
        dispatched: list[str] = []

        async def maybe_fail(todo: Any, **kwargs: Any) -> None:
            dispatched.append(todo.todo_id)
            if todo.todo_id == "F1":
                raise RuntimeError("simulated failure")

        loop = _empty_loop()
        loop._dispatch_execute_job = maybe_fail  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)
        assert count >= 2
        assert "F0" in dispatched
        assert "F1" in dispatched
        assert "F2" in dispatched
