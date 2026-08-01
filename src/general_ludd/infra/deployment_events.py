"""Persist streamed Terraform events and wake work consumers on terminal states."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import AuditEventModel
from general_ludd.events import EventBus, EventType
from general_ludd.events.types import Event

_TERMINAL_EVENTS = frozenset(
    {
        "terraform_deploy_completed",
        "terraform_deploy_failed",
    }
)


class TerraformEventBridge:
    """Bridge a worker-local EventBus into the shared durable database.

    PostgreSQL remains the source of truth across Gunicorn processes. Every
    streamed Terraform lifecycle event is committed as an audit row; terminal
    events also wake the local EventLoop immediately so it can claim newly
    runnable database work without waiting for the next periodic tick.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        wake: Callable[[], None] | None = None,
        worker_id: str = "worker",
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._wake = wake
        self._worker_id = worker_id[:64]
        self._subscription_id: str | None = None

    def start(self) -> None:
        if self._subscription_id is None:
            self._subscription_id = self._event_bus.subscribe(
                EventType.CUSTOM,
                self._receive,
            )

    def close(self) -> None:
        if self._subscription_id is not None:
            self._event_bus.unsubscribe(self._subscription_id)
            self._subscription_id = None

    def _receive(self, event: Event) -> Awaitable[None] | None:
        name = event.payload.get("name")
        if not isinstance(name, str) or not name.startswith("terraform_"):
            return None
        if name in _TERMINAL_EVENTS and self._wake is not None:
            self._wake()
        return self._persist(event, name)

    async def _persist(self, event: Event, name: str) -> None:
        details = {
            **event.payload,
            "event_id": event.event_id,
            "source": event.source,
            "timestamp": event.timestamp,
        }
        project_id = event.payload.get("project_id")
        if not isinstance(project_id, str):
            project_id = None
        async with self._session_factory() as session:
            session.add(
                AuditEventModel(
                    event_type=name,
                    project_id=project_id,
                    actor=self._worker_id,
                    entity_type="terraform_deployment",
                    entity_id=event.event_id,
                    correlation_id=event.correlation_id,
                    details=json.dumps(details, sort_keys=True),
                )
            )
            await session.commit()
