"""Repository implementations for the agentic harness."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import MemoryRecordModel
from general_ludd.db.repositories.shared import current_list_limit


class MemoryRepository:
    """Persistence for agent-memory key-value records (G1).

    Each record is scoped to an (agent_id, namespace) pair and keyed by
    ``key``. TTL support allows automatic expiry of transient entries.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Initialize with either a session or a transactional session factory."""
        self._session = session
        self._session_factory = session_factory

    @contextlib.asynccontextmanager
    async def _resolve_session(self) -> AsyncGenerator[AsyncSession, None]:
        if self._session is not None:
            yield self._session
        elif self._session_factory is not None:
            async with self._session_factory() as session, session.begin():
                yield session
        else:
            raise RuntimeError("MemoryRepository: no session or session_factory")

    async def _get_with_session(
        self,
        session: AsyncSession,
        agent_id: str,
        key: str,
        namespace: str,
        project_id: str | None = None,
    ) -> MemoryRecordModel | None:
        stmt = select(MemoryRecordModel).where(
            MemoryRecordModel.agent_id == agent_id,
            MemoryRecordModel.key == key,
            MemoryRecordModel.namespace == namespace,
        )
        if project_id is None:
            stmt = stmt.where(MemoryRecordModel.project_id.is_(None))
        else:
            stmt = stmt.where(MemoryRecordModel.project_id == project_id)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None and self._is_expired(row):
            await session.delete(row)
            await session.flush()
            return None
        return row

    async def get(
        self,
        agent_id: str,
        key: str,
        namespace: str = "default",
        project_id: str | None = None,
    ) -> MemoryRecordModel | None:
        """Return an unexpired memory value from an agent namespace."""
        async with self._resolve_session() as session:
            row = await self._get_with_session(session, agent_id, key, namespace, project_id)
            return self._detach_factory_owned(session, row)

    async def set(
        self,
        agent_id: str,
        key: str,
        value: str | None = None,
        namespace: str = "default",
        project_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> MemoryRecordModel:
        """Upsert and return a scoped agent-memory value with optional TTL.

        A ``value`` of ``None`` preserves the existing value on update (used by
        the API when the client omits the field); on create it stores ``""``.
        """
        async with self._resolve_session() as session:
            now = datetime.now(UTC)
            stmt = select(MemoryRecordModel).where(
                MemoryRecordModel.agent_id == agent_id,
                MemoryRecordModel.key == key,
                MemoryRecordModel.namespace == namespace,
            )
            if project_id is None:
                stmt = stmt.where(MemoryRecordModel.project_id.is_(None))
            else:
                stmt = stmt.where(MemoryRecordModel.project_id == project_id)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                existing = MemoryRecordModel(
                    agent_id=agent_id,
                    key=key,
                    value=value or "",
                    namespace=namespace,
                    project_id=project_id,
                    ttl_seconds=ttl_seconds,
                    created_at=now,
                    updated_at=now,
                )
                session.add(existing)
            else:
                if value is not None:
                    existing.value = value
                existing.ttl_seconds = ttl_seconds
                existing.updated_at = now
            await session.flush()
            await session.refresh(existing)
            if self._session is None:
                session.expunge(existing)
            return existing

    async def delete(
        self,
        agent_id: str,
        key: str,
        namespace: str = "default",
        project_id: str | None = None,
    ) -> bool:
        """Delete a scoped agent-memory value and report whether it existed."""
        async with self._resolve_session() as session:
            row = await self._get_with_session(session, agent_id, key, namespace, project_id)
            if row is None:
                return False
            await session.delete(row)
            await session.flush()
            return True

    async def list_by_namespace(
        self,
        agent_id: str,
        namespace: str = "default",
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryRecordModel]:
        """List bounded, unexpired values from exactly one project partition.

        An omitted ``project_id`` selects only global rows, matching ``get`` and
        ``delete`` rather than widening the query across every project.
        """
        async with self._resolve_session() as session:
            stmt = (
                select(MemoryRecordModel)
                .where(
                    MemoryRecordModel.agent_id == agent_id,
                )
                .order_by(MemoryRecordModel.key)
                .limit(min(limit, current_list_limit()))
            )
            if namespace != "*":
                stmt = stmt.where(MemoryRecordModel.namespace == namespace)
            if project_id is None:
                stmt = stmt.where(MemoryRecordModel.project_id.is_(None))
            else:
                stmt = stmt.where(MemoryRecordModel.project_id == project_id)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            live_rows = [row for row in rows if not self._is_expired(row)]
            if self._session is None:
                for row in live_rows:
                    session.expunge(row)
            return live_rows

    async def purge_expired(self) -> int:
        """Delete expired memory records and return the number removed."""
        from sqlalchemy import delete, func

        async with self._resolve_session() as session:
            elapsed_seconds = (func.julianday("now") - func.julianday(MemoryRecordModel.created_at)) * 86400.0
            stmt = delete(MemoryRecordModel).where(
                MemoryRecordModel.ttl_seconds.isnot(None),
                elapsed_seconds > MemoryRecordModel.ttl_seconds,
            )
            result = await session.execute(stmt)
            purged = int(cast("CursorResult[Any]", result).rowcount or 0)
            if purged:
                await session.flush()
            return purged

    @staticmethod
    def _is_expired(row: MemoryRecordModel | None) -> bool:
        if row is None or row.ttl_seconds is None:
            return False
        now = datetime.now(UTC)
        created = row.created_at
        if created is None:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (now - created).total_seconds() > row.ttl_seconds

    def _detach_factory_owned(
        self,
        session: AsyncSession,
        row: MemoryRecordModel | None,
    ) -> MemoryRecordModel | None:
        """Detach a returned row only when this repository owns its session."""
        if row is not None and self._session is None:
            session.expunge(row)
        return row
