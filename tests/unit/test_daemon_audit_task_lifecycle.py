"""Owner-side shutdown tests for daemon audit persistence tasks."""

from __future__ import annotations

import asyncio

import pytest

from general_ludd import daemon


@pytest.mark.asyncio
async def test_drain_self_update_audit_tasks_allows_pending_write_to_finish() -> None:
    completed = asyncio.Event()
    registry: set[asyncio.Task[object]] = set()

    async def persist() -> None:
        await asyncio.sleep(0)
        completed.set()

    task = asyncio.create_task(persist())
    registry.add(task)
    task.add_done_callback(registry.discard)

    await daemon._drain_self_update_audit_tasks(registry, timeout_seconds=1.0)

    assert completed.is_set()
    assert task.done()
    assert not task.cancelled()
    assert not registry


@pytest.mark.asyncio
async def test_drain_self_update_audit_tasks_cancels_only_after_timeout() -> None:
    registry: set[asyncio.Task[object]] = set()
    task = asyncio.create_task(asyncio.Event().wait())
    registry.add(task)
    task.add_done_callback(registry.discard)

    await daemon._drain_self_update_audit_tasks(registry, timeout_seconds=0.01)

    assert task.done()
    assert task.cancelled()
    assert not registry
