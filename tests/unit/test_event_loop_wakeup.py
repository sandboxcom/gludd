"""Immediate EventLoop wake-up when durable infrastructure state changes."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from general_ludd.event_loop.loop import EventLoop


class _WakeLoop(EventLoop):
    def __init__(self) -> None:
        self._running = False
        self._wake_event = asyncio.Event()
        self._inbound_queue = None
        self.ticks = 0
        self.first_tick = asyncio.Event()
        self.second_tick = asyncio.Event()

    async def tick(self) -> dict[str, Any]:
        self.ticks += 1
        if self.ticks == 1:
            self.first_tick.set()
        elif self.ticks == 2:
            self.second_tick.set()
        return {}

    async def _resume_interrupted_dispatches(self) -> None:
        return None


@pytest.mark.asyncio
async def test_wake_interrupts_long_tick_interval() -> None:
    loop = _WakeLoop()
    task = asyncio.create_task(loop.run_forever(interval=60.0))
    await asyncio.wait_for(loop.first_tick.wait(), timeout=1.0)

    loop.wake()
    await asyncio.wait_for(loop.second_tick.wait(), timeout=1.0)
    loop.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert loop.ticks == 2
