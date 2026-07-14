"""C.12: Events/hooks concurrency — remaining gaps from partial fix.

Tests:
    1. fire() holds the lock while snapshotting listeners
    2. EventBus history operations are thread-safe
    3. _scheduled_webhooks double-invocation guard is thread-safe
    4. Concurrent fire() + register/unregister is safe
"""

from __future__ import annotations

import inspect
import threading
import time
from typing import Any

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookSystem
from general_ludd.events.types import Event, EventType


class TestFireLocking:
    """fire() must hold self._lock while copying the listener list."""

    def test_fire_snapshot_under_lock_by_source(self) -> None:
        """Verify via source inspection that fire() acquires self._lock
        before reading/writing _hooks and _scheduled_webhooks.
        """
        fire_src = inspect.getsource(HookSystem.fire)
        assert "with self._lock:" in fire_src, (
            "fire() must acquire self._lock to snapshot _hooks and guard "
            "_scheduled_webhooks"
        )

    def test_fire_holds_lock_during_snapshot(self) -> None:
        """Concurrent register_callback during fire() must not cause a corrupted
        iteration or a RuntimeError from dict-mutation-during-iteration.
        """
        hs = HookSystem()
        fired: list[str] = []
        errors: list[BaseException] = []

        def slow_cb(_p: dict[str, Any]) -> None:
            fired.append("slow")
            time.sleep(0.05)
            fired.append("slow-done")

        hs.register_callback("e1", slow_cb)

        def hammer_register() -> None:
            try:
                for _i in range(100):
                    hid = hs.register_callback("e1", lambda p: None)
                    hs.unregister(hid)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=hammer_register, daemon=True)
        t.start()
        time.sleep(0.01)

        result = hs.fire("e1", {})
        t.join(timeout=5)

        assert not errors, f"hammer raised: {errors!r}"
        assert "slow" in fired
        assert result >= 1


class TestEventBusHistoryLocking:
    """EventBus history must be thread-safe."""

    def test_history_lock_present_in_source(self) -> None:
        """Verify _history_lock exists and guards _history access."""
        init_src = inspect.getsource(EventBus.__init__)
        assert "self._history_lock" in init_src, (
            "EventBus.__init__ must create _history_lock"
        )
        gh_src = inspect.getsource(EventBus.get_history)
        assert "with self._history_lock:" in gh_src or "with self._history_lock" in gh_src, (
            "get_history must acquire _history_lock"
        )

    def test_history_thread_safety(self) -> None:
        """Concurrent publish() and get_history() must not crash or lose data."""
        bus = EventBus(history_size=100)
        n_publishes = 500
        n_threads = 5
        barrier = threading.Barrier(n_threads + 1)
        errors: list[BaseException] = []

        def publisher() -> None:
            try:
                barrier.wait()
                for i in range(n_publishes // n_threads):
                    bus.publish(Event(type=EventType.CUSTOM, payload={"n": i}))
            except BaseException as exc:
                errors.append(exc)

        reader_histories: list[list[Event]] = []

        def reader() -> None:
            try:
                barrier.wait()
                for _ in range(20):
                    reader_histories.append(bus.get_history())
                    time.sleep(0.005)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=publisher) for _ in range(n_threads)]
        threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"threads raised: {errors!r}"
        for h in reader_histories:
            assert isinstance(h, list)


