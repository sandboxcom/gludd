"""Turn watchdog stall events into bounded, durable operator blockers.

The watchdog invokes EventBus subscribers from its sweeper thread.  This
subscriber therefore copies only bounded scalar evidence and hands persistence
back to the daemon's existing asyncio loop.  It never stores captured stacks,
cancels the stalled operation, or retries a failed database write.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import deque
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import HumanTodoModel
from general_ludd.db.repository import HumanTodoRepository
from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event, EventType

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 160
MAX_BODY_CHARS = 512
MAX_OPERATION_CHARS = 96
MAX_IDENTITY_CHARS = 96
MAX_TRACKED_IDENTITIES = 1_024
MAX_PENDING_WRITES = 64
_MAX_SECONDS = 31_536_000.0
_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9._:-]+")


@runtime_checkable
class SessionFactory(Protocol):
    """Callable creating one short-lived async database session."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


@dataclass(frozen=True, slots=True)
class _StallSnapshot:
    """Bounded, stack-free evidence copied at the EventBus boundary."""

    session_id: str
    operation: str
    task_identity: str
    elapsed_s: float
    deadline_s: float


def _bounded_text(value: object, limit: int, *, fallback: str) -> str:
    """Collapse control/whitespace runs and cap persisted untrusted text."""
    text = " ".join(str(value or "").split())
    return (text or fallback)[:limit]


def _bounded_token(value: object, limit: int, *, fallback: str) -> str:
    """Return a bounded identifier without whitespace or control characters."""
    token = _SAFE_TOKEN.sub("_", str(value or "")).strip("_")
    return (token or fallback)[:limit]


def _bounded_seconds(value: object) -> float:
    """Normalize event timing into a finite, non-negative display bound."""
    try:
        number = float(str(value))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(max(number, 0.0), _MAX_SECONDS)


def _snapshot(event: Event) -> _StallSnapshot:
    event_identity = _bounded_token(event.event_id, MAX_IDENTITY_CHARS, fallback="unknown-event")
    return _StallSnapshot(
        session_id=f"stall:{event_identity}",
        operation=_bounded_text(
            event.payload.get("operation"),
            MAX_OPERATION_CHARS,
            fallback="unknown operation",
        ),
        task_identity=_bounded_token(
            event.correlation_id,
            MAX_IDENTITY_CHARS,
            fallback="unknown-task",
        ),
        elapsed_s=_bounded_seconds(event.payload.get("elapsed_s")),
        deadline_s=_bounded_seconds(event.payload.get("deadline_s")),
    )


def _title(snapshot: _StallSnapshot) -> str:
    return f"Agent stall requires operator review: {snapshot.operation}"[:MAX_TITLE_CHARS]


def _body(snapshot: _StallSnapshot) -> str:
    text = (
        f"Operation {snapshot.operation!r} (task {snapshot.task_identity}) exceeded its monotonic "
        f"deadline: elapsed={snapshot.elapsed_s:.3f}s; deadline={snapshot.deadline_s:.3f}s. "
        "Inspect live diagnostics, then resolve or dismiss this blocker. The watchdog did not "
        "cancel or retry the stalled operation."
    )
    return text[:MAX_BODY_CHARS]


class StallEscalationSubscriber:
    """Persist one HumanTodo per stall event without blocking its watchdog.

    Event identities are remembered in a bounded FIFO.  An identity is retained
    after a database error deliberately: persistence has at-most-once semantics
    and a failed write is observable but never retried behind the operator's
    back.  A matching durable ``session_id`` also prevents replay duplication.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionFactory,
        loop: asyncio.AbstractEventLoop,
        log: logging.Logger | None = None,
    ) -> None:
        """Bind the existing event bus, database factory, and daemon loop."""
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._loop = loop
        self._logger = log or logger
        self._subscription_id: str | None = None
        self._closed = False
        self._seen_order: deque[str] = deque()
        self._seen: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        """Subscribe idempotently to stall events."""
        if self._subscription_id is not None:
            return
        self._closed = False
        self._subscription_id = self._event_bus.subscribe(EventType.STALL_DETECTED, self._on_event)

    def _on_event(self, event: Event) -> None:
        """Copy safe evidence and hand work to the daemon loop thread-safely."""
        snapshot = _snapshot(event)
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self._loop:
            self._schedule(snapshot)
            return
        try:
            self._loop.call_soon_threadsafe(self._schedule, snapshot)
        except RuntimeError:
            self._logger.error(
                "stall escalation scheduling failed identity=%s",
                snapshot.session_id,
            )

    def _remember(self, identity: str) -> None:
        self._seen.add(identity)
        self._seen_order.append(identity)
        while len(self._seen_order) > MAX_TRACKED_IDENTITIES:
            self._seen.discard(self._seen_order.popleft())

    def _schedule(self, snapshot: _StallSnapshot) -> None:
        if self._closed or snapshot.session_id in self._seen:
            return
        self._remember(snapshot.session_id)
        if len(self._tasks) >= MAX_PENDING_WRITES:
            self._logger.error(
                "stall escalation capacity exhausted identity=%s pending=%d",
                snapshot.session_id,
                len(self._tasks),
            )
            return
        task = self._loop.create_task(
            self._persist(snapshot),
            name=f"stall-escalation-{snapshot.session_id[-24:]}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            self._logger.error("stall escalation task was externally cancelled")
            return
        error = task.exception()
        if error is not None:  # defensive: _persist contains ordinary failures
            self._logger.error("stall escalation task failed: %s", error)

    async def _persist(self, snapshot: _StallSnapshot) -> None:
        try:
            async with self._session_factory() as session:
                existing = await session.scalar(
                    select(HumanTodoModel.id)
                    .where(
                        HumanTodoModel.agent_id == "stall-watchdog",
                        HumanTodoModel.session_id == snapshot.session_id,
                    )
                    .limit(1)
                )
                if existing is not None:
                    return
                repository = HumanTodoRepository(session)
                await repository.create(
                    agent_id="stall-watchdog",
                    title=_title(snapshot),
                    body=_body(snapshot),
                    category="blocker",
                    priority="urgent",
                    session_id=snapshot.session_id,
                    tags=["agent-stall", "operator-action"],
                )
                await session.commit()
        except Exception as exc:
            self._logger.error(
                "stall escalation persistence failed identity=%s: %s",
                snapshot.session_id,
                exc,
            )

    async def drain(self) -> None:
        """Wait for writes already accepted by this subscriber; never cancel."""
        await asyncio.sleep(0)
        while self._tasks:
            accepted = tuple(self._tasks)
            await asyncio.gather(*accepted, return_exceptions=True)
            self._tasks.difference_update(task for task in accepted if task.done())

    async def aclose(self) -> None:
        """Stop accepting events and durably finish accepted writes."""
        self._closed = True
        subscription_id = self._subscription_id
        self._subscription_id = None
        if subscription_id is not None:
            self._event_bus.unsubscribe(subscription_id)
        await self.drain()


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_PENDING_WRITES",
    "MAX_TITLE_CHARS",
    "StallEscalationSubscriber",
]
