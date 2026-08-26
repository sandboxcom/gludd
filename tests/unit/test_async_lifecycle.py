"""Canonical behavioral coverage for application-owned async tasks."""

from __future__ import annotations

import asyncio

import pytest

from general_ludd.util.async_lifecycle import (
    cancel_and_drain_tasks,
    quiesce_task_before_drain,
    track_owned_task,
)


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


@pytest.mark.asyncio
async def test_track_owned_task_unregisters_only_after_completion() -> None:
    release = asyncio.Event()

    async def worker() -> None:
        await release.wait()

    task = asyncio.create_task(worker())
    registry: set[asyncio.Task[None]] = set()

    track_owned_task(task, registry)

    assert registry == {task}
    release.set()
    await task
    await asyncio.sleep(0)
    assert registry == set()


@pytest.mark.asyncio
async def test_quiesces_producer_before_draining_dependents() -> None:
    order: list[str] = []
    stop_requested = asyncio.Event()

    async def producer() -> None:
        await stop_requested.wait()
        order.append("producer-stopped")

    async def drain() -> None:
        order.append("drain")
        assert producer_task.done()

    def request_stop() -> None:
        order.append("stop")
        stop_requested.set()

    producer_task = asyncio.create_task(producer())

    await quiesce_task_before_drain(
        producer_task,
        request_stop=request_stop,
        drain=drain,
        timeout_seconds=1.0,
    )

    assert order == ["stop", "producer-stopped", "drain"]


@pytest.mark.asyncio
async def test_cancels_stalled_producer_before_draining_dependents() -> None:
    order: list[str] = []
    started = asyncio.Event()

    async def producer() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            order.append("producer-cancelled")

    async def drain() -> None:
        order.append("drain")
        assert producer_task.done()

    producer_task = asyncio.create_task(producer())
    await started.wait()

    await quiesce_task_before_drain(
        producer_task,
        request_stop=lambda: order.append("stop"),
        drain=drain,
        timeout_seconds=0.01,
    )

    assert producer_task.cancelled()
    assert order == ["stop", "producer-cancelled", "drain"]


@pytest.mark.asyncio
async def test_failed_producer_is_observed_before_drain() -> None:
    order: list[str] = []

    async def producer() -> None:
        raise RuntimeError("producer failed")

    async def drain() -> None:
        order.append("drain")

    producer_task = asyncio.create_task(producer())
    await asyncio.sleep(0)

    await quiesce_task_before_drain(
        producer_task,
        request_stop=lambda: order.append("stop"),
        drain=drain,
        timeout_seconds=1.0,
    )

    assert order == ["stop", "drain"]