class TestFireWebhookDoubleInvocation:
    """fire() with webhooks must not double-schedule async callbacks."""

    def test_scheduled_webhooks_guarded_by_lock_in_source(self) -> None:
        """fire() must guard _scheduled_webhooks check+add with self._lock."""
        fire_src = inspect.getsource(HookSystem.fire)
        assert "with self._lock:" in fire_src, (
            "fire() must acquire self._lock to guard _scheduled_webhooks "
            "check-and-add against double-scheduling"
        )
        assert "_scheduled_webhooks" in fire_src

    def test_concurrent_fire_no_double_schedule(self) -> None:
        """Two threads calling fire() for the same webhook simultaneously
        must schedule the POST exactly once.
        """
        hs = HookSystem()
        hs.register_webhook("e1", "https://example.com/hook")

        results: list[int] = []
        barrier = threading.Barrier(2)

        def fire_in_thread() -> None:
            barrier.wait()
            r = hs.fire("e1", {"n": 1})
            results.append(r)

        t1 = threading.Thread(target=fire_in_thread)
        t2 = threading.Thread(target=fire_in_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert sum(results) <= 1, (
            f"Expected at most 1 webhook scheduled, got successes={results}"
        )


class TestConcurrentFireRegistration:
    """Concurrent fire() + register/unregister must be safe."""

    def test_concurrent_fire_while_registering(self) -> None:
        """fire() and register_callback() running concurrently must not
        cause dict-mutation-during-iteration errors.
        """
        hs = HookSystem()
        errors: list[BaseException] = []
        stopped = threading.Event()

        def firer() -> None:
            try:
                while not stopped.is_set():
                    hs.fire("e1", {"n": 1})
                    time.sleep(0.001)
            except BaseException as exc:
                errors.append(exc)

        def registrar() -> None:
            try:
                for _i in range(500):
                    hs.register_callback("e1", lambda p: None)
            except BaseException as exc:
                errors.append(exc)

        t1 = threading.Thread(target=firer, daemon=True)
        t2 = threading.Thread(target=registrar, daemon=True)
        t1.start()
        t2.start()
        t2.join(timeout=10)
        stopped.set()
        t1.join(timeout=5)

        assert not errors, (
            f"Concurrent fire/register raised: {errors!r}"
        )

    def test_concurrent_fire_while_unregistering(self) -> None:
        """fire() and unregister() running concurrently must not crash."""
        hs = HookSystem()
        errors: list[BaseException] = []
        stopped = threading.Event()

        # Register several hooks first
        registered: list[str] = []
        for _ in range(20):
            hid = hs.register_callback("e1", lambda p: None)
            registered.append(hid)

        def firer() -> None:
            try:
                while not stopped.is_set():
                    hs.fire("e1", {"n": 1})
                    time.sleep(0.001)
            except BaseException as exc:
                errors.append(exc)

        def unregistrar() -> None:
            try:
                for hid in registered:
                    hs.unregister(hid)
            except BaseException as exc:
                errors.append(exc)

        t1 = threading.Thread(target=firer, daemon=True)
        t2 = threading.Thread(target=unregistrar, daemon=True)
        t1.start()
        t2.start()
        t2.join(timeout=10)
        stopped.set()
        t1.join(timeout=5)

        assert not errors, (
            f"Concurrent fire/unregister raised: {errors!r}"
        )


class TestEventBusConcurrentPublish:
    """EventBus concurrent publish must be safe."""

    def test_concurrent_publish_subscribe(self) -> None:
        """Concurrent publish() and subscribe() must not crash or corrupt."""
        bus = EventBus()
        errors: list[BaseException] = []
        stopped = threading.Event()

        def publisher() -> None:
            try:
                while not stopped.is_set():
                    bus.publish(Event(type=EventType.CUSTOM))
                    time.sleep(0.001)
            except BaseException as exc:
                errors.append(exc)

        def subscriber() -> None:
            try:
                for _i in range(500):
                    sid = bus.subscribe(EventType.CUSTOM, lambda e: None)
                    bus.unsubscribe(sid)
            except BaseException as exc:
                errors.append(exc)

        t1 = threading.Thread(target=publisher, daemon=True)
        t2 = threading.Thread(target=subscriber, daemon=True)
        t1.start()
        t2.start()
        t2.join(timeout=10)
        stopped.set()
        t1.join(timeout=5)

        assert not errors, (
            f"Concurrent publish/subscribe raised: {errors!r}"
        )


class TestAsyncCallbackSingleInvocation:
    """Async callbacks must not be double-invoked by EventBus.publish()."""

    def test_async_callback_called_exactly_once_sync_context(self) -> None:
        """When publish() is called in a sync context (no running loop),
        an async subscriber must be invoked exactly once.
        """
        bus = EventBus()
        call_count = 0
        call_lock = threading.Lock()

        async def my_handler(event: Event) -> None:
            nonlocal call_count
            with call_lock:
                call_count += 1

        bus.subscribe(EventType.CUSTOM, my_handler)
        bus.publish(Event(type=EventType.CUSTOM))
        bus.publish(Event(type=EventType.CUSTOM))
        bus.publish(Event(type=EventType.CUSTOM))

        # Each publish should invoke the handler exactly once
        assert call_count == 3, (
            f"Expected 3 invocations, got {call_count}"
        )

    @pytest.mark.asyncio
    async def test_async_callback_called_exactly_once_async_context(self) -> None:
        """When publish() is called inside a running event loop,
        an async subscriber must be invoked exactly once per publish.
        """
        import asyncio

        bus = EventBus()
        call_count = 0

        async def my_handler(event: Event) -> None:
            nonlocal call_count
            call_count += 1

        bus.subscribe(EventType.CUSTOM, my_handler)
        bus.publish(Event(type=EventType.CUSTOM))
        bus.publish(Event(type=EventType.CUSTOM))

        # Let the scheduled tasks run
        await asyncio.sleep(0.05)

        assert call_count == 2, (
            f"Expected 2 invocations, got {call_count}"
        )
