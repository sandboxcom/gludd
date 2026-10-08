"""Repository implementations for the agentic harness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from general_ludd.db.models import (
    AgentMessageModel,
    AuditEventModel,
    AuditEventType,
)

MAX_INBOX_MESSAGES = 100
_SQLITE_EXPIRY_TOLERANCE_SECONDS = 0.001


def _utc_now() -> datetime:
    """Return the clock instant shared by one repository operation."""
    return datetime.now(UTC)


def _message_is_expired(
    created_at: datetime | None,
    ttl_seconds: int | None,
    now: datetime,
) -> bool:
    """Return whether a message is no longer admissible at ``now``.

    TTL admission is fail-closed at the exact boundary. Database rows always
    have ``created_at`` and positive TTL values, while the defensive checks keep
    degraded or manually constructed rows from becoming immortal.
    """
    if ttl_seconds is None:
        return False
    if created_at is None or ttl_seconds <= 0:
        return True
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return (now - created_at).total_seconds() >= ttl_seconds


def _message_age_seconds(now: datetime, dialect_name: str) -> ColumnElement[float]:
    """Build the backend-specific elapsed-seconds expression for one query."""
    if dialect_name == "postgresql":
        return cast(
            "ColumnElement[float]",
            func.extract("epoch", literal(now) - AgentMessageModel.created_at),
        )
    sqlite_age = cast(
        "ColumnElement[float]",
        (func.julianday(literal(now)) - func.julianday(AgentMessageModel.created_at))
        * 86400.0,
    )
    # SQLite's Julian-day representation has sub-millisecond floating-point
    # drift. Bias that uncertainty toward rejection so an exact-boundary row
    # can never be admitted as live.
    return sqlite_age + _SQLITE_EXPIRY_TOLERANCE_SECONDS


def _live_message_predicate(now: datetime, dialect_name: str) -> ColumnElement[bool]:
    """Admit only rows whose TTL has not reached its boundary."""
    age_seconds = _message_age_seconds(now, dialect_name)
    return or_(
        AgentMessageModel.ttl_seconds.is_(None),
        and_(
            AgentMessageModel.ttl_seconds > 0,
            age_seconds < AgentMessageModel.ttl_seconds,
        ),
    )


def _expired_message_predicate(now: datetime, dialect_name: str) -> ColumnElement[bool]:
    """Select TTL rows at or beyond their fail-closed expiry boundary."""
    age_seconds = _message_age_seconds(now, dialect_name)
    return and_(
        AgentMessageModel.ttl_seconds.is_not(None),
        or_(
            AgentMessageModel.ttl_seconds <= 0,
            age_seconds >= AgentMessageModel.ttl_seconds,
        ),
    )


class AuditEventRepository:
    """Persist append-only audit events and expose bounded history queries."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def create(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        project_id: str | None = None,
        details: str = "{}",
    ) -> AuditEventModel:
        """Persist an audit event attributed to a project.

        Raises:
            ValueError: If ``project_id`` is omitted.
        """
        if project_id is None:
            raise ValueError(
                "project_id is required for audit events — NULL project_id silently orphans the event from its project"
            )
        row = AuditEventModel(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            details=details or "{}",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def record_typed(
        self,
        event_type: AuditEventType,
        entity_type: str,
        entity_id: str,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEventModel:
        """Record an audit event from the typed AuditEventType taxonomy.

        Serializes ``details`` to JSON and delegates to :meth:`create`. This is
        the typed entry point the event loop uses so audit rows carry values
        from the AuditEventType enum rather than ad-hoc magic strings.
        """
        import json as _json

        return await self.create(
            event_type=event_type.value,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            details=_json.dumps(details) if details is not None else "{}",
        )

    async def list_by_entity(self, entity_type: str, entity_id: str, limit: int = 50) -> list[AuditEventModel]:
        """List recent audit events for one entity."""
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.entity_type == entity_type, AuditEventModel.entity_id == entity_id)
            .order_by(AuditEventModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_project(self, project_id: str, limit: int = 50) -> list[AuditEventModel]:
        """List recent audit events attributed to a project."""
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.project_id == project_id)
            .order_by(AuditEventModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
BROADCAST_RECIPIENT = "broadcast"


class AgentMessageRepository:
    """Persistence for the inter-agent message queue (AgentMessageModel)."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an asynchronous database session."""
        self._session = session

    async def send(
        self,
        data: dict[str, Any] | None = None,
        *,
        sender: str | None = None,
        recipient: str | None = None,
        topic: str = "",
        body: str = "",
        project_id: str | None = None,
        priority: str = "normal",
        ttl_seconds: int | None = None,
    ) -> AgentMessageModel:
        """Persist a message using mapping or keyword-style input."""
        keyword_style = data is None and sender is not None
        payload = dict(data or {})
        if sender is not None:
            payload["sender"] = sender
        if recipient is not None:
            payload["recipient"] = recipient
        if topic or "topic" not in payload:
            payload["topic"] = topic
        if body or "body" not in payload:
            payload["body"] = body
        if project_id is not None:
            payload["project_id"] = project_id
        if priority != "normal" or "priority" not in payload:
            payload["priority"] = priority
        if ttl_seconds is not None:
            payload["ttl_seconds"] = ttl_seconds
        row = AgentMessageModel(**payload)
        if keyword_style:
            # Preserve the legacy row-returning mapping API while supporting
            # the newer keyword API's boolean acknowledgement contract.
            row.__dict__["_keyword_style"] = True
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, message_id: str) -> AgentMessageModel | None:
        """Return an inter-agent message by identifier."""
        stmt = select(AgentMessageModel).where(AgentMessageModel.id == message_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def inbox(
        self,
        recipient: str,
        unread_only: bool = True,
        include_broadcast: bool = True,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentMessageModel]:
        """Return messages addressed to ``recipient`` (and broadcasts).

        Expired messages (past their ttl) are never returned.
        """
        target: Any
        if include_broadcast:
            target = AgentMessageModel.recipient.in_([recipient, BROADCAST_RECIPIENT])
        else:
            target = AgentMessageModel.recipient == recipient
        now = _utc_now()
        dialect_name = self._session.get_bind().dialect.name
        bounded_limit = min(max(limit, 0), MAX_INBOX_MESSAGES)
        stmt = select(AgentMessageModel).where(
            target,
            _live_message_predicate(now, dialect_name),
        )
        if unread_only:
            stmt = stmt.where(AgentMessageModel.read_at.is_(None))
        if project_id is not None:
            stmt = stmt.where(
                or_(
                    AgentMessageModel.project_id == project_id,
                    AgentMessageModel.project_id.is_(None),
                )
            )
        stmt = stmt.order_by(
            AgentMessageModel.created_at.asc(),
            AgentMessageModel.id.asc(),
        ).limit(bounded_limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def ack(self, message_id: str, project_id: str | None = None) -> AgentMessageModel | bool | None:
        """Mark a message read. Returns the row, or None if it does not exist.

        XT-11: when ``project_id`` is supplied, a message belonging to another
        project is treated as not-found (the ack neither reads nor mutates it),
        so a caller scoped to project A cannot mark project B's message read by
        guessing its id. ``project_id=None`` preserves the unscoped/admin path.
        """
        from sqlalchemy import update as _update

        row = await self.get_by_id(message_id)
        if row is None:
            return None
        if project_id is not None and row.project_id != project_id:
            return None
        # Guarded conditional UPDATE on read_at IS NULL: the read-then-mutate form
        # let two concurrent acks both see read_at None and both write, clobbering
        # the first ack's timestamp. Guarding on read_at IS NULL means only the
        # first ack writes; a later ack affects zero rows and leaves it untouched.
        now = _utc_now()
        guard = _update(AgentMessageModel).where(
            AgentMessageModel.id == message_id,
            AgentMessageModel.read_at.is_(None),
        )
        if project_id is not None:
            # Atomic backstop: the UPDATE itself refuses a cross-project row.
            guard = guard.where(AgentMessageModel.project_id == project_id)
        guard = guard.values(read_at=now)
        await self._session.execute(guard)
        await self._session.flush()
        await self._session.refresh(row)
        if getattr(row, "_keyword_style", False):
            return True
        return row

    async def purge(self) -> int:
        """Compatibility alias for purge_expired."""
        return await self.purge_expired()

    async def purge_expired(self) -> int:
        """Delete every message whose ttl has elapsed. Returns the count purged.

        Single set-based DELETE pushed into SQL rather than fetch-all + per-row
        ``session.delete()``: the expiry predicate mirrors :meth:`_is_expired`
        (``elapsed_seconds >= ttl_seconds``), using PostgreSQL epoch extraction
        or fail-closed SQLite Julian-day arithmetic. AgentMessageModel declares
        no child relationships (its only FK is ``project_id``
        ``ondelete=SET NULL``, an outbound reference), so the bulk delete
        bypasses no ORM cascade.
        """
        from sqlalchemy import delete

        now = _utc_now()
        dialect_name = self._session.get_bind().dialect.name
        stmt = delete(AgentMessageModel).where(
            _expired_message_predicate(now, dialect_name)
        )
        result = await self._session.execute(stmt)
        purged = int(cast("CursorResult[Any]", result).rowcount or 0)
        if purged:
            await self._session.flush()
        return purged

    async def unread_counts(self, project_id: str | None = None) -> dict[str, int]:
        """Per-recipient unread counts (excludes expired). Used by /api/facts.

        The TTL cutoff is pushed into the WHERE clause (a row survives when it
        has no ttl or its elapsed seconds are still within ttl, mirroring
        :meth:`_is_expired`) and the per-recipient tally is a SQL ``GROUP BY``
        instead of a full-table load + Python aggregation. ``recipient`` is
        NOT NULL, so there is no None bucket.
        """
        now = _utc_now()
        dialect_name = self._session.get_bind().dialect.name
        stmt = (
            select(AgentMessageModel.recipient, func.count())
            .where(
                AgentMessageModel.read_at.is_(None),
                _live_message_predicate(now, dialect_name),
            )
            .group_by(AgentMessageModel.recipient)
        )
        if project_id is not None:
            stmt = stmt.where(
                or_(
                    AgentMessageModel.project_id == project_id,
                    AgentMessageModel.project_id.is_(None),
                )
            )
        result = await self._session.execute(stmt)
        return {recipient: count for recipient, count in result.all()}

    @staticmethod
    def _is_expired(row: AgentMessageModel, now: datetime) -> bool:
        return _message_is_expired(row.created_at, row.ttl_seconds, now)
