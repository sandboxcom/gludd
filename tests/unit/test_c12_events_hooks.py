"""C12: Events/hooks fixes — TDD tests.

Covers three fixes from docs/design/WAVE_C_ADDENDUM_2026-07-10.md C12:
  1. fire() copies the listener list before iteration (mutation-safe)
  2. HookSystem register/unregister + EventBus subscribe/unsubscribe are
     thread-safe (no duplicate ids under concurrency)
  3. Async webhook callbacks are not double-scheduled (idempotent dispatch)
"""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookSystem
from general_ludd.events.types import Event, EventType

# ── 1. fire() snapshots listener list ────────────────────────────────

def test_fire_copies_list_before_iteration() -> None:
    """A callback that registers a new hook for the SAME event during fire()
    must NOT cause the new hook to be invoked in the current fire() call.

    Without the snapshot, register_callback appends-and-sorts the list
    mid-iteration, which can silently double-fire or skip entries.
    """
    hs = HookSystem()
    fired: list[str] = []

    def reentrant_callback(payload: dict[str, Any]) -> None:
        fired.append("original")

        def _new_callback(_p: dict[str, Any]) -> None:
            fired.append("late")

        hs.register_callback("e1", _new_callback)

    hs.register_callback("e1", reentrant_callback)
    hs.fire("e1", {})

    # The original callback fired; the late-registered callback MUST NOT have
    # been called during the same fire().
    assert fired == ["original"], (
        f"Expected ['original'] only (late callback should not fire during "
        f"the same iteration), got {fired}"
    )

    # A second fire() should now pick up the late callback.
    fired.clear()
    hs.fire("e1", {})
    assert fired == ["original", "late"], (
        f"Expected ['original', 'late'] on second fire, got {fired}"
    )


def test_fire_snapshot_deregister_no_skip() -> None:
    """A callback that unregisters ANOTHER hook for the same event during
    fire() must not skip subsequent hooks in the iteration.

    Without a snapshot, the in-place list comprehension in unregister()
    rebuilds the list, shrinking iteration indices and skipping callbacks.
    """
    hs = HookSystem()
    fired: list[str] = []

    def remove_the_other(payload: dict[str, Any]) -> None:
        fired.append("remover")
        hs.unregister(hook_id_b)

    def should_still_fire(payload: dict[str, Any]) -> None:
        fired.append("target")

    hook_id_a = hs.register_callback("e1", remove_the_other)
    hook_id_b = hs.register_callback("e1", should_still_fire)
    assert hook_id_a != hook_id_b

    hs.fire("e1", {})

    # Both callbacks must fire even though 'remove_the_other' unregistered
    # 'should_still_fire' mid-iteration.
    assert "remover" in fired, f"remover callback not fired, got {fired}"
    assert "target" in fired, (
        f"target callback skipped (unregister corrupted iteration), got {fired}"
    )


def test_eventbus_publish_copies_subscriber_list() -> None:
    """EventBus.publish() already snapshotted subscribers — this test pins
    that behaviour so it does not regress.
    """
    bus = EventBus()
    fired: list[str] = []

    def resubscriber(event: Event) -> None:
        fired.append("first")
        bus.subscribe(event.type, lambda e: fired.append("late"))

    bus.subscribe(EventType.CUSTOM, resubscriber)
    bus.publish(Event(type=EventType.CUSTOM))

    # The late subscriber must not fire during this publish.
    assert fired == ["first"], (
        f"Expected ['first'] only, got {fired}"
    )


# ── 2. Thread-safety (no duplicate ids under concurrency) ─────────────

def _unique_ids_after_parallel_registrations(
    register_fn: Callable[[], str],
    n_threads: int = 20,
    calls_per_thread: int = 50,
) -> bool:
    """Return True when every registration id is unique."""
    ids: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            for _ in range(calls_per_thread):
                rid = register_fn()
                with lock:
                    ids.append(rid)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker threads raised: {errors!r}"
    assert len(ids) == n_threads * calls_per_thread, (
        f"Expected {n_threads * calls_per_thread} registrations, got {len(ids)}"
    )
    return len(ids) == len(set(ids))


def test_hooksystem_registration_thread_safe() -> None:
    """HookSystem.register_callback under concurrency must produce unique ids."""
    hs = HookSystem()
    ok = _unique_ids_after_parallel_registrations(
        register_fn=lambda: hs.register_callback("e1", lambda p: None),
        n_threads=10,
        calls_per_thread=100,
    )
    assert ok, "Duplicate hook ids detected under concurrency"


def test_hooksystem_register_webhook_thread_safe() -> None:
    """HookSystem.register_webhook under concurrency must produce unique ids."""
    hs = HookSystem()
    ok = _unique_ids_after_parallel_registrations(
        register_fn=lambda: hs.register_webhook(
            "e1", "https://example.com/hook"
        ),
        n_threads=10,
        calls_per_thread=100,
    )
    assert ok, "Duplicate webhook hook ids detected under concurrency"


def test_eventbus_subscribe_thread_safe() -> None:
    """EventBus.subscribe under concurrency must produce unique subscription ids."""
    bus = EventBus()
    ok = _unique_ids_after_parallel_registrations(
        register_fn=lambda: bus.subscribe(EventType.CUSTOM, lambda e: None),
        n_threads=10,
        calls_per_thread=100,
    )
    assert ok, "Duplicate subscription ids detected under concurrency"


def test_eventbus_concurrent_subscribe_unsubscribe_no_crash() -> None:
    """subscribe + unsubscribe from many threads must not raise or corrupt."""
    bus = EventBus()
    barrier = threading.Barrier(10)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            for _i in range(100):
                sid = bus.subscribe(EventType.CUSTOM, lambda e: None)
                bus.unsubscribe(sid)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"subscribe/unsubscribe under concurrency raised: {errors!r}"
    # After all unsubscribes, subscriber dict should be effectively empty or
    # contain only empty lists.
    for sub_list in bus._subscribers.values():
        assert sub_list == [], f"Subscriber list not empty after unsubscribes: {sub_list}"


