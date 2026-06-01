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
                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(result)
                        task.add_done_callback(self._background_tasks.discard)
                        self._background_tasks.add(task)
                    except RuntimeError:
                        asyncio.run(result)
                elif inspect.iscoroutinefunction(callback):
                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(callback(event))
                        task.add_done_callback(self._background_tasks.discard)
                        self._background_tasks.add(task)
                    except RuntimeError:
                        asyncio.run(callback(event))
            except Exception as exc:
                logger.warning("Event subscriber %s error: %s", sub_id, exc)

        if self._history_size > 0:
            self._history.append(event)
            if len(self._history) > self._history_size:
                self._history = self._history[-self._history_size:]

        return len(all_subs)

    def get_history(self) -> list[Event]:
        return list(self._history)

    def clear(self) -> None:
        self._subscribers.clear()
