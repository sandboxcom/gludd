"""Canonical behavioral coverage for application-owned async tasks."""

from __future__ import annotations

import asyncio

import pytest

from general_ludd.util.async_lifecycle import cancel_and_drain_tasks


@pytest.mark.asyncio
async def test_cancels_awaits_and_unregisters_owned_task() -> None:
    started = asyncio.Event()

    async def worker() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    registry = {task}
    await started.wait()

    await cancel_and_drain_tasks(registry, registry=registry)

    assert task.cancelled()
    assert registry == set()


@pytest.mark.asyncio
async def test_completed_task_is_drained_without_rewriting_result() -> None:
    task = asyncio.create_task(asyncio.sleep(0, result="done"))
    await task
    registry = {task}

    await cancel_and_drain_tasks((task,), registry=registry)

    assert task.result() == "done"
    assert registry == set()


@pytest.mark.asyncio
async def test_empty_snapshot_preserves_unrelated_registry_entry() -> None:
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    registry = {task}

    await cancel_and_drain_tasks((), registry=registry)

    assert registry == {task}
