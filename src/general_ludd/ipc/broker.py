"""IPC broker for the gunicorn multi-worker architecture (Phase 1).

The daemon historically ran in a single process, so all pub/sub was an
in-process fan-out. Once gunicorn pre-forks the app into multiple workers,
that fan-out no longer crosses worker boundaries. The :class:`Broker`
Protocol below is the seam along which the daemon's pub/sub will be swapped
from :class:`InProcessBroker` (the default, no-op-from-an-IPC-standpoint
implementation) to a real cross-worker broker (Redis pub/sub, POSIX MQ, etc.)
without touching call sites.

This module ships only the in-process default. ``publish``/``subscribe`` here
behave exactly like the existing :class:`general_ludd.events.bus.EventBus`
delivery semantics (sync + async handlers, exception isolation, FIFO order)
so the eventual transport swap is behavior-preserving.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = ["Broker", "Handler", "InProcessBroker", "Message"]

Message = dict[str, Any]
Handler = Callable[[Message], Any]


@runtime_checkable
class Broker(Protocol):
    """Transport-agnostic pub/sub surface the daemon depends on.

    A future Redis/PosixMQ broker implements the same four methods; call sites
    never need to know which transport is live. The ``@runtime_checkable``
    decorator lets tests (and daemon wiring) assert a candidate satisfies the
    contract via ``isinstance``.
    """

    async def publish(self, topic: str, message: Message) -> int:
        """Deliver ``message`` to every subscriber of ``topic``.

        Returns the count of handlers that were successfully delivered to
        (sync handlers that returned without raising, plus async handlers
        that were awaited to completion without raising). Failing handlers
        are logged and excluded from the count, but never block delivery to
        the remaining subscribers.
        """
        ...

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Register ``handler`` to receive every subsequent publish to ``topic``."""
        ...

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        """Remove ``handler`` from ``topic``. No-op if it was never registered."""
        ...

    def clear(self) -> None:
        """Drop every subscriber on every topic."""
        ...


class InProcessBroker:
    """Default broker. All communication stays in-process; no IPC crosses workers.

    This is the Phase 1 implementation — it exists so the daemon can depend on
    the :class:`Broker` Protocol today, before a real cross-worker transport
    exists. Delivery semantics mirror :class:`general_ludd.events.bus.EventBus`:

    * sync and async (coroutine) handlers are both supported;
    * handlers are invoked in subscription order (FIFO);
    * a handler that raises (sync) or rejects (async) is logged at ``ERROR``
      and excluded from the returned delivery count, but does NOT abort
      delivery to the remaining handlers on that topic;
    * publishing to a topic with no subscribers is silently dropped and
      returns ``0``.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    async def publish(self, topic: str, message: Message) -> int:
        delivered = 0
        # Snapshot the handler list so a handler that (un)subscribes during
        # dispatch does not mutate the iteration.
        for handler in list(self._subs.get(topic, [])):
            try:
                result = handler(message)
                if asyncio.iscoroutine(result):
                    await result
                delivered += 1
            except Exception:
                logger.exception(
                    "broker handler failed for topic %s", topic
                )
        return delivered

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        # Handler was never registered for this topic (or already removed).
        # Unsubscribe is idempotent; mirror EventBus's tolerance.
        with contextlib.suppress(ValueError):
            self._subs[topic].remove(handler)

    def clear(self) -> None:
        self._subs.clear()
