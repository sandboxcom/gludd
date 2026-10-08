"""End-to-end acceptance for S83.158 claim-before-provision semantics."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop
from general_ludd.execution.graph_checkpointer import TickCheckpointer
from general_ludd.schemas.todo import TodoStatus


class _ExecutionEnvironmentRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def reconcile_execution_environment(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return {"status": "successful", "rc": 0, "events": []}


class _ClaimProvisionAcceptanceLoop(EventLoop):
    """Run the real claim/provision phases through the production tick boundary."""

    async def _run_phase_range(self, start: int, end: int) -> None:
        for phase_name in PHASE_ORDER[start:end]:
            if phase_name == "claim_runnable_todos":
                await self._phase_claim_runnable_todos()
            elif phase_name == "reconcile_compute_demand":
                await self._phase_reconcile_compute_demand()


@pytest_asyncio.fixture
async def durable_session_factory(
    tmp_path: Path,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    database_path = tmp_path / "claim-before-provision.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}", echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _loop(
    factory: async_sessionmaker[AsyncSession],
    runner: _ExecutionEnvironmentRunner,
    project_root: Path,
    *,
    checkpointer: TickCheckpointer | None = None,
) -> _ClaimProvisionAcceptanceLoop:
    return _ClaimProvisionAcceptanceLoop(
        session=factory,
        runner=runner,
        daemon_state={},
        checkpointer=checkpointer,
        config={
            "repo_root": str(project_root),
            "execution_environment": {
                "machine_cpus": 2,
                "machine_memory_mb": 4096,
                "machine_disk_gb": 8,
            },
        },
    )


async def _seed_todo(
    factory: async_sessionmaker[AsyncSession],
    *,
    runnable: bool,
) -> str:
    async with factory() as session:
        repository = TodoRepository(session)
        todo = await repository.create({"title": "claim-before-provision acceptance"})
        if runnable:
            todo = await repository.transition(
                todo.todo_id,
                TodoStatus.QUEUED,
                expected_version=todo.version,
            )
        await session.commit()
        return todo.todo_id


def _checkpoint_has_claimed_todos(checkpointer: TickCheckpointer) -> bool:
    saved_tick = checkpointer.get("last_tick")
    if saved_tick is None:
        return False
    tick_state = saved_tick.get("_tick_state")
    return isinstance(tick_state, dict) and bool(tick_state.get("claimed_todos"))


@pytest.mark.asyncio
async def test_non_runnable_durable_work_never_provisions(
    durable_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _seed_todo(durable_session_factory, runnable=False)
    runner = _ExecutionEnvironmentRunner()

    await _loop(durable_session_factory, runner, tmp_path).tick()

    assert runner.calls == []


@pytest.mark.asyncio
async def test_two_workers_and_restart_provision_once_for_one_durable_claim(
    durable_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    todo_id = await _seed_todo(durable_session_factory, runnable=True)
    runner = _ExecutionEnvironmentRunner()
    checkpoints = [TickCheckpointer(), TickCheckpointer()]
    workers = [
        _loop(
            durable_session_factory,
            runner,
            tmp_path,
            checkpointer=checkpointer,
        )
        for checkpointer in checkpoints
    ]

    await asyncio.gather(*(worker.tick() for worker in workers))
    durable_claim_checkpoint = next(
        checkpointer
        for checkpointer in checkpoints
        if _checkpoint_has_claimed_todos(checkpointer)
    )
    await _loop(
        durable_session_factory,
        runner,
        tmp_path,
        checkpointer=durable_claim_checkpoint,
    ).tick()

    assert [call["state"] for call in runner.calls] == ["present"]
    async with durable_session_factory() as session:
        status = await session.scalar(
            select(TodoModel.status).where(TodoModel.todo_id == todo_id)
        )
    assert status == TodoStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_failed_claim_commit_cannot_provision_rolled_back_work(
    durable_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    todo_id = await _seed_todo(durable_session_factory, runnable=True)
    runner = _ExecutionEnvironmentRunner()
    loop = _loop(durable_session_factory, runner, tmp_path)
    original_commit = loop._commit_tick_session
    commit_attempts = 0

    async def fail_claim_commit(session: Any) -> bool:
        nonlocal commit_attempts
        commit_attempts += 1
        if commit_attempts == 1:
            await session.rollback()
            return False
        return await original_commit(session)

    with patch.object(loop, "_commit_tick_session", fail_claim_commit):
        await loop.tick()

    assert runner.calls == []
    async with durable_session_factory() as session:
        status = await session.scalar(
            select(TodoModel.status).where(TodoModel.todo_id == todo_id)
        )
    assert status == TodoStatus.QUEUED.value
