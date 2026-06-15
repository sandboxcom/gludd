from __future__ import annotations

import asyncio
import inspect
import logging
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
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def subscribe(self, event_type: EventType | str, callback: Callable[..., Any]) -> str:
        sub_id = f"sub-{self._next_id}"
        self._next_id += 1
        key = event_type if isinstance(event_type, str) else event_type.value
        self._subscribers[key].append((sub_id, callback))
        return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        for key in list(self._subscribers.keys()):
            self._subscribers[key] = [
                (sid, cb) for sid, cb in self._subscribers[key]
                if sid != subscription_id
            ]

    def publish(self, event: Event) -> int:
        key = event.type if isinstance(event.type, str) else event.type.value
        subscribers = list(self._subscribers.get(key, []))
        wildcard_subs = list(self._subscribers.get("*", []))
        all_subs = subscribers + wildcard_subs
        for sub_id, callback in all_subs:
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    self._dispatch_coro(result)
                elif inspect.iscoroutinefunction(callback):
                    self._dispatch_coro(callback(event))
            except Exception as exc:
                logger.warning("Event subscriber %s error: %s", sub_id, exc)

        if self._history_size > 0:
            self._history.append(event)
            if len(self._history) > self._history_size:
                self._history = self._history[-self._history_size:]

        return len(all_subs)

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
            task.add_done_callback(self._background_tasks.discard)
            self._background_tasks.add(task)
            return

        # No running loop: drive the coroutine on a dedicated, isolated loop and
        # restore whatever event-loop policy state existed before, so we never
        # leave a closed loop behind for the next caller to trip over.
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(coro)
        finally:
            try:
                new_loop.close()
            finally:
                # Drop any reference to the (now closed) loop as the current one
                # so subsequent asyncio.get_event_loop() calls create a fresh one
                # instead of returning this closed loop.
                asyncio.set_event_loop(None)

    def get_history(self) -> list[Event]:
        return list(self._history)

    def clear(self) -> None:
        self._subscribers.clear()
