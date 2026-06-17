"""Unit tests for PID-cap enforcement at claim_runnable() call site.

The bug: _phase_dispatch_execute_jobs used to truncate the Python list of
claimed todos AFTER claim_runnable() had already written ACTIVE to the DB,
leaving ACTIVE-undispatched rows that accumulate until the 15-min reaper.

The fix: _phase_claim_runnable_todos now derives claim_limit from the PID cap
and passes it as limit= to claim_runnable(), so the cap is enforced at the
SELECT, before any DB write.

These tests confirm that claim_runnable is called with limit <= pid_cap,
regardless of the default hard_cap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_loop(**overrides):
    """Minimal EventLoop with mocked todo_repo and no session."""
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
        config={"tick_interval": 1.0},
        session=session,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, todo_repo


def _make_pid_outputs(desired: int) -> MagicMock:
    """Return a mock ControllerOutputs with desired_total_active_buckets = desired."""
    outputs = MagicMock()
    outputs.desired_total_active_buckets = desired
    return outputs


class TestSaturationClaimCap:
    """claim_runnable must be called with limit <= pid_cap when a cap is set."""

    @pytest.mark.asyncio
    async def test_no_pid_outputs_uses_default_limit(self):
        """Without PID outputs the default hard_cap (10) is used."""
        loop, todo_repo = _make_loop()
        todo_repo.claim_runnable.return_value = []

        # No pid_outputs in tick_state
        assert loop._tick_state.get("pid_outputs") is None

        await loop._phase_claim_runnable_todos()

        todo_repo.claim_runnable.assert_called_once()
        kwargs = todo_repo.claim_runnable.call_args.kwargs
        assert kwargs.get("limit", 10) == 10

    @pytest.mark.asyncio
    async def test_pid_cap_3_claim_limit_is_3(self):
        """When pid_cap=3, claim_runnable must be called with limit=3."""
        loop, todo_repo = _make_loop()
        todo_repo.claim_runnable.return_value = []
        loop._tick_state["pid_outputs"] = _make_pid_outputs(3)

        await loop._phase_claim_runnable_todos()

        todo_repo.claim_runnable.assert_called_once()
        kwargs = todo_repo.claim_runnable.call_args.kwargs
        assert kwargs["limit"] == 3

    @pytest.mark.asyncio
    async def test_pid_cap_1_claim_limit_is_1(self):
        """Minimal cap=1 is respected."""
        loop, todo_repo = _make_loop()
        todo_repo.claim_runnable.return_value = []
        loop._tick_state["pid_outputs"] = _make_pid_outputs(1)

        await loop._phase_claim_runnable_todos()

        kwargs = todo_repo.claim_runnable.call_args.kwargs
        assert kwargs["limit"] == 1

    @pytest.mark.asyncio
    async def test_pid_cap_larger_than_hard_cap_uses_hard_cap(self):
        """When pid_cap > hard_cap (10), hard_cap wins (tighter bound)."""
        loop, todo_repo = _make_loop()
        todo_repo.claim_runnable.return_value = []
        loop._tick_state["pid_outputs"] = _make_pid_outputs(50)

        await loop._phase_claim_runnable_todos()

        kwargs = todo_repo.claim_runnable.call_args.kwargs
        assert kwargs["limit"] <= 10

    @pytest.mark.asyncio
    async def test_pid_cap_0_claim_limit_is_0(self):
        """Zero cap means no rows should be claimed (controller wants idle)."""
        loop, todo_repo = _make_loop()
        todo_repo.claim_runnable.return_value = []
        loop._tick_state["pid_outputs"] = _make_pid_outputs(0)

        await loop._phase_claim_runnable_todos()

        kwargs = todo_repo.claim_runnable.call_args.kwargs
        assert kwargs["limit"] == 0

    @pytest.mark.asyncio
    async def test_claimed_todos_stored_in_tick_state(self):
        """Claimed todos are stored in tick_state regardless of cap path."""
        loop, todo_repo = _make_loop()
        todo = Todo(title="task", status=TodoStatus.QUEUED)
        todo_repo.claim_runnable.return_value = [todo]
        loop._tick_state["pid_outputs"] = _make_pid_outputs(5)

        await loop._phase_claim_runnable_todos()

        assert loop._tick_state["claimed_todos"] == [todo]

    @pytest.mark.asyncio
    async def test_project_id_forwarded_with_pid_cap(self):
        """project_id kwarg is still forwarded when a pid cap is in effect."""
        project = MagicMock()
        project.project_id = "proj-42"
        project_manager = MagicMock()
        project_manager.select_project.return_value = project
        loop, todo_repo = _make_loop(project_manager=project_manager)
        todo_repo.claim_runnable.return_value = []
        loop._tick_state["pid_outputs"] = _make_pid_outputs(4)

        await loop._phase_claim_runnable_todos()

        kwargs = todo_repo.claim_runnable.call_args.kwargs
        assert kwargs["limit"] == 4
        assert kwargs.get("project_id") == "proj-42"
