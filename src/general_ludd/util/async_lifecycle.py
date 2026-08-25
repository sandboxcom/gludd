"""Small structured-concurrency helpers for application-owned tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, MutableSet
from typing import Any


def track_owned_task(
    task: asyncio.Task[Any],
    registry: MutableSet[asyncio.Task[Any]],
) -> None:
    """Register an application-owned task until its terminal callback runs."""
    registry.add(task)
    task.add_done_callback(registry.discard)


async def cancel_and_drain_tasks(
    tasks: Iterable[asyncio.Task[Any]],
    *,
    registry: MutableSet[asyncio.Task[Any]] | None = None,
) -> None:
    """Cancel, await, and unregister a stable snapshot of owned tasks."""
    pending = tuple(tasks)
    for task in pending:
        if not task.done():
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    if registry is not None:
        for task in pending:
            registry.discard(task)
