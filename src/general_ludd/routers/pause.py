"""Pause/resume API endpoints for tasks, agents, infrastructure, projects, and models.

Endpoints:
    GET  /api/pause              — list all paused entities
    GET  /api/pause/status       — list all paused entities (alias)
    POST /api/pause/project      — pause a project
    POST /api/pause/model        — pause a model
    POST /api/tasks/{task_id}/pause    — pause a task
    POST /api/tasks/{task_id}/resume   — resume a task
    POST /api/agents/{agent_id}/pause  — pause an agent
    POST /api/agents/{agent_id}/resume — resume an agent
    POST /api/infra/{deployment_id}/pause  — pause deployed infra
    POST /api/infra/{deployment_id}/resume — resume deployed infra
    POST /api/resume/project     — resume a project
    POST /api/resume/model       — resume a model

PSK authentication is applied globally by the daemon middleware.
The shared PauseController instance is stored on
``app.state._pause_controller``.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import FastAPI
from pydantic import BaseModel, Field

from general_ludd.controllers.pause_controller import PauseController, PauseKind

logger = logging.getLogger(__name__)


class PauseEntityRequest(BaseModel):
    """Request body for pausing a task, agent, or infrastructure entity."""

    reason: str = Field("", description="Reason for pausing")


class ResumeEntityRequest(BaseModel):
    """Explicit empty request body for entity resume operations."""

    pass


class PauseRequest(BaseModel):
    """Request body for pausing a project or model with optional state."""

    target_id: str = Field(..., min_length=1, description="Project or model identifier")
    reason: str = Field("", description="Reason for pausing")
    resources: dict[str, object] | None = Field(
        None, description="Snapshot of daemon state (spend, leases, registries) at pause time"
    )
    last_state: dict[str, object] | None = Field(
        None, description="Last working state before pause (phase, cursor, etc.)"
    )


class ResumeRequest(BaseModel):
    """Request body identifying a project or model to resume."""

    target_id: str = Field(..., min_length=1, description="Project or model identifier")


def _format_pause_record(record: Any) -> dict[str, object]:
    return {
        "kind": record.kind,
        "target_id": record.target_id,
        "paused_at": record.paused_at,
        "reason": record.reason,
    }


def _get_controller(app: FastAPI) -> PauseController | None:
    return getattr(app.state, "_pause_controller", None)


async def _quiesce_entity_for_pause(
    app: FastAPI,
    controller: PauseController,
    kind: PauseKind,
    target_id: str,
) -> tuple[list[object], str, list[str]]:
    """Capture one entity's resumable state when both collaborators exist."""
    dispatcher = getattr(app.state, "_agent_dispatcher", None)
    hibernation = getattr(app.state, "_hibernation_controller", None)
    if dispatcher is None or hibernation is None:
        return [], "none", []
    result = await controller.quiesce_entity(
        kind,
        target_id,
        dispatcher=dispatcher,
        hibernation=hibernation,
    )
    if isinstance(result, tuple):
        handles, status, errors = result
        return list(handles), status, list(errors)
    return result[:], "clean", []


