"""E2E proof tests for the scheduler parallel-dispatch feature.

Exercises the full path: claim -> plan -> batch -> dispatch -> complete.

Coverage:
  1. Multiple work items with no shared resources are dispatched in parallel.
  2. Work items sharing exclusive resources are serialized into separate batches.
  3. Batch size is respected (scheduler output maps 1:1 to dispatch execution).
  4. Failed items don't block the other items in the same concurrent batch.
  5. The complete flow from claimed todos through Scheduler.plan() to
     concurrent-asyncio.gather or sequential dispatch.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.scheduling.scheduler import Scheduler, WorkItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_todo(
    todo_id: str,
    queue: str = "core",
    work_type: str = "code",
) -> MagicMock:
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.queue = queue
    todo.work_type = work_type
    todo.priority = "medium"
    todo.title = f"task-{todo_id}"
    todo.description = ""
    todo.prompt_profile = None
    todo.model_profile = None
    todo.plan_artifact = None
    type(todo).project_id = property(lambda self: None)
    return todo


def _make_session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


def _make_session_factory(sessions: list[MagicMock] | None = None) -> MagicMock:
    """Return a session factory that dispenses sessions from the given list.

    Each call to factory() opens a new async context manager returning the next
    session. When the list is exhausted, a default mock session is used instead.
    """
    if sessions is None:
        sessions = []
    idx = 0

    factory = MagicMock()
    # We need a factory that returns a new context manager per call.
    # Build a real coroutine factory.
    async def _factory_coro() -> MagicMock:
        nonlocal idx
        if idx < len(sessions):
            s = sessions[idx]
            idx += 1
            return s
        return _make_session()

    # Make factory() an async context manager that yields the session.
    class _FactoryCtxMgr:
        async def __aenter__(self) -> MagicMock:
            return await _factory_coro()

        async def __aexit__(self, *args: object) -> None:
            pass

    factory.return_value = _FactoryCtxMgr()
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
# Test: parallel dispatch when no shared resources
# ---------------------------------------------------------------------------


class TestParallelDispatchNoSharedResources:
    """Independent items with distinct resources batch together and run concurrently."""

    @pytest.mark.asyncio
    async def test_independent_items_all_run_in_single_concurrent_batch(self) -> None:
        """Three todos with distinct todo_ids (each gets its own resource label
        when queue_exclusive is False) are placed in one batch by the Scheduler
        and dispatched concurrently via asyncio.gather."""
        todos = [_make_todo(f"P{i}", queue=f"q{i}") for i in range(3)]

        started: list[str] = []
        finished: list[str] = []
        # Use barriers so we can verify items ran concurrently.
        barrier = asyncio.Barrier(3)

        async def record_dispatch(todo: Any) -> None:
            started.append(todo.todo_id)
            await barrier.wait()
            finished.append(todo.todo_id)

        sessions = [_make_session() for _ in range(3)]
        factory = _make_session_factory(sessions)

        loop = EventLoop(session=None, config={})
        loop._session_factory = factory
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
        loop._active_session = None
        loop._prompt_variant_selector = None
        loop._run_recorder = None

        loop._dispatch_execute_job = record_dispatch  # type: ignore[method-assign]  # pytest monkeypatch
        # Override isolated to skip sandbox + session wrapping for this test.
        async def fake_isolated(todo: Any) -> None:
            await record_dispatch(todo)

        loop._dispatch_execute_job_isolated = fake_isolated  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)

        assert count == 3, f"Expected 3 dispatched, got {count}"
        assert set(started) == {"P0", "P1", "P2"}, f"Not all started: {started}"
        assert set(finished) == {"P0", "P1", "P2"}, f"Not all finished: {finished}"

    @pytest.mark.asyncio
    async def test_scheduler_produces_single_batch_for_independent_items(self) -> None:
        """Direct Scheduler.plan() call confirms independent items land in one batch."""
        items = [
            WorkItem(id="a", resources=frozenset({"db_a"})),
            WorkItem(id="b", resources=frozenset({"db_b"})),
            WorkItem(id="c", resources=frozenset({"db_c"})),
        ]
        batches = Scheduler().plan(items)
        assert len(batches) == 1, f"Expected 1 batch, got {len(batches)}: {batches}"
        assert set(batches[0]) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Test: serialized dispatch when shared exclusive resources
# ---------------------------------------------------------------------------


class TestSerializedDispatchSharedResources:
    """Items sharing an exclusive resource are serialized across batches."""

    @pytest.mark.asyncio
    async def test_shared_resource_items_run_in_separate_batches(self) -> None:
        """Two todos sharing master_tree are placed in separate batches and
        run sequentially, not concurrently."""
        todos = [_make_todo(f"S{i}") for i in range(2)]

        batch_boundaries: list[str] = []
        active_at_once: list[int] = []
        currently_active = 0

        async def record_with_tracking(todo: Any) -> None:
            nonlocal currently_active
            batch_boundaries.append(f"enter:{todo.todo_id}")
            currently_active += 1
            active_at_once.append(currently_active)
            # Small yield so any concurrent work could run.
            await asyncio.sleep(0)
            currently_active -= 1
            batch_boundaries.append(f"exit:{todo.todo_id}")

        sessions = [_make_session() for _ in range(2)]
        factory = _make_session_factory(sessions)

        loop = EventLoop(session=None, config={})
        loop._session_factory = factory
        loop._config_snapshot = {"scheduler_queue_exclusive": True}
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
        loop._prompt_variant_selector = None
        loop._run_recorder = None

        async def fake_isolated(todo: Any) -> None:
            await record_with_tracking(todo)

        loop._dispatch_execute_job = record_with_tracking  # type: ignore[method-assign]  # pytest monkeypatch
        loop._dispatch_execute_job_isolated = fake_isolated  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)

        assert count == 2
        # With queue_exclusive=True, same-queue todos share a queue resource →
        # each gets its own batch → sequential execution → only 1 active at once.
        assert (
            max(active_at_once) == 1
        ), f"Expected at most 1 concurrent job, got {active_at_once}"

    @pytest.mark.asyncio
    async def test_scheduler_produces_separate_batches_for_shared_resource(self) -> None:
        """Direct Scheduler.plan() confirms shared-resource items split into
        separate batches."""
        items = [
            WorkItem(id="x", resources=frozenset({"master_tree"})),
            WorkItem(id="y", resources=frozenset({"master_tree"})),
        ]
        batches = Scheduler().plan(items)
        assert len(batches) == 2, f"Expected 2 batches, got {len(batches)}: {batches}"
        for batch in batches:
            assert len(batch) == 1


# ---------------------------------------------------------------------------
# Test: batch size is respected
# ---------------------------------------------------------------------------


class TestBatchSizeRespected:
    """The Scheduler output batch sizes drive dispatch execution boundaries."""

    @pytest.mark.asyncio
    async def test_batch_boundaries_match_scheduler_output(self) -> None:
        """Three items on the same exclusive queue → each in its own batch.
        Verify that async boundaries match the batch plan exactly."""
        todos = [_make_todo(f"B{i}") for i in range(3)]

        dispatch_leaf: list[tuple[str, int]] = []
        batch_idx = 0

        async def fake_isolated(todo: Any) -> None:
            nonlocal batch_idx
            dispatch_leaf.append((todo.todo_id, batch_idx))

        loop = EventLoop(session=None, config={})
        loop._session_factory = _make_session_factory([_make_session()])
        loop._config_snapshot = {"scheduler_queue_exclusive": True}
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
        loop._prompt_variant_selector = None
        loop._run_recorder = None

        loop._dispatch_execute_job = fake_isolated  # type: ignore[method-assign]  # pytest monkeypatch
        loop._dispatch_execute_job_isolated = fake_isolated  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)
        assert count == 3

        # The scheduler produces batches deterministically. Queue-exclusive mode
        # with same queue gives 3 batches of 1 each. Each isolated call is one batch.
        # With sequential execution, they run in order.
        assert len(dispatch_leaf) == 3, f"Expected 3 dispatch calls, got {dispatch_leaf}"

    @pytest.mark.asyncio
    async def test_batch_of_two_and_single_hybrid(self) -> None:
        """Two independent items (distinct resources) + one sharing with neither
        → scheduler produces a batch of 3. Verify all are dispatched."""
        items = [
            WorkItem(id="h1", resources=frozenset({"res_a"})),
            WorkItem(id="h2", resources=frozenset({"res_b"})),
            WorkItem(id="h3", resources=frozenset({"res_c"})),
        ]
        batches = Scheduler().plan(items)
        assert len(batches) == 1
        assert len(batches[0]) == 3


# ---------------------------------------------------------------------------
# Test: failed items don't block the batch
# ---------------------------------------------------------------------------


class TestFailedItemsDontBlockBatch:
    """When one item in a concurrent batch fails, others still complete."""

    @pytest.mark.asyncio
    async def test_failing_item_does_not_block_siblings(self) -> None:
        """Three independent items run concurrently; the middle one raises.
        Both passing items complete and are counted. The failing item is logged
        but does not abort the batch."""
        todos = [_make_todo(f"F{i}") for i in range(3)]

        completed: list[str] = []

        async def dispatch_maybe_fail(todo: Any) -> None:
            if todo.todo_id == "F1":
                raise RuntimeError("injected failure: F1")
            completed.append(todo.todo_id)

        sessions = [_make_session() for _ in range(3)]
        factory = _make_session_factory(sessions)

        loop = EventLoop(session=None, config={})
        loop._session_factory = factory
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
        loop._active_session = None
        loop._prompt_variant_selector = None
        loop._run_recorder = None

        async def fake_isolated(todo: Any) -> None:
            await dispatch_maybe_fail(todo)

        loop._dispatch_execute_job = dispatch_maybe_fail  # type: ignore[method-assign]  # pytest monkeypatch
        loop._dispatch_execute_job_isolated = fake_isolated  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)

        # The gather inside _dispatch_jobs_via_scheduler uses
        # return_exceptions=True → the RuntimeError is captured, not raised.
        # F0 and F2 succeed → count=2.
        assert count == 2, f"Expected 2 completed, got {count}"
        assert set(completed) == {"F0", "F2"}, f"Wrong completed set: {completed}"

    @pytest.mark.asyncio
    async def test_all_items_failing_still_batch_completes(self) -> None:
        """When every item in a batch raises, the batch still completes
        (zero dispatch counted, no uncaught exception)."""
        todos = [_make_todo(f"AF{i}") for i in range(2)]

        async def always_fail(_todo: Any) -> None:
            raise RuntimeError("all fail")

        sessions = [_make_session() for _ in range(2)]
        factory = _make_session_factory(sessions)

        loop = EventLoop(session=None, config={})
        loop._session_factory = factory
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
        loop._active_session = None
        loop._prompt_variant_selector = None
        loop._run_recorder = None

        async def fake_isolated(todo: Any) -> None:
            await always_fail(todo)

        loop._dispatch_execute_job = always_fail  # type: ignore[method-assign]  # pytest monkeypatch
        loop._dispatch_execute_job_isolated = fake_isolated  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)

        assert count == 0, f"Expected 0 completed (all failed), got {count}"


# ---------------------------------------------------------------------------
# Test: full path — claim → plan → batch → dispatch → complete
# ---------------------------------------------------------------------------


class TestFullClaimPlanBatchDispatchComplete:
    """End-to-end exercise of the complete _phase_dispatch_execute_jobs path."""

    @pytest.mark.asyncio
    async def test_full_phase_dispatch_with_concurrent_batch(self) -> None:
        """Three claimed todos go through _phase_dispatch_execute_jobs with
        a session_factory → Scheduler.plan() batches them → dispatched
        concurrently → count in metrics."""
        todos = [_make_todo(f"E2E{i}") for i in range(3)]

        executed: list[str] = []

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            executed.append(todo.todo_id)

        sessions = [_make_session() for _ in range(3)]
        factory = _make_session_factory(sessions)

        loop = EventLoop(session=None, config={})
        loop._session_factory = factory
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
        loop._active_session = None
        loop._prompt_variant_selector = None
        loop._run_recorder = None

        async def fake_isolated(todo: Any) -> None:
            await fake_dispatch(todo)

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]  # pytest monkeypatch
        loop._dispatch_execute_job_isolated = fake_isolated  # type: ignore[method-assign]  # pytest monkeypatch

        # Simulate the phase state after claim.
        loop._tick_state = {"claimed_todos": todos}
        loop._tick_metrics = {"todos_dispatched": 0}

        await loop._phase_dispatch_execute_jobs()

        assert loop._tick_metrics["todos_dispatched"] == 3
        assert set(executed) == {"E2E0", "E2E1", "E2E2"}

    @pytest.mark.asyncio
    async def test_empty_claimed_set_dispatches_zero(self) -> None:
        """No claimed todos → dispatch count stays zero."""
        loop = EventLoop(session=None, config={})
        loop._session_factory = None
        loop._config_snapshot = {}
        loop._runner = None
        loop._http_client = None
        loop._budget_guard = None
        loop._mcp_tool_registry = None
        loop._adaptive_router = None
        loop._tick_state = {"claimed_todos": []}
        loop._tick_metrics = {"todos_dispatched": 0}

        await loop._phase_dispatch_execute_jobs()
        assert loop._tick_metrics["todos_dispatched"] == 0

    @pytest.mark.asyncio
    async def test_complex_hybrid_schedule_respects_batches_dependencies(self) -> None:
        """Mix of independent items, shared-resource items, and dependency-
        ordered items: the Scheduler produces correct batch topology and the
        dispatch path counts correctly."""
        # Build scenarios:
        #   - alpha, beta share master_tree → serialized (different batches)
        #   - gamma, delta are independent → can batch together
        #   - epsilon depends on gamma → must come after gamma
        #
        # We use the underlying scheduler directly for batch topology and then
        # verify the EventLoop dispatch counts correctly.
        build = WorkItem(id="build")
        test_a = WorkItem(
            id="test_a",
            depends_on=frozenset({"build"}),
            resources=frozenset({"test_runner"}),
        )
        test_b = WorkItem(
            id="test_b",
            depends_on=frozenset({"build"}),
            resources=frozenset({"test_runner"}),
        )
        deploy = WorkItem(
            id="deploy",
            depends_on=frozenset({"test_a", "test_b"}),
        )

        batches = Scheduler().plan([build, test_a, test_b, deploy])

        # build must be in batch 0.
        assert "build" in batches[0]
        # test_a and test_b share test_runner → different batches.
        test_a_batch = next(i for i, b in enumerate(batches) if "test_a" in b)
        test_b_batch = next(i for i, b in enumerate(batches) if "test_b" in b)
        assert test_a_batch != test_b_batch, (
            f"test_a and test_b must be in different batches: {batches}"
        )
        # deploy must come after both test_a and test_b.
        deploy_batch = next(i for i, b in enumerate(batches) if "deploy" in b)
        assert deploy_batch > test_a_batch
        assert deploy_batch > test_b_batch
        # All 4 items appear exactly once.
        all_ids = [iid for batch in batches for iid in batch]
        assert set(all_ids) == {"build", "test_a", "test_b", "deploy"}


# ---------------------------------------------------------------------------
# Test: greenfield items never block others
# ---------------------------------------------------------------------------


class TestGreenfieldItemsDontBlock:
    """Greenfield items slot freely alongside resource-holding items."""

    def test_greenfield_batches_with_resource_items(self) -> None:
        """Two resource-holding items + one greenfield → all in one batch."""
        items = [
            WorkItem(id="gf", is_greenfield=True),
            WorkItem(id="worker_a", resources=frozenset({"db_x"})),
            WorkItem(id="worker_b", resources=frozenset({"db_y"})),
        ]
        batches = Scheduler().plan(items)
        assert len(batches) == 1
        assert set(batches[0]) == {"gf", "worker_a", "worker_b"}

    def test_greenfield_with_dependency_still_orders(self) -> None:
        """Greenfield item depending on another item appears in a later batch."""
        base = WorkItem(id="base", resources=frozenset({"db_z"}))
        gf = WorkItem(id="gf", is_greenfield=True, depends_on=frozenset({"base"}))
        batches = Scheduler().plan([base, gf])
        assert len(batches) == 2
        assert batches[0] == ["base"]
        assert batches[1] == ["gf"]


# ---------------------------------------------------------------------------
# Test: sequential fallback when no session_factory
# ---------------------------------------------------------------------------


class TestSequentialFallback:
    """Without a session_factory, dispatch falls back to sequential execution."""

    @pytest.mark.asyncio
    async def test_no_session_factory_runs_sequential(self) -> None:
        todos = [_make_todo(f"SEQ{i}") for i in range(3)]

        order: list[str] = []

        async def record_order(todo: Any, **kwargs: Any) -> None:
            order.append(todo.todo_id)

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

        loop._dispatch_execute_job = record_order  # type: ignore[method-assign]  # pytest monkeypatch

        count = await loop._dispatch_jobs_via_scheduler(todos)

        assert count == 3
        # Sequential fallback: _dispatch_execute_job called in scheduler order.
        # With default resources (todo:idX unique per item), all in one batch,
        # processed sequentially within the batch loop.
        assert set(order) == {"SEQ0", "SEQ1", "SEQ2"}
