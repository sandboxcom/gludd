"""Small structured-concurrency helpers for application-owned tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, MutableSet
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


async def quiesce_task_before_drain(
    task: asyncio.Future[Any] | None,
    *,
    request_stop: Callable[[], None],
    drain: Callable[[], Awaitable[Any]],
    timeout_seconds: float,
) -> None:
    """Stop one producer before draining resources it can still acquire.

    The producer first receives its cooperative stop signal.  If it does not
    finish within the bounded grace period, cancellation is the fallback and
    must itself complete within the same bound.  Only a terminal producer may
    be followed by the dependent drain callback.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    request_stop()
    if task is not None and not task.done():
        done, _pending = await asyncio.wait((task,), timeout=timeout_seconds)
        if not done:
            task.cancel()
            done, _pending = await asyncio.wait((task,), timeout=timeout_seconds)
            if not done:
                raise TimeoutError("owned producer did not terminate after cancellation")

    if task is not None:
        # Observe terminal exceptions without allowing a failed producer to
        # skip dependent cleanup.  Its owner-specific done callback remains
        # responsible for recording the failure/degraded state.
        await asyncio.gather(task, return_exceptions=True)
    await drain()
