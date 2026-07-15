"""StsAuditPipeline — structured audit event logger for STS token lifecycle.

Phase P4: records mint, use, renew, revoke, revive events to StsAuditModel rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class StsAuditPipeline:
    """Persistent audit event logger for STS token operations.

    Each token_id maps to one StsAuditModel row. Events are appended as
    JSON entries to the ``events`` column and use_count is incremented
    atomically.
    """

    def __init__(self, session_factory: async_sessionmaker[Any]) -> None:
        self._session_factory = session_factory
        self._pending_events: list[dict[str, Any]] = []

    def wire_to_daemon(self, daemon_state: dict[str, object]) -> None:
        daemon_state["_sts_audit_pipeline"] = self

    async def flush_on_tick(self) -> int:
        if not self._pending_events:
            return 0
        count = len(self._pending_events)
        from general_ludd.db.models import StsAuditModel

        async with self._session_factory() as session:
            for event in self._pending_events:
                token_id = str(event.get("token_id", ""))
                result = await session.execute(
                    select(StsAuditModel).where(StsAuditModel.token_id == token_id)
                )
                row_raw = result.scalar_one_or_none()
                row: StsAuditModel | None = (
                    await row_raw if asyncio.iscoroutine(row_raw) else row_raw
                )
                if row is not None:
                    row.use_count = (row.use_count or 0) + 1
                    row.last_used_at = time.time()
                    try:
                        events_list = _json.loads(row.events)
                    except Exception:
                        events_list = []
                    events_list.append(event)
                    row.events = _json.dumps(events_list)
                    add_result = session.add(row)
                    if asyncio.iscoroutine(add_result):
                        await add_result
            await session.commit()
        self._pending_events = []
        return count

    async def record_mint(
        self,
        token_id: str,
        issuer_agent_id: str,
        subject_agent_id: str,
        scope_actions: list[str] | None = None,
    ) -> None:
        scope_hash = self._scope_hash(scope_actions)
        event = self._event_dict(
            action="mint",
            agent_id=subject_agent_id,
            parent_agent_id=issuer_agent_id,
            scope_hash=scope_hash,
        )
        await self._append_event(token_id, event)

    async def record_use(
        self,
        token_id: str,
        agent_id: str,
        parent_agent_id: str,
    ) -> None:
        event = self._event_dict(
            action="use",
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        await self._append_event(token_id, event)

    async def record_renew(
        self,
        token_id: str,
        agent_id: str,
        parent_agent_id: str,
    ) -> None:
        event = self._event_dict(
            action="renew",
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        await self._append_event(token_id, event)

    async def record_revoke(
        self,
        token_id: str,
        agent_id: str,
        parent_agent_id: str,
    ) -> None:
        event = self._event_dict(
            action="revoke",
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        await self._append_event(token_id, event)

    async def record_revive(
        self,
        token_id: str,
        agent_id: str,
        parent_agent_id: str,
    ) -> None:
        event = self._event_dict(
            action="revive",
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        await self._append_event(token_id, event)

    def _scope_hash(self, actions: list[str] | None) -> str:
        if actions is None:
            return ""
        canonical = _json.dumps(sorted(actions), sort_keys=True)
        return hashlib.md5(canonical.encode(), usedforsecurity=False).hexdigest()[:16]

    def _event_dict(
        self,
        action: str,
        agent_id: str,
        parent_agent_id: str = "",
        scope_hash: str = "",
    ) -> dict[str, object]:
        return {
            "action": action,
            "agent_id": agent_id,
            "parent_agent_id": parent_agent_id,
            "scope_hash": scope_hash,
            "timestamp": time.time(),
        }

    async def _append_event(self, token_id: str, event: dict[str, object]) -> None:
        from general_ludd.db.models import StsAuditModel

        async with self._session_factory.begin() as session:
            result = await session.execute(
                select(StsAuditModel).where(StsAuditModel.token_id == token_id)
            )
            row: StsAuditModel | None = result.scalar_one_or_none()
            if row is None:
                return
            row.use_count = (row.use_count or 0) + 1
            row.last_used_at = time.time()
            try:
                events_list = _json.loads(row.events)
            except Exception:
                events_list = []
            events_list.append(event)
            row.events = _json.dumps(events_list)
            session.add(row)
