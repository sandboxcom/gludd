"""HTTP router for bot→human requests (HumanTodo).

Separate from the agent-todo router (``routers/todos.py``). An agent files a
human-todo via ``POST /api/human-todos`` when it cannot complete its work
without human action; the human resolves it via ``PATCH``. When
``parent_agent_todo_id`` is set, the parent agent todo is transitioned to
``blocked_on_human`` on file and back to ``queued``/``cancelled`` on
resolution, with ``human_resolution`` injected into the next dispatch as
``human_input``.

Auth follows the daemon pattern: GET endpoints are public (a human needs to
see the queue without the admin PSK); write endpoints (POST/PATCH/DELETE) are
PSK-gated.
"""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from general_ludd.db.repository import (
    HUMAN_TODO_CATEGORIES,
    HUMAN_TODO_PRIORITIES,
    HUMAN_TODO_TERMINAL,
    HumanTodoRepository,
    InvalidTransitionError,
    TodoRepository,
)
from general_ludd.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def _human_todo_to_dict(row: Any) -> dict[str, Any]:
    try:
        tags: list[str] = _json.loads(row.tags or "[]")
    except Exception:
        tags = []
    return {
        "id": row.id,
        "parent_agent_todo_id": row.parent_agent_todo_id,
        "agent_id": row.agent_id,
        "session_id": row.session_id,
        "title": row.title,
        "body": row.body,
        "category": row.category,
        "priority": row.priority,
        "status": row.status,
        "human_resolution": row.human_resolution,
        "human_resolver": row.human_resolver,
        "created_at": str(row.created_at) if row.created_at else None,
        "updated_at": str(row.updated_at) if row.updated_at else None,
        "resolved_at": str(row.resolved_at) if row.resolved_at else None,
        "due_at": str(row.due_at) if getattr(row, "due_at", None) else None,
        "tags": tags,
    }


class CreateHumanTodoRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1)
    category: str
    priority: str = Field(default="medium")
    parent_agent_todo_id: str | None = None
    session_id: str | None = None
    due_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)


class PatchHumanTodoRequest(BaseModel):
    status: str | None = None
    human_resolution: str | None = None
    human_resolver: str | None = None


class AddTagRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=128)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.post("/api/human-todos", status_code=201)
    async def api_create_human_todo(req: CreateHumanTodoRequest) -> dict[str, Any]:
        if req.category not in HUMAN_TODO_CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail=f"invalid category; must be one of {sorted(HUMAN_TODO_CATEGORIES)}",
            )
        if req.priority not in HUMAN_TODO_PRIORITIES:
            raise HTTPException(
                status_code=422,
                detail=f"invalid priority; must be one of {sorted(HUMAN_TODO_PRIORITIES)}",
            )
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = HumanTodoRepository(session)
            try:
                row = await repo.create(
                    agent_id=req.agent_id,
                    title=req.title,
                    body=req.body,
                    category=req.category,
                    priority=req.priority,
                    parent_agent_todo_id=req.parent_agent_todo_id,
                    session_id=req.session_id,
                    due_at=req.due_at,
                    tags=req.tags,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            # Blocking integration: if a parent agent todo is named, transition
            # it to BLOCKED_ON_HUMAN so the event-loop claimer skips it.
            if req.parent_agent_todo_id is not None:
                todo_repo = TodoRepository(session)
                parent = await todo_repo.get_by_id(req.parent_agent_todo_id)
                if parent is not None:
                    try:
                        await todo_repo.transition(
                            req.parent_agent_todo_id,
                            TodoStatus.BLOCKED_ON_HUMAN,
                            expected_version=parent.version,
                        )
                    except (InvalidTransitionError, Exception) as exc:
                        # Non-fatal: the human-todo is still filed; the parent
                        # just isn't blocked (e.g. already terminal). Log and
                        # continue so the agent's request is never lost.
                        logger.warning(
                            "could not block parent todo %s: %s",
                            req.parent_agent_todo_id,
                            exc,
                        )
            await session.commit()
            return _human_todo_to_dict(row)

    @app.get("/api/human-todos")
    async def api_list_human_todos(
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        factory = _get_session_factory(app)
        if factory is None:
            return []
        _limit = max(1, min(limit, 500))
        _offset = max(0, offset)
        async with factory() as session:
            repo = HumanTodoRepository(session)
            rows = await repo.list_all(
                limit=_limit,
                offset=_offset,
                status=status,
                category=category,
                priority=priority,
                agent_id=agent_id,
            )
            return [_human_todo_to_dict(r) for r in rows]

    @app.get("/api/human-todos/feed")
    async def api_human_todos_feed(since: datetime | None = None) -> list[dict[str, Any]]:
        factory = _get_session_factory(app)
        if factory is None:
            return []
        from datetime import UTC, timedelta
        from datetime import datetime as _dt

        boundary = since if since is not None else _dt.now(UTC) - timedelta(hours=24)
        async with factory() as session:
            repo = HumanTodoRepository(session)
            rows = await repo.list_changed_since(boundary)
            return [_human_todo_to_dict(r) for r in rows]

    @app.get("/api/human-todos/{human_todo_id}")
    async def api_get_human_todo(human_todo_id: str) -> dict[str, Any]:
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = HumanTodoRepository(session)
            row = await repo.get(human_todo_id)
            if row is None:
                raise HTTPException(status_code=404, detail="human-todo not found")
            return _human_todo_to_dict(row)

    @app.patch("/api/human-todos/{human_todo_id}")
    async def api_patch_human_todo(
        human_todo_id: str, req: PatchHumanTodoRequest
    ) -> dict[str, Any]:
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = HumanTodoRepository(session)
            row = await repo.get(human_todo_id)
            if row is None:
                raise HTTPException(status_code=404, detail="human-todo not found")
            if row.status in HUMAN_TODO_TERMINAL:
                raise HTTPException(
                    status_code=422,
                    detail=f"human-todo is in terminal state {row.status!r}",
                )
            target = req.status or row.status
            if target not in {"done", "dismissed", "in_progress", "open", "superseded"}:
                raise HTTPException(status_code=422, detail=f"invalid status {target!r}")
            try:
                if target == "done":
                    if not req.human_resolver or not req.human_resolution:
                        raise HTTPException(
                            status_code=422,
                            detail="done requires human_resolver and human_resolution",
                        )
                    row = await repo.mark_done(
                        human_todo_id, req.human_resolver, req.human_resolution
                    )
                elif target == "dismissed":
                    if not req.human_resolver or not req.human_resolution:
                        raise HTTPException(
                            status_code=422,
                            detail="dismissed requires human_resolver and human_resolution (reason)",
                        )
                    row = await repo.dismiss(
                        human_todo_id, req.human_resolver, req.human_resolution
                    )
                elif target == "in_progress":
                    row = await repo.mark_in_progress(human_todo_id)
                else:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "PATCH may only move to done/dismissed/in_progress; "
                            f"got {target!r}"
                        ),
                    )
            except InvalidTransitionError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            # Unblocking integration: when this human-todo had a parent agent
            # todo and just resolved, transition the parent back. ``done`` →
            # QUEUED (resume; the resolution text is delivered as human_input
            # on the next dispatch via the parent's tags). ``dismissed`` →
            # CANCELLED (the agent should try a different approach).
            if (
                row.parent_agent_todo_id is not None
                and row.status in {"done", "dismissed"}
            ):
                todo_repo = TodoRepository(session)
                parent = await todo_repo.get_by_id(row.parent_agent_todo_id)
                if parent is not None:
                    target_todo_status = (
                        TodoStatus.QUEUED if row.status == "done" else TodoStatus.CANCELLED
                    )
                    try:
                        await todo_repo.transition(
                            row.parent_agent_todo_id,
                            target_todo_status,
                            expected_version=parent.version,
                        )
                    except (InvalidTransitionError, Exception) as exc:
                        logger.warning(
                            "could not unblock parent todo %s: %s",
                            row.parent_agent_todo_id,
                            exc,
                        )
            await session.commit()
            return _human_todo_to_dict(row)

    @app.delete("/api/human-todos/{human_todo_id}")
    async def api_delete_human_todo(human_todo_id: str) -> dict[str, Any]:
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = HumanTodoRepository(session)
            row = await repo.get(human_todo_id)
            if row is None:
                raise HTTPException(status_code=404, detail="human-todo not found")
            # Soft-delete for audit: move to dismissed if still open.
            if row.status not in HUMAN_TODO_TERMINAL:
                await repo.dismiss(human_todo_id, "admin", "soft-deleted by admin")
            await session.commit()
            return {"id": human_todo_id, "status": "deleted", "final_status": row.status}

    @app.post("/api/human-todos/{human_todo_id}/tags")
    async def api_add_tag(human_todo_id: str, req: AddTagRequest) -> dict[str, Any]:
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = HumanTodoRepository(session)
            try:
                row = await repo.add_tag(human_todo_id, req.tag)
            except InvalidTransitionError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            await session.commit()
            return _human_todo_to_dict(row)
