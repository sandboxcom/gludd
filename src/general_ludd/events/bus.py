from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from general_ludd.events.types import Event, EventType

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self, history_size: int = 0) -> None:
        self._subscribers: dict[str, list[tuple[str, Callable[..., Any]]]] = defaultdict(list)
        self._history: list[Event] = []
        self._history_size = history_size
        self._next_id = 0
        self._lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def subscribe(self, event_type: EventType | str, callback: Callable[..., Any]) -> str:
        with self._lock:
            sub_id = f"sub-{self._next_id}"
            self._next_id += 1
            key = event_type if isinstance(event_type, str) else event_type.value
            self._subscribers[key].append((sub_id, callback))
            return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        with self._lock:
            for key in list(self._subscribers.keys()):
                self._subscribers[key] = [
                    (sid, cb) for sid, cb in self._subscribers[key]
                    if sid != subscription_id
                ]

    def publish(self, event: Event) -> int:
        """Deliver ``event`` to every matching subscriber.

        Returns the number of *successful* synchronous deliveries. Subscribers
        that raise are excluded from the count and logged at ERROR (with the
        event type/id). Async subscribers are scheduled and counted as
        delivered at dispatch time; any exception they raise once they actually
        run is surfaced separately by the background-task done-callback in
        :meth:`_dispatch_coro` (see :meth:`_on_task_done`).
        """
        key = event.type if isinstance(event.type, str) else event.type.value
        event_id = getattr(event, "event_id", None)
        with self._lock:
            subscribers = list(self._subscribers.get(key, []))
            wildcard_subs = list(self._subscribers.get("*", []))
        all_subs = subscribers + wildcard_subs
        delivered = 0
        failed = 0
        for sub_id, callback in all_subs:
            try:
                result = callback(event)
                if inspect.isawaitable(result):
                    self._dispatch_coro(result)
                delivered += 1
            except Exception as exc:
                failed += 1
                logger.error(
                    "Event subscriber %s failed for event type=%s id=%s: %s",
                    sub_id,
                    key,
                    event_id,
                    exc,
                    exc_info=True,
                )

        if failed:
            logger.error(
                "Event type=%s id=%s: %d/%d subscriber(s) failed",
                key,
                event_id,
                failed,
                len(all_subs),
            )

        if self._history_size > 0:
            with self._history_lock:
                self._history.append(event)
                if len(self._history) > self._history_size:
                    self._history = self._history[-self._history_size:]

        return delivered

    def _dispatch_coro(self, coro: Any) -> None:
        """Run a coroutine produced by a subscriber callback.

        If an event loop is currently running, the coroutine is scheduled as a
        tracked background task on it. Otherwise (sync caller / test context) the
        coroutine is run to completion on a fresh, dedicated event loop.

        Using a fresh loop rather than ``asyncio.run`` avoids the
        "RuntimeError: Event loop is closed" that ``asyncio.run`` can raise when
        a *closed* loop has been left as the current loop by a prior caller (a
        common situation under pytest-asyncio when tests interleave). The
        coroutine is always consumed — even on failure — so no
        "coroutine was never awaited" warning leaks into a later test.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            task = loop.create_task(coro)
            task.add_done_callback(self._on_task_done)
            self._background_tasks.add(task)
            return

        # No running loop: drive the coroutine on a dedicated, isolated loop and
        # restore whatever event-loop policy state existed before, so we never
        # leave a closed loop behind for the next caller to trip over.
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(coro)
        except Exception as exc:
            # Surface async subscriber failures even on the no-running-loop
            # path. Without this they would propagate out of publish() and be
            # mis-attributed to the (already-counted) subscriber, or vanish.
            logger.error(
                "Async event subscriber raised in dispatch loop: %s", exc, exc_info=True
            )
        finally:
            try:
                new_loop.close()
            finally:
                # Drop any reference to the (now closed) loop as the current one
                # so subsequent asyncio.get_event_loop() calls create a fresh one
                # instead of returning this closed loop.
                asyncio.set_event_loop(None)

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        """Done-callback for scheduled async subscriber tasks.

        Removes the task from the tracking set and, crucially, surfaces any
        exception it raised. Without calling ``task.exception()`` here, an async
        subscriber that throws would fail silently (asyncio only emits a
        "Task exception was never retrieved" warning at GC time, which is easy
        to miss and arrives detached from the originating event).
        """
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Async event subscriber task failed: %s", exc, exc_info=True
            )

    def get_history(self) -> list[Event]:
        with self._history_lock:
            return list(self._history)

    async def drain(self) -> None:
        """Wait until every currently scheduled async subscriber has finished.

        The loop repeats because a subscriber may publish another event and
        schedule more work. Tests and graceful shutdown can therefore observe
        progressive event failures deterministically instead of waiting for an
        unrelated suite or process timeout.
        """
        while self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()
