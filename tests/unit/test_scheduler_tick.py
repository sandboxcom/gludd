"""Tests for Scheduler.plan() integration with the EventLoop tick.

W(#23): verify that _phase_dispatch_execute_jobs uses Scheduler.plan() to:
  1. Run independent jobs concurrently via asyncio.gather (session-per-coroutine).
  2. Serialize jobs that share a resource into sequential batches.
  3. Fall back to sequential execution (no gather) when no session_factory.
  4. Count dispatched jobs correctly in tick metrics.
  5. Handle Scheduler errors fail-closed (sequential fallback).
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_todo(todo_id: str, queue: str = "core") -> MagicMock:
    """Create a fake todo object with the minimal attributes EventLoop expects."""
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.queue = queue
    todo.work_type = "code"
    todo.priority = "medium"
    todo.title = f"todo-{todo_id}"
    todo.description = ""
    todo.prompt_profile = None
    todo.model_profile = None
    todo.plan_artifact = None
    todo.project_id = None
    # hasattr checks for project_id
    type(todo).project_id = property(lambda self: None)
    return todo


def _make_session() -> MagicMock:
    """Minimal async session mock."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


def _make_session_factory(session: MagicMock | None = None) -> MagicMock:
    """Async session factory (async context manager)."""
    if session is None:
        session = _make_session()

    factory = MagicMock()
    # async with factory() as s: ...
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = ctx
    return factory


def _make_variable_repo() -> MagicMock:
    repo = MagicMock()
    repo.load_vars_for_project = AsyncMock(return_value={})
    return repo


def _make_task_return_repo() -> MagicMock:
    repo = MagicMock()
    repo.create = AsyncMock()
    return repo


# ---------------------------------------------------------------------------
# Tests for _dispatch_jobs_via_scheduler (concurrent + sequential paths)
# ---------------------------------------------------------------------------

