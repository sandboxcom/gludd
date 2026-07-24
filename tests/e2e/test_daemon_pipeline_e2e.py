"""E2E test: daemon pipeline claim_runnable → dispatch_execute_job.

Exercises the full pipeline: a QUEUED todo is claimed, dispatched via the
runner, and the tick cycle completes with the expected metrics and state.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop
from general_ludd.schemas.todo import TodoStatus

_PROJECT_ID = "proj-pipeline"


@pytest.fixture
def _engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    return engine


@pytest.fixture
async def _session_factory(_engine):
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await _engine.dispose()


@pytest.fixture
def _runner():
    runner = MagicMock()
    runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/test-daemon-pipeline"})
    runner.write_vars = MagicMock()
    runner.run_playbook = MagicMock()
    return runner


async def _seed_todo(factory, **overrides) -> TodoModel:
    async with factory() as session:
        repo = TodoRepository(session)
        defaults = {
            "todo_id": "PIPE-001",
            "title": "Pipeline E2E task",
            "description": "Verify claim->dispatch flow end-to-end",
            "queue": "core",
            "priority": 7,
            "work_type": "code",
            "status": TodoStatus.QUEUED.value,
            "project_id": _PROJECT_ID,
        }
        defaults.update(overrides)
        todo = await repo.create(defaults)
        await session.commit()
        return todo


def _make_loop(factory, runner):
    project_manager = MagicMock()
    project_manager.select_project.return_value = SimpleNamespace(
        project_id=_PROJECT_ID
    )
    loop = EventLoop(
        session=factory,
        runner=runner,
        task_return_repo=MagicMock(),
        config={"repo_root": "/tmp"},
        project_manager=project_manager,
    )
    loop._task_return_repo.claim_unreviewed = MagicMock(return_value=[])
    loop._runner = runner
    return loop


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------


class TestDaemonPipeline:
    @pytest.mark.asyncio
    async def test_claim_runnable_picks_queued_todo(self, _session_factory, _runner):
        await _seed_todo(_session_factory, todo_id="PIPE-CLAIM-1")
        loop = _make_loop(_session_factory, _runner)
        metrics = await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        assert any(t.todo_id == "PIPE-CLAIM-1" for t in claimed)
        assert metrics["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_dispatch_fires_runner_for_claimed_todo(self, _session_factory, _runner):
        await _seed_todo(_session_factory, todo_id="PIPE-DISP-1")
        loop = _make_loop(_session_factory, _runner)
        await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        write_calls = [
            c for c in _runner.write_vars.call_args_list
            if len(c[0]) >= 1 and isinstance(c[0][0], str) and c[0][0].startswith("EXEC-")
        ]
        assert len(write_calls) >= 1, "Expected at least one EXEC- write_vars call"

    @pytest.mark.asyncio
    async def test_full_cycle_metric_and_status(self, _session_factory, _runner):
        await _seed_todo(_session_factory, todo_id="PIPE-FULL-1", queue="core")
        loop = _make_loop(_session_factory, _runner)
        metrics = await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1
        pipe_todo = next((t for t in claimed if t.todo_id == "PIPE-FULL-1"), None)
        assert pipe_todo is not None
        assert metrics["phases_completed"] == len(PHASE_ORDER)
        assert isinstance(metrics["tick_duration_ms"], float)
        assert loop._tick_state.get("claimed_todos") is not None
