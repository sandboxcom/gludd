"""Pause/resume API endpoints for projects and models.

Endpoints:
    GET  /api/pause              — list all paused entities
    POST /api/pause/project      — pause a project
    POST /api/pause/model        — pause a model
    POST /api/resume/project     — resume a project
    POST /api/resume/model       — resume a model

PSK authentication is applied globally by the daemon middleware.
The shared PauseController instance is stored on
``app.state._pause_controller``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class PauseRequest(BaseModel):
    target_id: str = Field(..., min_length=1, description="Project or model identifier")
    reason: str = Field("", description="Reason for pausing")
    resources: dict[str, object] | None = Field(
        None, description="Snapshot of daemon state (spend, leases, registries) at pause time"
    )
    last_state: dict[str, object] | None = Field(
        None, description="Last working state before pause (phase, cursor, etc.)"
    )


class ResumeRequest(BaseModel):
    target_id: str = Field(..., min_length=1, description="Project or model identifier")


def _get_controller(app: FastAPI) -> Any:
    return getattr(app.state, "_pause_controller", None)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get("/api/pause")
    async def api_pause_list() -> dict[str, Any]:
        """List all currently paused entities (projects and models)."""
        controller = _get_controller(app)
        if controller is None:
            return {"paused": [], "count": 0}
        records = controller.list_paused()
        return {
            "paused": [
                {
                    "kind": r.kind,
                    "target_id": r.target_id,
                    "paused_at": r.paused_at,
                    "reason": r.reason,
                }
                for r in records
            ],
            "count": len(records),
        }

    @app.post("/api/pause/project")
    async def api_pause_project(req: PauseRequest) -> dict[str, Any]:
        """Pause a project. Idempotent — re-pausing returns the existing record."""
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "paused": False}
        record = controller.pause(
            "project",
            req.target_id,
            reason=req.reason,
            resources=req.resources or None,
            last_state=req.last_state or None,
        )
        return {
            "paused": True,
            "kind": "project",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
            "reason": record.reason,
            "resources": record.resources,
            "last_state": record.last_state,
        }

    @app.post("/api/pause/model")
    async def api_pause_model(req: PauseRequest) -> dict[str, Any]:
        """Pause a model profile. Idempotent."""
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "paused": False}
        record = controller.pause(
            "model",
            req.target_id,
            reason=req.reason,
            resources=req.resources or None,
            last_state=req.last_state or None,
        )
        return {
            "paused": True,
            "kind": "model",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
            "reason": record.reason,
            "resources": record.resources,
            "last_state": record.last_state,
        }

    @app.post("/api/resume/project")
    async def api_resume_project(req: ResumeRequest) -> dict[str, Any]:
        """Resume a paused project. Returns null when not paused (idempotent)."""
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "resumed": False}
        record = controller.resume("project", req.target_id)
        if record is None:
            return {"resumed": False, "target_id": req.target_id, "message": "was not paused"}
        return {
            "resumed": True,
            "kind": "project",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
        }

    @app.post("/api/resume/model")
    async def api_resume_model(req: ResumeRequest) -> dict[str, Any]:
        """Resume a paused model profile. Returns null when not paused (idempotent)."""
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "resumed": False}
        record = controller.resume("model", req.target_id)
        if record is None:
            return {"resumed": False, "target_id": req.target_id, "message": "was not paused"}
        return {
            "resumed": True,
            "kind": "model",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
        }
