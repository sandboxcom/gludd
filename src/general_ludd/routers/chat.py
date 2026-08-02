"""Chat contracts daemon router: session listing, search, and message validation.

Endpoints (PSK auth applied by daemon middleware — not in _PUBLIC_PATHS):
  GET  /api/chat/sessions                 -> list persisted chat sessions
  GET  /api/chat/sessions/{file_path:path} -> session detail + messages
  POST /api/chat/sessions/search           -> search sessions by query
  POST /api/chat/validate                  -> validate a message against ChatMessage contract
"""

from __future__ import annotations

import urllib.parse
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from general_ludd.chat.contracts import ChatConfig, ChatMessage
from general_ludd.chat.history import ChatHistory


class _SessionSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=20, ge=1, le=100)


class _ValidateRequest(BaseModel):
    role: str
    content: str
    timestamp: str | None = None
    model: str | None = None


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.get("/api/chat/sessions")
    async def list_sessions(
        limit: int = Query(default=20, ge=1, le=100),
        model: str | None = Query(default=None),
    ) -> dict[str, object]:
        history = ChatHistory()
        sessions = history.list_sessions(limit=limit, model_filter=model)
        return {
            "sessions": sessions,
            "total": len(sessions),
            "limit": limit,
        }

    @app.get("/api/chat/sessions/{file_path:path}")
    async def get_session(file_path: str) -> dict[str, object]:
        decoded = urllib.parse.unquote(file_path)
        history = ChatHistory()
        session = history.get_session(decoded)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {decoded!r} not found")
        messages = history.get_messages(decoded)
        return {
            "session": session,
            "messages": messages,
            "message_count": len(messages),
        }

    @app.post("/api/chat/sessions/search")
    async def search_sessions(body: _SessionSearchRequest) -> dict[str, object]:
        history = ChatHistory()
        results = history.search(body.query, limit=body.limit)
        return {
            "results": results,
            "total": len(results),
            "query": body.query,
        }

    @app.get("/api/chat/stats")
    async def chat_stats() -> dict[str, object]:
        history = ChatHistory()
        return history.stats()

    @app.post("/api/chat/validate")
    async def validate_message(body: _ValidateRequest) -> dict[str, object]:
        valid_roles = {"system", "user", "assistant", "tool"}
        if body.role not in valid_roles:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid role {body.role!r}. Must be one of: {sorted(valid_roles)}",
            )
        try:
            msg = ChatMessage(
                role=cast("Literal['system', 'user', 'assistant', 'tool']", body.role),
                content=body.content,
                timestamp=body.timestamp,
                model=body.model,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "valid": True,
            "as_api": msg.as_api_message(),
            "as_persistent": msg.as_persistent_record(),
        }

    @app.post("/api/chat/config/validate")
    async def validate_config(body: dict[str, object]) -> dict[str, object]:
        try:
            cfg = ChatConfig(
                model=str(body.get("model", "default")),
                system_prompt=cast("str | None", body.get("system_prompt")),
                eval_mode=bool(body.get("eval_mode")),
                api_base_url=cast("str | None", body.get("api_base_url")),
                api_key=cast("str | None", body.get("api_key")),
                project_dir=cast("str | None", body.get("project_dir")),
                history_file=cast("str | None", body.get("history_file")),
                save_interval=int(str(body.get("save_interval", 5))),
                resume=bool(body.get("resume")),
                max_context=int(str(body["max_context"])) if body.get("max_context") is not None else None,
                stream=bool(body.get("stream", True)),
                export_format=cast("str | None", body.get("export_format")),
                export_output=cast("str | None", body.get("export_output")),
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "valid": True,
            "session_kwargs": cfg.to_session_kwargs(),
        }
