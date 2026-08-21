"""Persist streamed Terraform events and wake work consumers on terminal states."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from sqlalchemy import func, select, text
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
_WAKEUP_CHANNEL = "gludd_terraform_ready"

logger = logging.getLogger(__name__)


def _emit_wakeup_progress(message: str) -> None:
    """Surface cross-worker wake lifecycle even when app loggers are filtered."""
    print(message, flush=True)


@runtime_checkable
class WakeupListener(Protocol):
    """Define the lifecycle required by Terraform wakeup listeners."""

    def start(self) -> None:
        """Start listening for wakeup notifications."""
        ...

    def close(self) -> None:
        """Request synchronous listener shutdown."""
        ...

    async def aclose(self) -> None:
        """Await complete listener shutdown."""
        ...


class PostgresWakeupListener:
    """Listen for transactional Terraform wakeups on a dedicated connection.

    LISTEN is established before the durable audit-table catch-up query. This
    closes the usual subscribe/query race: an event is either in catch-up, in a
    notification, or harmlessly in both (the audit id deduplicates it).
    """

    def __init__(
        self,
        *,
        database_url: str,
        session_factory: async_sessionmaker[AsyncSession],
        wake: Callable[[], None],
        worker_id: str,
        reconnect_min_seconds: float = 0.1,
        reconnect_max_seconds: float = 5.0,
    ) -> None:
        """Initialize a namespaced PostgreSQL wakeup listener."""
        self._database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self._session_factory = session_factory
        self._wake = wake
        self._worker_id = worker_id[:64]
        self._reconnect_min_seconds = reconnect_min_seconds
        self._reconnect_max_seconds = reconnect_max_seconds
        self._last_audit_event_id = 0
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closed = False

    @property
    def ready(self) -> bool:
        """Return whether the PostgreSQL subscription is ready."""
        return self._ready.is_set()

    def start(self) -> None:
        """Start the listener task if it is not already running."""
        if self._task is None or self._task.done():
            self._closed = False
            self._task = asyncio.create_task(
                self._run(),
                name=f"gludd-pg-wakeup-{self._worker_id}-{os.getpid()}",
            )

    async def wait_ready(self, timeout: float = 10.0) -> None:
        """Wait up to ``timeout`` seconds for subscription readiness."""
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    def close(self) -> None:
        """Request listener shutdown without waiting for task completion."""
        self._closed = True
        if self._task is not None:
            self._task.cancel()

    async def aclose(self) -> None:
        """Cancel and await the listener task, leaving no owned task behind."""
        self.close()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._ready.clear()
        logger.info(
            "Terraform PostgreSQL wake listener closed worker=%s pid=%d",
            self._worker_id,
            os.getpid(),
        )
        _emit_wakeup_progress(
            "Terraform PostgreSQL wake listener closed "
            f"worker={self._worker_id} pid={os.getpid()}"
        )

    async def _run(self) -> None:
        delay = self._reconnect_min_seconds
        while not self._closed:
            try:
                await self._listen_once()
                if not self._closed:
                    raise ConnectionError("PostgreSQL notification stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._ready.clear()
                logger.warning(
                    "Terraform PostgreSQL wake listener reconnecting worker=%s pid=%d delay=%.2fs error=%s",
                    self._worker_id,
                    os.getpid(),
                    delay,
                    type(error).__name__,
                )
                _emit_wakeup_progress(
                    "Terraform PostgreSQL wake listener reconnecting "
                    f"worker={self._worker_id} pid={os.getpid()} "
                    f"delay={delay:.2f}s error={type(error).__name__}"
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_seconds)
            else:
                delay = self._reconnect_min_seconds

    async def _listen_once(self) -> None:
        import psycopg

        connection = await psycopg.AsyncConnection.connect(
            self._database_url,
            autocommit=True,
            application_name=f"gludd-wakeup:{self._worker_id}:{os.getpid()}",
        )
        async with connection:
            await connection.execute(f"LISTEN {_WAKEUP_CHANNEL}")
            self._ready.set()
            caught_up = await self.catch_up()
            logger.info(
                "Terraform PostgreSQL wake listener ready worker=%s pid=%d catchup=%d",
                self._worker_id,
                os.getpid(),
                caught_up,
            )
            _emit_wakeup_progress(
                "Terraform PostgreSQL wake listener ready "
                f"worker={self._worker_id} pid={os.getpid()} catchup={caught_up}"
            )
            async for notification in connection.notifies():
                if self._closed:
                    break
                self.handle_notification(notification.payload)

    def handle_notification(self, payload: str) -> None:
        """Wake local work for a new notification, deduplicating audit IDs."""
        try:
            audit_event_id = int(json.loads(payload)["audit_event_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning(
                "Terraform PostgreSQL wake notification malformed worker=%s pid=%d",
                self._worker_id,
                os.getpid(),
            )
            self._wake()
            return
        if audit_event_id <= self._last_audit_event_id:
            return
        self._last_audit_event_id = audit_event_id
        self._wake()
        logger.info(
            "Terraform PostgreSQL wake notification received worker=%s pid=%d audit_event_id=%d",
            self._worker_id,
            os.getpid(),
            audit_event_id,
        )
        _emit_wakeup_progress(
            "Terraform PostgreSQL wake notification received "
            f"worker={self._worker_id} pid={os.getpid()} audit_event_id={audit_event_id}"
        )

    async def catch_up(self) -> int:
        """Wake local work for durable terminal events missed while offline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count(), func.max(AuditEventModel.id)).where(
                    AuditEventModel.id > self._last_audit_event_id,
                    AuditEventModel.event_type.in_(_TERMINAL_EVENTS),
                )
            )
            count, max_id = result.one()
        caught_up = int(count or 0)
        if caught_up and max_id is not None:
            self._last_audit_event_id = int(max_id)
            self._wake()
            logger.info(
                "Terraform PostgreSQL wake catch-up worker=%s pid=%d events=%d latest=%d",
                self._worker_id,
                os.getpid(),
                caught_up,
                self._last_audit_event_id,
            )
            _emit_wakeup_progress(
                "Terraform PostgreSQL wake catch-up "
                f"worker={self._worker_id} pid={os.getpid()} events={caught_up} "
                f"latest={self._last_audit_event_id}"
            )
        return caught_up


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
        listener: WakeupListener | None = None,
    ) -> None:
        """Initialize the event bridge and its optional wakeup listener."""
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._wake = wake
        self._worker_id = worker_id[:64]
        self._listener = listener
        self._subscription_id: str | None = None

    def start(self) -> None:
        """Subscribe the bridge and start its listener exactly once."""
        if self._subscription_id is None:
            self._subscription_id = self._event_bus.subscribe(
                EventType.CUSTOM,
                self._receive,
            )
            if self._listener is not None:
                self._listener.start()

    def close(self) -> None:
        """Unsubscribe and request synchronous listener shutdown."""
        if self._subscription_id is not None:
            self._event_bus.unsubscribe(self._subscription_id)
            self._subscription_id = None
        if self._listener is not None:
            self._listener.close()

    async def aclose(self) -> None:
        """Unsubscribe and await complete listener shutdown."""
        if self._subscription_id is not None:
            self._event_bus.unsubscribe(self._subscription_id)
            self._subscription_id = None
        if self._listener is not None:
            await self._listener.aclose()

    def _receive(self, event: Event) -> Awaitable[None] | None:
        name = event.payload.get("name")
        if not isinstance(name, str) or not name.startswith("terraform_"):
            return None
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
            row = AuditEventModel(
                    event_type=name,
                    project_id=project_id,
                    actor=self._worker_id,
                    entity_type="terraform_deployment",
                    entity_id=event.event_id,
                    correlation_id=event.correlation_id,
                    details=json.dumps(details, sort_keys=True),
                )
            session.add(row)
            await session.flush()
            if name in _TERMINAL_EVENTS and session.get_bind().dialect.name == "postgresql":
                await session.execute(
                    text(f"SELECT pg_notify('{_WAKEUP_CHANNEL}', :payload)"),
                    {
                        "payload": json.dumps(
                            {
                                "audit_event_id": row.id,
                                "event_id": event.event_id,
                            },
                            separators=(",", ":"),
                        )
                    },
                )
            await session.commit()
        if name in _TERMINAL_EVENTS and self._wake is not None:
            self._wake()
