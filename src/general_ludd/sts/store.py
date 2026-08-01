"""TokenStore — persist and retrieve AgentTokenModel records.

Never stores secret_id — that lives only in OpenBao.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from general_ludd.db.models import AgentTokenModel

logger = logging.getLogger(__name__)


class TokenStore:
    """Persists ``AgentTokenModel`` rows via the async session factory."""

    def __init__(self, session_factory: async_sessionmaker[Any]) -> None:
        self._session_factory = session_factory

    async def store(self, token_record: AgentTokenModel) -> None:
        """Insert or merge *token_record* into the DB."""
        async with self._session_factory.begin() as session:
            add_result = session.add(token_record)
            if inspect.isawaitable(add_result):
                await add_result

    async def get(self, agent_id: str) -> AgentTokenModel | None:
        """Return the token record for *agent_id*, or None."""
        from general_ludd.db.models import AgentTokenModel as A

        async with self._session_factory() as session:
            stmt = select(A).where(A.agent_id == agent_id)
            result = await session.execute(stmt)
            row: AgentTokenModel | None = result.scalar_one_or_none()
            return row

    async def revoke(self, token_id: str) -> None:
        """Mark the token *token_id* as revoked (soft-delete with timestamp)."""
        record = await self._by_token_id(token_id)
        if record is not None:
            record.revoked_at = datetime.now(UTC)
            async with self._session_factory.begin() as session:
                add_result = session.add(record)
                if inspect.isawaitable(add_result):
                    await add_result

    async def increment_hydration(self, agent_id: str) -> None:
        """Increment the hydration_count for *agent_id*'s token record."""
        from general_ludd.db.models import AgentTokenModel as A

        async with self._session_factory.begin() as session:
            stmt = select(A).where(A.agent_id == agent_id)
            result = await session.execute(stmt)
            record: AgentTokenModel | None = result.scalar_one_or_none()
            if record is not None:
                record.hydration_count += 1
                add_result = session.add(record)
                if inspect.isawaitable(add_result):
                    await add_result

    async def _by_token_id(self, token_id: str) -> AgentTokenModel | None:
        from general_ludd.db.models import AgentTokenModel as A

        async with self._session_factory() as session:
            stmt = select(A).where(A.token_id == token_id)
            result = await session.execute(stmt)
            row: AgentTokenModel | None = result.scalar_one_or_none()
            return row

    async def list_expired(self, now: datetime) -> list[AgentTokenModel]:
        """Return all live (non-revoked) tokens whose ``expires_at < now``.

        Used by :class:`~general_ludd.sts.reaper.TokenReaper.reap_expired`
        to sweep TTL-elapsed tokens. A token counts as expired iff:
          - ``expires_at IS NOT NULL``
          - ``expires_at < now``
          - ``revoked_at IS NULL`` (already-revoked tokens are not reaped)
        """
        from general_ludd.db.models import AgentTokenModel as A

        async with self._session_factory() as session:
            stmt = (
                select(A)
                .where(A.expires_at.is_not(None))
                .where(A.expires_at < now)
                .where(A.revoked_at.is_(None))
            )
            result = await session.execute(stmt)
            rows: list[AgentTokenModel] = list(result.scalars().all())
            return rows

    async def list_all(self) -> list[AgentTokenModel]:
        """Return every token row (live + revoked + expired).

        Used by :class:`~general_ludd.sts.visualizer.TokenTreeRenderer.render_active_tokens`
        to build a complete forest view. The caller filters by ``revoked_at``
        and ``expires_at`` as needed.
        """
        from general_ludd.db.models import AgentTokenModel as A

        async with self._session_factory() as session:
            stmt = select(A)
            result = await session.execute(stmt)
            rows: list[AgentTokenModel] = list(result.scalars().all())
            return rows

    async def list_children(self, parent_agent_id: str) -> list[AgentTokenModel]:
        """Return all tokens whose ``parent_agent_id`` matches *parent_agent_id*.

        Includes both live and already-revoked rows; the caller
        (:class:`~general_ludd.sts.reaper.TokenReaper.cascade_revoke`)
        filters by ``revoked_at`` and recurses into live children only.
        """
        from general_ludd.db.models import AgentTokenModel as A

        async with self._session_factory() as session:
            stmt = select(A).where(A.parent_agent_id == parent_agent_id)
            result = await session.execute(stmt)
            rows: list[AgentTokenModel] = list(result.scalars().all())
            return rows
