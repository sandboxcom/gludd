"""Failure-surfacing tests for events/bus.py and events/hooks.py.

An audit found the event bus swallowed handler failures and reported success:
- publish() returned len(all_subs) regardless of how many handlers raised;
- async subscriber exceptions were never surfaced (the background task's
  done-callback only discarded the task, never read task.exception());
- HookSystem.fire() warned-and-counted, hiding failures.

These tests pin the corrected behavior: a throwing sync subscriber reduces the
delivered count and is logged at ERROR; an async subscriber exception is logged
at ERROR; and hook failures are surfaced (not silently swallowed).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookSystem
from general_ludd.events.types import Event, EventType

_BUS_LOGGER = "general_ludd.events.bus"
_HOOKS_LOGGER = "general_ludd.events.hooks"


class TestPublishFailureAccounting:
    def test_throwing_sync_subscriber_reduces_delivered_count(self):
        bus = EventBus()
        good_calls: list[int] = []

        def bad(_e: Event) -> None:
            raise ValueError("boom")

        def good(_e: Event) -> None:
            good_calls.append(1)

        bus.subscribe(EventType.MODEL_ADDED, bad)
        bus.subscribe(EventType.MODEL_ADDED, good)

        bus_logger = logging.getLogger(_BUS_LOGGER)
        with patch.object(bus_logger, "error", wraps=bus_logger.error) as mock_error:
            delivered = bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))

        assert delivered == 1
        assert good_calls == [1]
        error_texts = [
            str(a) for call in mock_error.mock_calls if call.args for a in call.args
        ]
        assert any("failed" in t for t in error_texts), \
            f"No 'failed' in any error arg. Args: {error_texts}"
        assert any("model_added" in t for t in error_texts), \
            f"'model_added' not in any error arg. Args: {error_texts}"

    def test_all_subscribers_succeed_returns_full_count(self):
        bus = EventBus()
        bus.subscribe(EventType.MODEL_ADDED, lambda _e: None)
        bus.subscribe(EventType.MODEL_ADDED, lambda _e: None)
        assert bus.publish(Event(type=EventType.MODEL_ADDED, payload={})) == 2

    def test_no_subscribers_returns_zero_and_logs_nothing(self):
        bus = EventBus()
        bus_logger = logging.getLogger(_BUS_LOGGER)
        with patch.object(bus_logger, "error", wraps=bus_logger.error) as mock_error:
            assert bus.publish(Event(type=EventType.CUSTOM, payload={})) == 0
        assert mock_error.call_count == 0

    @pytest.mark.asyncio
    async def test_async_subscriber_exception_is_logged(self):
        bus = EventBus()

        async def bad(_e: Event) -> None:
            raise RuntimeError("async boom")

        bus.subscribe("test.async_fail", bad)

        bus_logger = logging.getLogger(_BUS_LOGGER)
        with patch.object(bus_logger, "error", wraps=bus_logger.error) as mock_error:
            delivered = bus.publish(Event(type="test.async_fail"))
            assert delivered == 1
            assert len(bus._background_tasks) == 1, (
                "publish should have scheduled exactly one async subscriber task"
            )
            (task,) = tuple(bus._background_tasks)
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        error_texts = [
            str(a) for call in mock_error.mock_calls if call.args for a in call.args
        ]
        assert any("async event subscriber task failed" in t.lower() for t in error_texts), \
            f"No async-task-failure error arg. Args: {error_texts}"

    def test_async_subscriber_exception_logged_without_running_loop(self):
        try:
            prev_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            prev_loop = None

        try:
            bus = EventBus()

            async def bad(_e: Event) -> None:
                raise RuntimeError("sync-path async boom")

            bus.subscribe("test.async_fail_syncpath", bad)

            bus_logger = logging.getLogger(_BUS_LOGGER)
            with patch.object(bus_logger, "error", wraps=bus_logger.error) as mock_error:
                delivered = bus.publish(Event(type="test.async_fail_syncpath"))

            assert delivered == 1
            error_texts = [
                str(a) for call in mock_error.mock_calls if call.args for a in call.args
            ]
            assert any("dispatch loop" in t.lower() for t in error_texts), \
                f"No 'dispatch loop' error arg. Args: {error_texts}"
        finally:
            asyncio.set_event_loop(prev_loop)


class TestHookFireFailureSurfacing:
    def test_failing_callback_logged_at_error_and_excluded_from_count(self):
        hooks = HookSystem()
        ok: list[int] = []
        hooks.register_callback("on_x", lambda _p: 1 / 0)
        hooks.register_callback("on_x", lambda _p: ok.append(1))

        hooks_logger = logging.getLogger(_HOOKS_LOGGER)
        with patch.object(hooks_logger, "error", wraps=hooks_logger.error) as mock_error:
            count = hooks.fire("on_x", {})

        assert count == 1
        assert ok == [1]
        error_texts = [
            str(a) for call in mock_error.mock_calls if call.args for a in call.args
        ]
        assert any("failed" in t for t in error_texts), \
            f"No 'failed' in any error arg. Args: {error_texts}"

    def test_all_hooks_succeed_count_and_no_error(self):
        hooks = HookSystem()
        hooks.register_callback("on_x", lambda _p: None)
        hooks.register_callback("on_x", lambda _p: None)
        hooks_logger = logging.getLogger(_HOOKS_LOGGER)
        with patch.object(hooks_logger, "error", wraps=hooks_logger.error) as mock_error:
            assert hooks.fire("on_x", {}) == 2
        assert mock_error.call_count == 0

    def test_event_bus_is_stored_and_receives_hook_triggered(self):
        bus = EventBus(history_size=10)
        hooks = HookSystem(event_bus=bus)
        assert hooks._event_bus is bus

        hooks.register_callback("on_x", lambda _p: None)
        hooks.fire("on_x", {})

        history = bus.get_history()
        assert any(
            e.type == EventType.HOOK_TRIGGERED
            and e.payload.get("event_name") == "on_x"
            and e.payload.get("succeeded") == 1
            and e.payload.get("failed") == 0
            for e in history
        )

    def test_no_event_bus_does_not_break_fire(self):
        hooks = HookSystem()
        assert hooks.fire("on_x", {}) == 0
