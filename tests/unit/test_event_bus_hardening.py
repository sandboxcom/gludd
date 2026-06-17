"""Hardening tests for EventBus — covers three audit-confirmed fixes.

Fix 1 — dead double-call branch removed:
    An async subscriber must fire exactly ONCE per publish, never twice.

Fix 2 — _background_tasks cap:
    _MAX_BACKGROUND_TASKS class constant exists at 1000; when the set is at
    capacity, _dispatch_coro logs an error, closes the coroutine, and returns
    without creating a task (prevents OOM under burst).

Fix 3 — sync-context publish restores (not nulls) thread event loop:
    publish() called from a non-async context must leave the thread's current
    event loop as it was before the call, not as None.
"""
from __future__ import annotations

import asyncio
import contextlib
from unittest import mock

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event, EventType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(etype: EventType = EventType.CUSTOM) -> Event:
    return Event(type=etype, payload={})


# ---------------------------------------------------------------------------
# Fix 1: async subscriber fires exactly ONCE (dead double-call branch removed)
# ---------------------------------------------------------------------------

class TestAsyncSubscriberFiresOnce:
    """Async callbacks must be called exactly once per publish, not twice."""

    def test_async_subscriber_fires_once_sync_context(self) -> None:
        """In a sync (no running loop) context, the coroutine runs exactly once."""
        bus = EventBus()
        call_count: list[int] = []

        async def on_event(e: Event) -> None:
            call_count.append(1)

        bus.subscribe(EventType.CUSTOM, on_event)
        bus.publish(_make_event())

        assert len(call_count) == 1, (
            f"Async subscriber called {len(call_count)} time(s); expected exactly 1. "
            "The dead elif branch would have called it a second time."
        )

    @pytest.mark.asyncio
    async def test_async_subscriber_fires_once_async_context(self) -> None:
        """Inside a running event loop the subscriber is scheduled once as a task."""
        bus = EventBus()
        call_count: list[int] = []

        async def on_event(e: Event) -> None:
            call_count.append(1)

        bus.subscribe(EventType.CUSTOM, on_event)
        bus.publish(_make_event())

        # Yield control so the background task can run.
        await asyncio.sleep(0)

        assert len(call_count) == 1, (
            f"Async subscriber called {len(call_count)} time(s); expected exactly 1."
        )

    def test_sync_subscriber_fires_once(self) -> None:
        """Regression: sync subscribers must still fire exactly once."""
        bus = EventBus()
        call_count: list[int] = []

        bus.subscribe(EventType.CUSTOM, lambda e: call_count.append(1))
        bus.publish(_make_event())

        assert len(call_count) == 1


# ---------------------------------------------------------------------------
# Fix 2: _background_tasks cap — OOM guard under burst
# ---------------------------------------------------------------------------

class TestBackgroundTasksCap:
    """_MAX_BACKGROUND_TASKS class const exists and the cap guard works."""

    def test_max_background_tasks_constant_exists(self) -> None:
        assert hasattr(EventBus, "_MAX_BACKGROUND_TASKS"), (
            "EventBus must expose _MAX_BACKGROUND_TASKS class constant"
        )
        assert EventBus._MAX_BACKGROUND_TASKS == 1000

    @pytest.mark.asyncio
    async def test_cap_drops_excess_subscriber_and_logs_error(self) -> None:
        """When _background_tasks is at capacity, the next coro is closed + error logged."""
        bus = EventBus()
        call_count: list[int] = []

        async def on_event(e: Event) -> None:
            call_count.append(1)

        bus.subscribe(EventType.CUSTOM, on_event)

        # Fill _background_tasks with dummy, never-completing sentinel tasks so the
        # set is AT the cap limit before we publish the real event.
        loop = asyncio.get_running_loop()
        sentinel_tasks: list[asyncio.Task[None]] = []
        for _ in range(EventBus._MAX_BACKGROUND_TASKS):
            t: asyncio.Task[None] = loop.create_task(asyncio.sleep(9999))
            sentinel_tasks.append(t)
            bus._background_tasks.add(t)

        assert len(bus._background_tasks) == EventBus._MAX_BACKGROUND_TASKS

        # Capture what logger.error sees.
        with mock.patch(
            "general_ludd.events.bus.logger"
        ) as mock_logger:
            bus.publish(_make_event())
            # Yield so any task that slipped through could run.
            await asyncio.sleep(0)

        # The subscriber must NOT have been called (coro dropped).
        assert len(call_count) == 0, (
            "Async subscriber must not fire when background task cap is reached"
        )
        # logger.error must have been called (cap-reached message).
        assert mock_logger.error.called, "logger.error must be called when cap is reached"
        # Cleanup sentinel tasks.
        for t in sentinel_tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    @pytest.mark.asyncio
    async def test_cap_boundary_just_below_still_schedules(self) -> None:
        """One slot below the cap: subscriber IS scheduled normally."""
        bus = EventBus()
        call_count: list[int] = []

        async def on_event(e: Event) -> None:
            call_count.append(1)

        bus.subscribe(EventType.CUSTOM, on_event)

        loop = asyncio.get_running_loop()
        sentinel_tasks: list[asyncio.Task[None]] = []
        # Fill to cap - 1.
        for _ in range(EventBus._MAX_BACKGROUND_TASKS - 1):
            t = loop.create_task(asyncio.sleep(9999))
            sentinel_tasks.append(t)
            bus._background_tasks.add(t)

        assert len(bus._background_tasks) == EventBus._MAX_BACKGROUND_TASKS - 1

        bus.publish(_make_event())
        await asyncio.sleep(0)

        assert len(call_count) == 1, (
            "One slot below cap: async subscriber must still be scheduled"
        )

        for t in sentinel_tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


# ---------------------------------------------------------------------------
# Fix 3: sync-context publish restores thread event loop (not None)
# ---------------------------------------------------------------------------

class TestSyncPublishRestoresEventLoop:
    """publish() from a non-async context must leave the thread's loop intact."""

    def test_event_loop_not_none_after_sync_publish_with_async_subscriber(self) -> None:
        """After a sync publish with an async sub, get_event_loop() must not raise or return None."""
        bus = EventBus()

        async def on_event(e: Event) -> None:
            pass

        bus.subscribe(EventType.CUSTOM, on_event)

        # Record what the policy considers the current loop BEFORE publish.
        try:
            prior = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            prior = None

        bus.publish(_make_event())

        # After publish the policy's current loop must be the same as before —
        # not None (which would break downstream callers).
        try:
            after = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            after = None

        assert after is prior, (
            f"Event loop after publish ({after!r}) differs from before ({prior!r}). "
            "publish() must RESTORE the prior loop, not set None."
        )

    def test_event_loop_not_none_after_sync_publish_no_async_sub(self) -> None:
        """Baseline: a sync-only publish must not disturb the thread loop either."""
        bus = EventBus()
        bus.subscribe(EventType.CUSTOM, lambda e: None)

        try:
            prior = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            prior = None

        bus.publish(_make_event())

        try:
            after = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            after = None

        assert after is prior
