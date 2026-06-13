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

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def _msg_to_dict(msg: Any) -> dict[str, Any]:
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


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    _daemon_state.setdefault("messages", [])

    @app.post("/api/messages", status_code=201)
    async def api_send_message(req: SendMessageRequest) -> dict[str, Any]:
        data = req.model_dump()
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = AgentMessageRepository(session)
                row = await repo.send(data)
                await session.commit()
                return _msg_to_dict(row)
        # Degraded fallback: in-memory.
        import uuid
        from datetime import UTC, datetime

        mem = dict(data)
        mem["id"] = f"MSG-{uuid.uuid4().hex[:12].upper()}"
        mem["created_at"] = datetime.now(UTC)
        mem["read_at"] = None
        _daemon_state["messages"].append(mem)
        return {**mem, "created_at": str(mem["created_at"]), "read_at": None}

    @app.get("/api/messages")
    async def api_inbox(
        recipient: str,
        unread: bool = True,
        include_broadcast: bool = True,
        project_id: str | None = None,
    ) -> dict[str, Any]:
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
        # Degraded fallback: in-memory.
        results = []
        for m in _daemon_state["messages"]:
            target = m.get("recipient")
            if target == recipient or (include_broadcast and target == "broadcast"):
                if unread and m.get("read_at") is not None:
                    continue
                results.append({**m, "created_at": str(m.get("created_at"))})
        return {"messages": results, "count": len(results), "recipient": recipient}

    @app.post("/api/messages/{message_id}/ack")
    async def api_ack_message(message_id: str) -> dict[str, Any]:
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = AgentMessageRepository(session)
                row = await repo.ack(message_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="message not found")
                await session.commit()
                return {"acked": True, "id": row.id, "read_at": str(row.read_at)}
        for m in _daemon_state["messages"]:
            if m.get("id") == message_id:
                from datetime import UTC, datetime

                m["read_at"] = datetime.now(UTC)
                return {"acked": True, "id": message_id, "read_at": str(m["read_at"])}
        raise HTTPException(status_code=404, detail="message not found")
