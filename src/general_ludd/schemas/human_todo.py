"""Validated HTTP payloads and content-safe HumanTodo serialization."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

from general_ludd.db.models import HumanTodoModel


def human_todo_to_dict(row: HumanTodoModel) -> dict[str, object]:
    """Serialize one database row while treating malformed legacy tags as empty."""
    try:
        tags: list[str] = json.loads(row.tags or "[]")
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
    """Payload for filing one human-owned action request."""

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
    """Payload for advancing a human-owned action request."""

    status: str | None = None
    human_resolution: str | None = None
    human_resolver: str | None = None


class AddTagRequest(BaseModel):
    """Payload for attaching one bounded classification tag."""

    tag: str = Field(min_length=1, max_length=128)


__all__ = (
    "AddTagRequest",
    "CreateHumanTodoRequest",
    "PatchHumanTodoRequest",
    "human_todo_to_dict",
)
