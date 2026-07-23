"""Message-queue API: send / inbox / ack for inter-agent coordination.

Endpoints (PSK auth applied by the daemon middleware exactly like other
/api/* routes — these paths are NOT in the daemon's _PUBLIC_PATHS set):

  POST /api/messages                       -> send a message
  GET  /api/messages?recipient=X&unread=.. -> inbox (includes broadcast)
  POST /api/messages/{id}/ack              -> mark a message read

Persistence is via AgentMessageRepository against the daemon's SQLite DB.
Without a session factory (degraded boot) the endpoints fall back to an
in-memory store kept on the daemon_state dict so the API stays usable.
"""

from __future__ import annotations

import collections
import logging
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import AgentMessageModel
from general_ludd.db.repository import AgentMessageRepository

logger = logging.getLogger(__name__)


class SendMessageRequest(BaseModel):
    sender: str = Field(min_length=1, max_length=128)
    recipient: str = Field(min_length=1, max_length=128)
    topic: str = Field(default="", max_length=256)
    body: str = Field(default="", max_length=65536)
    priority: str = Field(default="normal", pattern=r"^(low|normal|high|urgent)$")
    ttl_seconds: int | None = Field(default=None, ge=1)
    project_id: str | None = None


def _get_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession] | None:
    return getattr(app.state, "_session_factory", None)


def _msg_to_dict(msg: AgentMessageModel) -> dict[str, object]:
    return {
        "id": msg.id,
        "sender": msg.sender,
        "recipient": msg.recipient,
        "topic": msg.topic,
        "body": msg.body,
        "priority": msg.priority,
        "project_id": msg.project_id,
        "created_at": str(msg.created_at) if msg.created_at else None,
        "read_at": str(msg.read_at) if msg.read_at else None,
        "ttl_seconds": msg.ttl_seconds,
    }


# Memory-leak guard (follow-up to P3 470253a): the degraded-mode in-memory
# message fallback (`_daemon_state["messages"]`) is unbounded — without a session
# factory every POST /api/messages appends forever. Bound it to the most-recent N
# via a deque(maxlen). All consumers iterate the collection (send appends, inbox
# and ack iterate), never index/slice the raw object or JSON-serialize it
# directly, so a deque is a drop-in: FIFO eviction drops the oldest messages once
# the cap is hit.
_MAX_INMEMORY_MESSAGES = 5000


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    # Bound the in-memory fallback. Preserve any pre-seeded entries; idempotent if
    # already a deque with the right cap.
    _existing = _daemon_state.get("messages")
    if not (
        isinstance(_existing, collections.deque)
        and _existing.maxlen == _MAX_INMEMORY_MESSAGES
    ):
        items: list[dict[str, object]] = (
            list(_existing)
            if isinstance(_existing, (list, collections.deque))
            else []
        )
        _daemon_state["messages"] = collections.deque(
            items, maxlen=_MAX_INMEMORY_MESSAGES
        )

    messages = cast(collections.deque[dict[str, object]], _daemon_state["messages"])

    @app.api_route("/api/messages", methods=["GET", "POST"])
    async def api_messages(
        request: Request,
        recipient: str | None = None,
        unread: bool = True,
        include_broadcast: bool = True,
        project_id: str | None = None,
    ) -> object:
        if request.method == "POST":
            payload = await request.json()
            req = SendMessageRequest.model_validate(payload)
            data = req.model_dump()
            factory = _get_session_factory(app)
            if factory is not None:
                async with factory() as session:
                    repo = AgentMessageRepository(session)
                    row = await repo.send(data)
                    await session.commit()
                    return JSONResponse(status_code=201, content=_msg_to_dict(row))
            import uuid
            from datetime import UTC, datetime

            mem = dict(data)
            mem["id"] = f"MSG-{uuid.uuid4().hex[:12].upper()}"
            mem["created_at"] = datetime.now(UTC)
            mem["read_at"] = None
            messages.append(mem)
            return JSONResponse(
                status_code=201,
                content={**mem, "created_at": str(mem["created_at"]), "read_at": None},
            )

        if recipient is None:
            raise HTTPException(status_code=422, detail="recipient is required")
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = AgentMessageRepository(session)
                msgs = await repo.inbox(
                    recipient,
                    unread_only=unread,
                    include_broadcast=include_broadcast,
                    project_id=project_id,
                )
                results = [_msg_to_dict(m) for m in msgs]
                return {"messages": results, "count": len(results), "recipient": recipient}
        results = []
        for m in messages:
            target = m.get("recipient")
            if target == recipient or (include_broadcast and target == "broadcast"):
                if unread and m.get("read_at") is not None:
                    continue
                if project_id is not None and m.get("project_id") != project_id:
                    continue
                results.append({**m, "created_at": str(m.get("created_at"))})
        return {"messages": results, "count": len(results), "recipient": recipient}

    @app.post("/api/messages/{message_id}/ack")
    async def api_ack_message(
            message_id: str, project_id: str | None = None
    ) -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = AgentMessageRepository(session)
                # XT-11: forward project_id so a cross-tenant ack is refused
                # (returns None -> 404, indistinguishable from not-found).
                row = await repo.ack(message_id, project_id=project_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="message not found")
                await session.commit()
                return {"acked": True, "id": row.id, "read_at": str(row.read_at)}
        for m in messages:
            if m.get("id") == message_id:
                # XT-11: the degraded in-memory fallback must also refuse a
                # cross-tenant ack, matching the DB path.
                if project_id is not None and m.get("project_id") != project_id:
                    continue
                from datetime import UTC, datetime

                m["read_at"] = datetime.now(UTC)
                return {"acked": True, "id": message_id, "read_at": str(m["read_at"])}
        raise HTTPException(status_code=404, detail="message not found")
