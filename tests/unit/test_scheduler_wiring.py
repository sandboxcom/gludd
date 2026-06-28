"""Regression tests for the scheduler being WIRED into the event loop.

Review DEFECT 0: ``TodoScheduler`` was defined but never invoked — no call site
in ``PHASE_ORDER``/``_run_phases``/daemon — so SCHEDULED todos were never
promoted and the cron feature was dead end-to-end despite green scheduler unit
tests. These tests assert the wiring itself: the phase is registered, ordered
before claiming, and actually drives ``TodoScheduler.tick`` against the
tick-scoped repo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop
from general_ludd.schemas.todo import TodoStatus


def test_run_scheduler_registered_before_claim() -> None:
    """The scheduler phase must exist in PHASE_ORDER and run before todos are
    claimed, so a promoted one-shot / spawned cron child is claimable same tick."""
    assert "run_scheduler" in PHASE_ORDER
    assert PHASE_ORDER.index("run_scheduler") < PHASE_ORDER.index(
        "claim_runnable_todos"
    )


def test_run_scheduler_phase_method_exists() -> None:
    """_run_phases does getattr(self, f'_phase_{name}') — the method must exist
    or every tick AttributeErrors."""
    assert callable(getattr(EventLoop, "_phase_run_scheduler", None))


@pytest.mark.asyncio
async def test_phase_run_scheduler_promotes_due_oneshot() -> None:
    """_phase_run_scheduler drives TodoScheduler.tick against the tick-scoped
    repo: a due one-shot SCHEDULED todo is transitioned SCHEDULED→QUEUED."""
    now = datetime.now(UTC)

    class _Due:
        todo_id = "TODO-DUE1"
        version = 1
        schedule_paused = False
        cron = None
        scheduled_at = now - timedelta(minutes=1)
        run_count = 0
        max_runs = None
        schedule_timezone = "UTC"

    repo = AsyncMock()
    repo.list_due_scheduled = AsyncMock(return_value=[_Due()])
    repo.transition = AsyncMock()

    # Bypass the heavy EventLoop.__init__; exercise just the phase body.
    loop = EventLoop.__new__(EventLoop)
    loop._todo_repo = repo
    loop._active_session = object()
    loop._tick_metrics = {}

    await loop._phase_run_scheduler()

    repo.transition.assert_awaited_once()
    call_args = repo.transition.await_args.args
    assert call_args[0] == "TODO-DUE1"
    assert call_args[1] == TodoStatus.QUEUED
    assert loop._tick_metrics["scheduled_promoted"] == 1
    assert loop._tick_metrics["scheduled_spawned"] == 0


@pytest.mark.asyncio
async def test_phase_run_scheduler_noops_without_repo() -> None:
    """No DB session/repo on the tick → phase is a safe no-op (no crash)."""
    loop = EventLoop.__new__(EventLoop)
    loop._todo_repo = None
    loop._active_session = None
    loop._tick_metrics = {}
    await loop._phase_run_scheduler()  # must not raise
    assert "scheduled_promoted" not in loop._tick_metrics
