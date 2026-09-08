"""Observable, fenced supervision for one durable todo execution attempt."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.event_loop.lease import (
    LeaseRenewalStatus,
    confirm_lease_termination,
    reclaim_expired_leases,
    renew_lease,
    request_lease_cancellation,
)
from general_ludd.events import CustomEvent

logger = logging.getLogger(__name__)


class EventPublisher(Protocol):
    """Minimal event-bus boundary used by the lease supervisor."""

    def publish(self, event: object) -> object:
        """Publish one event."""


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class OwnedExecutionCancelled(RuntimeError):
    """Signal that a supervised runner stopped after cancellation."""


@dataclass(frozen=True, slots=True)
class ExecutionLeaseIdentity:
    """Immutable database fence for one claimed todo attempt."""

    bucket_key: str
    holder_id: str
    todo_version: int


@dataclass(frozen=True, slots=True)
class LeaseTerminationOutcome:
    """Result of terminal proof and safe reclaim."""

    confirmed: bool
    reclaimed: int


class ExecutionLeaseSupervisor:
    """Heartbeat and cancel one exact lease through short-lived DB sessions."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        identity: ExecutionLeaseIdentity,
        event_bus: EventPublisher | None = None,
        ttl_seconds: int = 300,
        heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        """Initialize one exact-attempt supervisor.

        Args:
            session_factory: Creates an independent short-lived database session.
            identity: Immutable holder and todo-version fence.
            event_bus: Optional sink for sanitized lifecycle events.
            ttl_seconds: Lease duration applied by each heartbeat.
            heartbeat_interval_seconds: Delay between successful renewals.

        Raises:
            ValueError: If the timing configuration cannot renew before expiry.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if heartbeat_interval_seconds >= ttl_seconds:
            raise ValueError("heartbeat interval must be shorter than lease TTL")
        self._session_factory = session_factory
        self.identity = identity
        self._event_bus = event_bus
        self._ttl_seconds = ttl_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._cancel_requested = threading.Event()
        self._stop_requested = asyncio.Event()

    def is_cancellation_requested(self) -> bool:
        """Return a thread-safe cancellation signal for blocking runners."""
        return self._cancel_requested.is_set()

    def stop(self) -> None:
        """Stop future heartbeats without changing runner cancellation state."""
        self._stop_requested.set()

    def publish(self, name: str, **payload: object) -> None:
        """Emit one content-free lifecycle marker without affecting execution."""
        event_payload: dict[str, object] = {
            "bucket_key": self.identity.bucket_key,
            "holder_id": self.identity.holder_id,
            "todo_version": self.identity.todo_version,
            **payload,
        }
        logger.info("%s status=%s", name, event_payload.get("status", "unknown"))
        if self._event_bus is None:
            return
        try:
            self._event_bus.publish(
                CustomEvent(
                    name=name,
                    payload=event_payload,
                    source="execution_lease_supervisor",
                )
            )
        except Exception as exc:
            logger.warning(
                "EXECUTION_LEASE_EVENT status=failed exception_type=%s",
                type(exc).__name__,
            )

    async def heartbeat_once(self) -> LeaseRenewalStatus:
        """Renew once, failing closed when ownership cannot be proven."""
        try:
            async with self._session_factory() as session:
                try:
                    status = await renew_lease(
                        session,
                        bucket_key=self.identity.bucket_key,
                        holder_id=self.identity.holder_id,
                        todo_version=self.identity.todo_version,
                        ttl_seconds=self._ttl_seconds,
                    )
                    await session.commit()
                except Exception:
                    with contextlib.suppress(Exception):
                        await session.rollback()
                    raise
        except Exception as exc:
            self._cancel_requested.set()
            logger.error(
                "EXECUTION_LEASE_HEARTBEAT status=failed exception_type=%s",
                type(exc).__name__,
            )
            self.publish(
                "execution_lease_heartbeat",
                status="failed",
                exception_type=type(exc).__name__,
            )
            return LeaseRenewalStatus.STALE

        if status is not LeaseRenewalStatus.RENEWED:
            self._cancel_requested.set()
        self.publish("execution_lease_heartbeat", status=status.value)
        return status

    async def run(self, *, heartbeat_immediately: bool = True) -> None:
        """Heartbeat periodically until stopped or fenced out.

        ``heartbeat_immediately=False`` is used after the dispatcher has awaited
        a pre-transaction renewal.  That keeps the first periodic renewal from
        racing the job transaction on single-connection database pools while
        preserving the standalone supervisor's fail-closed immediate default.
        """
        if not heartbeat_immediately:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_requested.wait(),
                    timeout=self._heartbeat_interval_seconds,
                )
            if self._stop_requested.is_set():
                return
        while not self._stop_requested.is_set():
            status = await self.heartbeat_once()
            if status is not LeaseRenewalStatus.RENEWED:
                return
            if self._stop_requested.is_set():
                return
            try:
                await asyncio.wait_for(
                    self._stop_requested.wait(),
                    timeout=self._heartbeat_interval_seconds,
                )
            except TimeoutError:
                continue

    async def request_cancellation(self) -> bool:
        """Persist exact-owner cancellation, then signal the blocking runner."""
        requested = False
        failure_type: str | None = None
        try:
            async with self._session_factory() as session:
                try:
                    requested = await request_lease_cancellation(
                        session,
                        bucket_key=self.identity.bucket_key,
                        holder_id=self.identity.holder_id,
                        todo_version=self.identity.todo_version,
                    )
                    await session.commit()
                except Exception:
                    with contextlib.suppress(Exception):
                        await session.rollback()
                    raise
        except Exception as exc:
            failure_type = type(exc).__name__
            logger.error(
                "EXECUTION_LEASE_CANCEL status=failed exception_type=%s",
                failure_type,
            )
        finally:
            self._cancel_requested.set()

        status = "requested" if requested else "failed" if failure_type else "stale"
        event_payload: dict[str, object] = {"status": status}
        if failure_type is not None:
            event_payload["exception_type"] = failure_type
        self.publish("execution_lease_cancellation_requested", **event_payload)
        return requested

    async def confirm_termination(self) -> LeaseTerminationOutcome:
        """Persist terminal proof, then invoke the fenced reclaimer."""
        try:
            async with self._session_factory() as session:
                try:
                    confirmed = await confirm_lease_termination(
                        session,
                        bucket_key=self.identity.bucket_key,
                        holder_id=self.identity.holder_id,
                        todo_version=self.identity.todo_version,
                    )
                    reclaimed = (
                        await reclaim_expired_leases(session) if confirmed else 0
                    )
                    await session.commit()
                except Exception:
                    with contextlib.suppress(Exception):
                        await session.rollback()
                    raise
        except Exception as exc:
            logger.error(
                "EXECUTION_LEASE_TERMINATION status=failed exception_type=%s",
                type(exc).__name__,
            )
            self.publish(
                "execution_lease_termination_confirmed",
                status="failed",
                exception_type=type(exc).__name__,
                reclaimed=0,
            )
            return LeaseTerminationOutcome(confirmed=False, reclaimed=0)

        status = "confirmed" if confirmed else "stale"
        self.publish(
            "execution_lease_termination_confirmed",
            status=status,
            reclaimed=reclaimed,
        )
        return LeaseTerminationOutcome(confirmed=confirmed, reclaimed=reclaimed)


__all__ = (
    "ExecutionLeaseIdentity",
    "ExecutionLeaseSupervisor",
    "LeaseTerminationOutcome",
    "OwnedExecutionCancelled",
)
