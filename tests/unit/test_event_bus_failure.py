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

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookSystem
from general_ludd.events.types import Event, EventType


class TestPublishFailureAccounting:
    def test_throwing_sync_subscriber_reduces_delivered_count(self, caplog):
        bus = EventBus()
        good_calls: list[int] = []

        def bad(_e: Event) -> None:
            raise ValueError("boom")

        def good(_e: Event) -> None:
            good_calls.append(1)

        bus.subscribe(EventType.MODEL_ADDED, bad)
        bus.subscribe(EventType.MODEL_ADDED, good)

        logging.getLogger("general_ludd.events.bus").propagate = True
        with caplog.at_level(logging.ERROR, logger="general_ludd.events.bus"):
            delivered = bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))

        # Two subscribers, one raised -> only the good one counts as delivered.
        assert delivered == 1
        # The good subscriber still ran (a failure must not block others).
        assert good_calls == [1]
        # The failure was logged at ERROR with the event type, not swallowed.
        error_logs = [(r.levelno, r.getMessage()) for r in caplog.records]
        assert any(
            record.levelno == logging.ERROR and "failed" in record.getMessage()
            for record in caplog.records
        ), f"No ERROR 'failed' log found. caplog records: {error_logs}"
        assert any("model_added" in record.getMessage() for record in caplog.records), \
            f"'model_added' not in any log message. Messages: {[r.getMessage() for r in caplog.records]}"

    def test_all_subscribers_succeed_returns_full_count(self):
        bus = EventBus()
        bus.subscribe(EventType.MODEL_ADDED, lambda _e: None)
        bus.subscribe(EventType.MODEL_ADDED, lambda _e: None)
        assert bus.publish(Event(type=EventType.MODEL_ADDED, payload={})) == 2

    def test_no_subscribers_returns_zero_and_logs_nothing(self, caplog):
        bus = EventBus()
        logging.getLogger("general_ludd.events.bus").propagate = True
        with caplog.at_level(logging.ERROR, logger="general_ludd.events.bus"):
            assert bus.publish(Event(type=EventType.CUSTOM, payload={})) == 0
        assert [r for r in caplog.records if r.levelno == logging.ERROR] == []

    @pytest.mark.asyncio
    async def test_async_subscriber_exception_is_logged(self, caplog):
        bus = EventBus()

        async def bad(_e: Event) -> None:
            raise RuntimeError("async boom")

        bus.subscribe("test.async_fail", bad)

        logging.getLogger("general_ludd.events.bus").propagate = True
        with caplog.at_level(logging.ERROR, logger="general_ludd.events.bus"):
            # Counted as delivered at dispatch time (the coroutine was scheduled).
            delivered = bus.publish(Event(type="test.async_fail"))
            assert delivered == 1
            # The coroutine is scheduled as a tracked background task on the
            # running loop. Capture it and await it DETERMINISTICALLY (instead of
            # polling with sleeps) so the task runs to completion and its
            # done-callback fires before we assert.
            assert len(bus._background_tasks) == 1, (
                "publish should have scheduled exactly one async subscriber task"
            )
            (task,) = tuple(bus._background_tasks)
            # The subscriber raises, so the task resolves to an exception; await
            # it via gather(return_exceptions=True) to retrieve the result and
            # let _on_task_done run, without re-raising here.
            await asyncio.gather(task, return_exceptions=True)
            # The done-callback is scheduled via call_soon; yield once so it
            # runs and emits the ERROR log before we assert.
            await asyncio.sleep(0)

        error_logs = [(r.levelno, r.getMessage()) for r in caplog.records]
        assert any(
            record.levelno == logging.ERROR
            and "async event subscriber task failed" in record.getMessage().lower()
            for record in caplog.records
        ), f"No async-task-failure ERROR log found. caplog records: {error_logs}"

    def test_async_subscriber_exception_logged_without_running_loop(self, caplog):
        # No running event loop -> _dispatch_coro drives a fresh, isolated loop
        # (run_until_complete completes synchronously inside publish(), so the
        # ERROR is logged before publish() returns -- no polling needed) and
        # must still surface the exception instead of letting it escape
        # publish().
        #
        # The bus creates and closes its own loop internally. To keep this test
        # hermetic under xdist -- so neither a pre-existing loop is clobbered nor
        # a closed loop is leaked to sibling tests on this worker -- snapshot and
        # restore the policy's current event loop around the call.
        try:
            prev_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            prev_loop = None

        try:
            bus = EventBus()

            async def bad(_e: Event) -> None:
                raise RuntimeError("sync-path async boom")

            bus.subscribe("test.async_fail_syncpath", bad)

            logging.getLogger("general_ludd.events.bus").propagate = True
            with caplog.at_level(logging.ERROR, logger="general_ludd.events.bus"):
                delivered = bus.publish(Event(type="test.async_fail_syncpath"))

            # publish() did not propagate the async failure as a delivery error;
            # the dispatch-loop error was already logged synchronously.
            assert delivered == 1
            error_logs = [(r.levelno, r.getMessage()) for r in caplog.records]
            assert any(
                record.levelno == logging.ERROR
                and "dispatch loop" in record.getMessage().lower()
                for record in caplog.records
            ), f"No 'dispatch loop' ERROR log found. caplog records: {error_logs}"
        finally:
            # Restore whatever loop the worker had before so we never leave a
            # closed loop (or None) behind for the next test on this xdist worker.
            asyncio.set_event_loop(prev_loop)


class TestHookFireFailureSurfacing:
    def test_failing_callback_logged_at_error_and_excluded_from_count(self, caplog):
        hooks = HookSystem()
        ok: list[int] = []
        hooks.register_callback("on_x", lambda _p: 1 / 0)
        hooks.register_callback("on_x", lambda _p: ok.append(1))

        with caplog.at_level(logging.ERROR, logger="general_ludd.events.hooks"):
            count = hooks.fire("on_x", {})

        # Only the successful hook counts; the other still ran.
        assert count == 1
        assert ok == [1]
        error_logs = [(r.levelno, r.getMessage()) for r in caplog.records]
        assert any(
            record.levelno == logging.ERROR and "failed" in record.getMessage()
            for record in caplog.records
        ), f"No ERROR 'failed' hook log found. caplog records: {error_logs}"

    def test_all_hooks_succeed_count_and_no_error(self, caplog):
        hooks = HookSystem()
        hooks.register_callback("on_x", lambda _p: None)
        hooks.register_callback("on_x", lambda _p: None)
        with caplog.at_level(logging.ERROR, logger="general_ludd.events.hooks"):
            assert hooks.fire("on_x", {}) == 2
        assert [r for r in caplog.records if r.levelno == logging.ERROR] == []

    def test_event_bus_is_stored_and_receives_hook_triggered(self):
        bus = EventBus(history_size=10)
        hooks = HookSystem(event_bus=bus)
        # The previously-dropped param is now retained.
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
        # No bus configured -> fire still works, no HookTriggeredEvent attempted.
        assert hooks.fire("on_x", {}) == 0