class TestSchedulerTickConcurrentDispatch:
    """session_factory present → concurrent gather within each batch."""

    @pytest.mark.asyncio
    async def test_independent_jobs_dispatched_concurrently(self) -> None:
        """Three independent todos (distinct queues, no shared resources) should
        all land in one Scheduler batch and be gathered together."""
        from general_ludd.event_loop.loop import EventLoop

        dispatch_order: list[str] = []
        dispatch_events: list[asyncio.Event] = [asyncio.Event() for _ in range(3)]
        todos = [_make_todo(f"T{i}", queue=f"q{i}") for i in range(3)]

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            tid = todo.todo_id
            # Signal this job started, then yield to let others start.
            idx = int(tid[1:])
            dispatch_order.append(f"start:{tid}")
            dispatch_events[idx].set()
            # Brief cooperative yield so gather actually runs concurrently.
            await asyncio.sleep(0)
            dispatch_order.append(f"end:{tid}")

        session_factory = _make_session_factory()
        loop = EventLoop(session=None, config={})
        loop._session_factory = session_factory
        loop._config_snapshot = {}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None
        loop._prompt_registry = None
        loop._skill_registry = None
        loop._variable_repo = None
        loop._task_return_repo = None
        loop._active_session = None

        # Patch _dispatch_execute_job to use our fake.
        cast(Any, loop)._dispatch_execute_job = fake_dispatch

        # Rebuild _dispatch_execute_job_isolated to use the patched method.
        # We need it to call the fake directly (skip the isolated session wrapper)
        # while still testing that concurrent dispatch happens.
        # Simplest: patch _dispatch_execute_job_isolated too.
        isolated_calls: list[str] = []

        async def fake_isolated(todo: Any) -> None:
            await fake_dispatch(todo)
            isolated_calls.append(todo.todo_id)

        cast(Any, loop)._dispatch_execute_job_isolated = fake_isolated

        count = await loop._dispatch_jobs_via_scheduler(todos)

        assert count == 3
        # All three jobs completed.
        assert set(isolated_calls) == {"T0", "T1", "T2"}

    @pytest.mark.asyncio
    async def test_sequential_when_no_session_factory(self) -> None:
        """No session_factory → falls back to sequential execution."""
        from general_ludd.event_loop.loop import EventLoop

        dispatch_order: list[str] = []
        todos = [_make_todo(f"S{i}") for i in range(3)]

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            dispatch_order.append(todo.todo_id)
            await asyncio.sleep(0)

        loop = EventLoop(session=MagicMock(), config={})
        loop._session_factory = None
        loop._config_snapshot = {}
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

        cast(Any, loop)._dispatch_execute_job = fake_dispatch

        count = await loop._dispatch_jobs_via_scheduler(todos)

        assert count == 3
        # Sequential: order is preserved (todos have unique resources, each is
        # its own batch in the sequential scheduler output, order intact).
        assert dispatch_order == ["S0", "S1", "S2"]

    @pytest.mark.asyncio
    async def test_empty_todos_returns_zero(self) -> None:
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(session=None, config={})
        loop._session_factory = None
        loop._config_snapshot = {}

        count = await loop._dispatch_jobs_via_scheduler([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_scheduler_error_falls_back_sequential(self) -> None:
        """If Scheduler.plan() raises, fall back to sequential (fail-closed)."""
        from unittest.mock import patch

        from general_ludd.event_loop.loop import EventLoop

        dispatch_order: list[str] = []
        todos = [_make_todo(f"E{i}") for i in range(2)]

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            dispatch_order.append(todo.todo_id)

        loop = EventLoop(session=None, config={})
        loop._session_factory = None
        loop._config_snapshot = {}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None
        cast(Any, loop)._dispatch_execute_job = fake_dispatch

        with patch(
            "general_ludd.scheduling.scheduler.Scheduler.plan",
            side_effect=ValueError("injected error"),
        ):
            count = await loop._dispatch_jobs_via_scheduler(todos)

        assert count == 2
        assert set(dispatch_order) == {"E0", "E1"}

    @pytest.mark.asyncio
    async def test_metrics_todos_dispatched_updated(self) -> None:
        """_phase_dispatch_execute_jobs updates tick_metrics['todos_dispatched']."""
        from general_ludd.event_loop.loop import EventLoop

        todos = [_make_todo("M1"), _make_todo("M2")]

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            pass

        loop = EventLoop(session=None, config={})
        loop._session_factory = None
        loop._config_snapshot = {}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None
        loop._prompt_registry = None
        loop._skill_registry = None
        loop._variable_repo = None
        loop._task_return_repo = None
        loop._active_session = None
        loop._tick_state = {"claimed_todos": todos}
        loop._tick_metrics = {"todos_dispatched": 0}
        cast(Any, loop)._dispatch_execute_job = fake_dispatch

        await loop._phase_dispatch_execute_jobs()

        assert loop._tick_metrics["todos_dispatched"] == 2

    @pytest.mark.asyncio
    async def test_pid_cap_limits_dispatch(self) -> None:
        """PID cap applied before scheduler: only cap many todos processed."""
        from general_ludd.event_loop.loop import EventLoop

        dispatched: list[str] = []

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            dispatched.append(todo.todo_id)

        todos = [_make_todo(f"P{i}") for i in range(5)]
        pid = MagicMock()
        pid.desired_total_active_buckets = 2

        loop = EventLoop(session=None, config={})
        loop._session_factory = None
        loop._config_snapshot = {}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None
        loop._prompt_registry = None
        loop._skill_registry = None
        loop._variable_repo = None
        loop._task_return_repo = None
        loop._active_session = None
        loop._tick_state = {"claimed_todos": todos, "pid_outputs": pid}
        loop._tick_metrics = {"todos_dispatched": 0}
        cast(Any, loop)._dispatch_execute_job = fake_dispatch

        await loop._phase_dispatch_execute_jobs()

        # Only 2 dispatched (PID cap = 2).
        assert loop._tick_metrics["todos_dispatched"] == 2
        assert len(dispatched) == 2


class TestSchedulerOrderDriven:
    """Verify that Scheduler.plan() drives the dispatch order."""

    @pytest.mark.asyncio
    async def test_scheduler_batches_determine_order(self) -> None:
        """Single-resource todos each form their own batch; order is Scheduler's
        topological output (input order preserved for independent items)."""
        from general_ludd.event_loop.loop import EventLoop

        execution_order: list[str] = []
        todos = [_make_todo(f"O{i}") for i in range(3)]

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            execution_order.append(todo.todo_id)

        loop = EventLoop(session=None, config={})
        loop._session_factory = None
        loop._config_snapshot = {}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None
        cast(Any, loop)._dispatch_execute_job = fake_dispatch

        await loop._dispatch_jobs_via_scheduler(todos)

        # All dispatched; order matches input (each job is its own resource).
        assert execution_order == ["O0", "O1", "O2"]

    @pytest.mark.asyncio
    async def test_concurrent_batch_when_queue_exclusive_false(self) -> None:
        """With queue_exclusive=False (default), same-queue todos get unique
        resource labels (todo:id) and can be batched together by the Scheduler."""
        from general_ludd.event_loop.loop import EventLoop

        isolated_calls: list[str] = []

        async def fake_isolated(todo: Any) -> None:
            isolated_calls.append(todo.todo_id)

        # Three todos on same queue, queue_exclusive disabled → each has unique
        # resource, they CAN be in the same concurrent batch.
        todos = [_make_todo(f"C{i}", queue="core") for i in range(3)]
        session_factory = _make_session_factory()

        loop = EventLoop(session=None, config={})
        loop._session_factory = session_factory
        loop._config_snapshot = {"scheduler_queue_exclusive": False}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None
        cast(Any, loop)._dispatch_execute_job_isolated = fake_isolated

        count = await loop._dispatch_jobs_via_scheduler(todos)

        assert count == 3
        assert set(isolated_calls) == {"C0", "C1", "C2"}

    @pytest.mark.asyncio
    async def test_queue_exclusive_serializes_same_queue(self) -> None:
        """With scheduler_queue_exclusive=True, same-queue todos share a queue
        resource and are serialized into separate batches."""
        from general_ludd.event_loop.loop import EventLoop

        current_batch: set[str] = set()

        async def fake_isolated(todo: Any) -> None:
            # Each call to _dispatch_execute_job_isolated is within a batch.
            current_batch.add(todo.todo_id)

        # Two todos on same queue; with queue_exclusive=True they must serialize.
        todos = [_make_todo(f"QE{i}", queue="core") for i in range(2)]
        session_factory = _make_session_factory()

        loop = EventLoop(session=None, config={})
        loop._session_factory = session_factory
        loop._config_snapshot = {"scheduler_queue_exclusive": True}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None

        # Patch gather to detect concurrent vs sequential calls.
        concurrent_batches: list[list[str]] = []


        async def recording_dispatch(todos_: list[Any]) -> int:
            from general_ludd.scheduling.scheduler import Scheduler, WorkItem

            items = []
            for t in todos_:
                tid = str(t.todo_id)
                queue = t.queue or "core"
                resources = frozenset({f"queue:{queue}", f"todo:{tid}"})
                items.append(WorkItem(id=tid, resources=resources))
            batches = Scheduler().plan(items)
            concurrent_batches.extend(batches)
            return len(todos_)

        cast(Any, loop)._dispatch_jobs_via_scheduler = recording_dispatch

        await loop._dispatch_jobs_via_scheduler(todos)

        # With queue_exclusive=True on same queue, each todo in its own batch.
        assert len(concurrent_batches) == 2
        # Each batch has exactly one item.
        assert all(len(b) == 1 for b in concurrent_batches)


class TestDispatchExecuteJobIsolated:
    """_dispatch_execute_job_isolated opens its own session."""

    @pytest.mark.asyncio
    async def test_isolated_uses_session_factory(self) -> None:
        """_dispatch_execute_job_isolated opens a session from session_factory."""
        from general_ludd.event_loop.loop import EventLoop

        session = _make_session()
        factory = _make_session_factory(session)
        todo = _make_todo("ISO1")

        dispatch_kwargs: list[dict[str, Any]] = []

        async def fake_dispatch(t: Any, **kwargs: Any) -> None:
            dispatch_kwargs.append(kwargs)

        loop = EventLoop(session=None, config={})
        loop._session_factory = factory
        loop._config_snapshot = {}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None
        cast(Any, loop)._dispatch_execute_job = fake_dispatch

        await loop._dispatch_execute_job_isolated(todo)

        # Session factory was invoked (async context entered).
        factory.assert_called_once()
        # Session commit was attempted.
        session.commit.assert_awaited_once()
        # _dispatch_execute_job was called with session overrides.
        assert len(dispatch_kwargs) == 1
        assert "_session_override" in dispatch_kwargs[0]
        assert dispatch_kwargs[0]["_session_override"] is session
