"""HTTP API for G1 persistent agent memory.

Endpoints:
  POST   /api/memory                     — create/update a memory record
  GET    /api/memory/{agent_id}          — list records for an agent
  DELETE /api/memory/{agent_id}/{key}    — delete a memory record
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from general_ludd.db.models import MemoryRecordModel
from general_ludd.db.repository import MemoryRepository

logger = logging.getLogger(__name__)


class MemoryCreateRequest(BaseModel):
    """Validated body for creating or updating a memory record."""

    agent_id: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=256)
    value: str = Field(default="")
    namespace: str = Field(default="default", max_length=128)
    project_id: str | None = Field(default=None, max_length=256)
    ttl_seconds: int | None = Field(default=None, ge=0)


class MemoryRecordResponse(BaseModel):
    """API representation of a persisted memory record."""

    id: str
    agent_id: str
    key: str
    value: str
    namespace: str
    project_id: str | None = None
    ttl_seconds: int | None
    created_at: str
    updated_at: str

    @staticmethod
    def from_model(row: MemoryRecordModel) -> MemoryRecordResponse:
        """Build the response from a persisted memory record model."""
        return MemoryRecordResponse(
            id=row.id,
            agent_id=row.agent_id,
            key=row.key,
            value=row.value,
            namespace=row.namespace,
            project_id=row.project_id,
            ttl_seconds=row.ttl_seconds,
            created_at=row.created_at.isoformat() if row.created_at else "",
            updated_at=row.updated_at.isoformat() if row.updated_at else "",
        )


def _get_memory_repo(app: FastAPI) -> MemoryRepository:
    repo = getattr(app.state, "_memory_repo", None)
    if not isinstance(repo, MemoryRepository):
        raise HTTPException(status_code=503, detail="Memory repository not available")
    return repo


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register memory CRUD endpoints on the FastAPI app."""

    @app.post("/api/memory", status_code=201)
    async def api_memory_create(req: MemoryCreateRequest) -> MemoryRecordResponse:
        repo = _get_memory_repo(app)
        row = await repo.set(
            agent_id=req.agent_id,
            key=req.key,
            value=req.value if "value" in req.model_fields_set else None,
            namespace=req.namespace,
            project_id=req.project_id,
            ttl_seconds=req.ttl_seconds,
        )
        return MemoryRecordResponse.from_model(row)

    @app.get("/api/memory/{agent_id}")
    async def api_memory_list(
        agent_id: str,
        namespace: str = Query(default="default", max_length=128),
        project_id: str | None = Query(default=None, max_length=256),
    ) -> list[MemoryRecordResponse]:
        repo = _get_memory_repo(app)
        rows = await repo.list_by_namespace(agent_id, namespace=namespace, project_id=project_id)
        return [MemoryRecordResponse.from_model(r) for r in rows]

    @app.delete("/api/memory/{agent_id}/{key}", status_code=204)
    async def api_memory_delete(
        agent_id: str,
        key: str,
        namespace: str = Query(default="default", max_length=128),
        project_id: str | None = Query(default=None, max_length=256),
    ) -> None:
        repo = _get_memory_repo(app)
        deleted = await repo.delete(agent_id, key, namespace=namespace, project_id=project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory record not found")
        return None