async def _rehydrate_entity_after_resume(
    app: FastAPI,
    controller: PauseController,
    kind: PauseKind,
    target_id: str,
    *,
    has_handles: bool,
) -> tuple[int, str, list[str]]:
    """Restore saved entity state through the canonical dispatcher path."""
    if not has_handles:
        return 0, "none", []
    dispatcher = getattr(app.state, "_agent_dispatcher", None)
    hibernation = getattr(app.state, "_hibernation_controller", None)
    if dispatcher is None or hibernation is None:
        return 0, "none", []
    snapshots, status, errors = await controller.resume_rehydrate(
        kind,
        target_id,
        dispatcher=dispatcher,
        hibernation=hibernation,
    )
    return len(snapshots), status, list(errors)


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register pause and resume routes on *app*."""

    @app.get("/api/pause")
    async def api_pause_list() -> dict[str, object]:
        """List all currently paused entities (projects and models)."""
        controller = _get_controller(app)
        if controller is None:
            return {"paused": [], "count": 0}
        records = controller.list_paused()
        return {
            "paused": [_format_pause_record(r) for r in records],
            "count": len(records),
        }

    @app.get("/api/pause/status")
    async def api_pause_status() -> dict[str, object]:
        """List all currently paused entities across all types."""
        controller = _get_controller(app)
        if controller is None:
            return {"paused": [], "count": 0, "by_type": {}}
        records = controller.list_paused()
        by_type: dict[str, list[dict[str, object]]] = {}
        for r in records:
            by_type.setdefault(r.kind, []).append(_format_pause_record(r))
        return {
            "paused": [_format_pause_record(r) for r in records],
            "count": len(records),
            "by_type": by_type,
        }

    # --- Task pause/resume ---

    @app.post("/api/tasks/{task_id}/pause")
    async def api_pause_task(task_id: str, req: PauseEntityRequest) -> dict[str, object]:
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "paused": False}
        handles, quiesce_status, quiesce_errors = await _quiesce_entity_for_pause(
            app, controller, "task", task_id
        )
        if quiesce_status == "none":
            record = controller.pause("task", task_id, reason=req.reason)
        else:
            record = controller.pause(
                "task",
                task_id,
                reason=req.reason,
                agent_handles=handles,
                quiesce_status=quiesce_status,
                quiesce_errors=quiesce_errors,
            )
        return {
            "paused": True,
            "kind": "task",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
            "reason": record.reason,
            "quiesce_status": quiesce_status,
            "quiesce_errors": quiesce_errors,
        }

    @app.post("/api/tasks/{task_id}/resume")
    async def api_resume_task(task_id: str, _req: ResumeEntityRequest | None = None) -> dict[str, object]:
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "resumed": False}
        record = controller.resume("task", task_id)
        if record is None:
            return {"resumed": False, "target_id": task_id, "message": "was not paused"}
        rehydrated_count, rehydrate_status, rehydrate_errors = await _rehydrate_entity_after_resume(
            app,
            controller,
            "task",
            task_id,
            has_handles=bool(record.agent_handles),
        )
        return {
            "resumed": True,
            "kind": "task",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
            "rehydrated_count": rehydrated_count,
            "rehydrate_status": rehydrate_status,
            "rehydrate_errors": rehydrate_errors,
        }

    # --- Agent pause/resume ---

    @app.post("/api/agents/{agent_id}/pause")
    async def api_pause_agent(agent_id: str, req: PauseEntityRequest) -> dict[str, object]:
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "paused": False}
        handles, quiesce_status, quiesce_errors = await _quiesce_entity_for_pause(
            app, controller, "agent", agent_id
        )
        if quiesce_status == "none":
            record = controller.pause("agent", agent_id, reason=req.reason)
        else:
            record = controller.pause(
                "agent",
                agent_id,
                reason=req.reason,
                agent_handles=handles,
                quiesce_status=quiesce_status,
                quiesce_errors=quiesce_errors,
            )
        return {
            "paused": True,
            "kind": "agent",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
            "reason": record.reason,
            "quiesce_status": quiesce_status,
            "quiesce_errors": quiesce_errors,
        }

    @app.post("/api/agents/{agent_id}/resume")
    async def api_resume_agent(agent_id: str, _req: ResumeEntityRequest | None = None) -> dict[str, object]:
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "resumed": False}
        record = controller.resume("agent", agent_id)
        if record is None:
            return {"resumed": False, "target_id": agent_id, "message": "was not paused"}
        rehydrated_count, rehydrate_status, rehydrate_errors = await _rehydrate_entity_after_resume(
            app,
            controller,
            "agent",
            agent_id,
            has_handles=bool(record.agent_handles),
        )
        return {
            "resumed": True,
            "kind": "agent",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
            "rehydrated_count": rehydrated_count,
            "rehydrate_status": rehydrate_status,
            "rehydrate_errors": rehydrate_errors,
        }

    # --- Infra pause/resume ---

    @app.post("/api/infra/{deployment_id}/pause")
    async def api_pause_infra(deployment_id: str, req: PauseEntityRequest) -> dict[str, object]:
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "paused": False}
        record = controller.pause("infra", deployment_id, reason=req.reason)
        return {
            "paused": True,
            "kind": "infra",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
            "reason": record.reason,
        }

    @app.post("/api/infra/{deployment_id}/resume")
    async def api_resume_infra(deployment_id: str, _req: ResumeEntityRequest | None = None) -> dict[str, object]:
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "resumed": False}
        record = controller.resume("infra", deployment_id)
        if record is None:
            return {"resumed": False, "target_id": deployment_id, "message": "was not paused"}
        return {
            "resumed": True,
            "kind": "infra",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
        }

    @app.post("/api/pause/project")
    async def api_pause_project(req: PauseRequest) -> dict[str, object]:
        """Pause a project. Idempotent — re-pausing returns the existing record."""
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "paused": False}
        dispatcher = getattr(app.state, "_agent_dispatcher", None)
        hibernation = getattr(app.state, "_hibernation_controller", None)
        quiesce_result = await controller.quiesce_project(
            req.target_id,
            dispatcher=dispatcher,
            hibernation=hibernation,
        )
        handles: list[object]
        if isinstance(quiesce_result, tuple):
            quiesce_handles, quiesce_status, quiesce_errors = quiesce_result
            handles = list(quiesce_handles)
        else:
            handles = quiesce_result[:]
            quiesce_status = "clean"
            quiesce_errors = []
        record = controller.pause(
            "project",
            req.target_id,
            reason=req.reason,
            resources=req.resources or None,
            last_state=req.last_state or None,
            agent_handles=cast(list[object] | None, handles),
            quiesce_status=quiesce_status,
            quiesce_errors=quiesce_errors,
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
    async def api_pause_model(req: PauseRequest) -> dict[str, object]:
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
    async def api_resume_project(req: ResumeRequest) -> dict[str, object]:
        """Resume a paused project. Returns null when not paused (idempotent)."""
        controller = _get_controller(app)
        if controller is None:
            return {"error": "pause controller not available", "resumed": False}
        record = controller.resume("project", req.target_id)
        if record is None:
            return {"resumed": False, "target_id": req.target_id, "message": "was not paused"}
        rehydrated_count = 0
        rehydrate_errors: list[str] = []
        if record.agent_handles:
            dispatcher = getattr(app.state, "_agent_dispatcher", None)
            hibernation = getattr(app.state, "_hibernation_controller", None)
            if dispatcher is not None and hibernation is not None:
                # S8: Use resume_rehydrate (canonical path) instead of inline
                # rehydration loop that drops depth/messages on AgentTask.
                snapshots, _status, errors = await controller.resume_rehydrate(
                    "project",
                    req.target_id,
                    dispatcher=dispatcher,
                    hibernation=hibernation,
                )
                rehydrated_count = len(snapshots)
                rehydrate_errors = list(errors)
        return {
            "resumed": True,
            "kind": "project",
            "target_id": record.target_id,
            "paused_at": record.paused_at,
            "rehydrated_count": rehydrated_count,
            "rehydrate_errors": rehydrate_errors,
        }

    @app.post("/api/resume/model")
    async def api_resume_model(req: ResumeRequest) -> dict[str, object]:
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
