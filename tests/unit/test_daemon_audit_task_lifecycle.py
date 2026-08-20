"""Owner-side shutdown tests for daemon audit persistence tasks."""

from __future__ import annotations

import asyncio

import pytest

from general_ludd import daemon


@pytest.mark.asyncio
async def test_drain_self_update_audit_tasks_cancels_and_awaits_pending() -> None:
    task = asyncio.create_task(asyncio.Event().wait())
    daemon._SELF_UPDATE_AUDIT_TASKS.add(task)
    task.add_done_callback(daemon._SELF_UPDATE_AUDIT_TASKS.discard)

    await daemon._drain_self_update_audit_tasks()

    assert task.done()
    assert not daemon._SELF_UPDATE_AUDIT_TASKS