def test_hooksystem_concurrent_register_unregister_no_crash() -> None:
    """register + unregister from many threads must not raise or corrupt."""
    hs = HookSystem()
    barrier = threading.Barrier(10)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            for _i in range(100):
                hid = hs.register_callback("e1", lambda p: None)
                hs.unregister(hid)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"register/unregister under concurrency raised: {errors!r}"
    # After all unregisters, the e1 hook list should be empty.
    hooks = hs._hooks.get("e1", [])
    assert hooks == [], f"Hook list not empty after unregisters: {hooks}"


def test_hooksystem_lock_present_in_source() -> None:
    """Pin the threading lock so a refactor cannot silently drop thread-safety."""
    src = inspect.getsource(HookSystem.__init__)
    assert "threading.Lock()" in src or "threading.RLock(" in src, (
        "HookSystem.__init__ must create a threading lock"
    )
    reg_src = inspect.getsource(HookSystem.register_callback)
    assert "with self._lock:" in reg_src or "with self._lock" in reg_src, (
        "register_callback must acquire the lock"
    )
    unreg_src = inspect.getsource(HookSystem.unregister)
    assert "with self._lock:" in unreg_src or "with self._lock" in unreg_src, (
        "unregister must acquire the lock"
    )


def test_eventbus_lock_present_in_source() -> None:
    """Pin the threading lock so a refactor cannot silently drop thread-safety."""
    src = inspect.getsource(EventBus.__init__)
    assert "threading.Lock()" in src or "threading.RLock(" in src, (
        "EventBus.__init__ must create a threading lock"
    )
    sub_src = inspect.getsource(EventBus.subscribe)
    assert "with self._lock:" in sub_src or "with self._lock" in sub_src, (
        "subscribe must acquire the lock"
    )
    unsub_src = inspect.getsource(EventBus.unsubscribe)
    assert "with self._lock:" in unsub_src or "with self._lock" in unsub_src, (
        "unsubscribe must acquire the lock"
    )


# ── 3. Async webhooks not double-scheduled ────────────────────────────

@pytest.mark.asyncio
async def test_async_callbacks_not_double_invoked() -> None:
    """Calling fire() twice in quick succession for the same event must not
    schedule duplicate webhook tasks for the same hook_id.

    The HookSystem tracks in-flight webhook hook_ids and skips scheduling
    when one is already pending. Once the task completes, the slot is freed.
    """
    import asyncio as aio

    hs = HookSystem()

    call_count = 0
    call_lock = threading.Lock()

    # Use a slow mock so the first call is still "in flight" when we fire
    # the second time.
    async def slow_post(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        with call_lock:
            call_count += 1
        await aio.sleep(0.1)

        class FakeResponse:
            status_code = 200
            def raise_for_status(self) -> None:
                pass

        return FakeResponse()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = slow_post

        hook_id = hs.register_webhook("e1", "https://example.com/hook")
        assert hook_id.startswith("hook-wh-")

        # Fire twice rapidly — both before the async task completes.
        # fire() schedules the webhook via ensure_future on the running loop
        # but does NOT await it, so the second fire() runs while the first
        # is still in-flight.
        result1 = hs.fire("e1", {"key": "val1"})
        result2 = hs.fire("e1", {"key": "val2"})

        # First fire schedules; second fire sees the hook_id still in flight
        # and skips it.
        assert result1 == 1, f"First fire should have 1 success, got {result1}"
        assert result2 == 0, (
            f"Second fire should skip the already-scheduled webhook (0 success), "
            f"got {result2}"
        )

        # Let the scheduled task run to completion.
        await aio.sleep(0.2)

    # The async task must have been called exactly once.
    assert call_count == 1, (
        f"Webhook POST called {call_count} times — expected exactly 1"
    )


def test_async_webhook_reenabled_after_completion() -> None:
    """Once a webhook task finishes, the hook_id slot is released, and a
    subsequent fire() schedules it again. (Proves the idempotency guard
    is not a one-shot-kill.)
    """
    hs = HookSystem()
    post_called = threading.Event()

    async def tracked_post(*args: Any, **kwargs: Any) -> Any:
        post_called.set()
        import asyncio
        await asyncio.sleep(0.05)

        class FakeResponse:
            status_code = 200
            def raise_for_status(self) -> None:
                pass
        return FakeResponse()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = tracked_post

        hs.register_webhook("e1", "https://example.com/hook")

        r1 = hs.fire("e1", {"n": 1})
        assert r1 == 1

        # Wait until the async task actually runs.
        post_called.wait(timeout=5.0)
        # Give the task time to finish and run its done callback.
        time.sleep(0.15)

        # Second fire should schedule again (slot freed).
        r2 = hs.fire("e1", {"n": 2})
        assert r2 == 1, (
            f"Second fire after completion should schedule again, got {r2}"
        )


def test_eventbus_publish_preserves_existing_snapshot() -> None:
    """EventBus.publish() already uses list() for snapshot — confirm it
    still works correctly for the common case.
    """
    bus = EventBus()
    received: list[str] = []

    def cb_a(event: Event) -> None:
        received.append("A")

    def cb_b(event: Event) -> None:
        received.append("B")

    bus.subscribe(EventType.CUSTOM, cb_a)
    bus.subscribe(EventType.CUSTOM, cb_b)
    delivered = bus.publish(Event(type=EventType.CUSTOM))

    assert delivered == 2, f"Expected 2 deliveries, got {delivered}"
    assert "A" in received
    assert "B" in received
