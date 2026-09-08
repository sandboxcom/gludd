"""Private request and response contracts shared by pause/resume routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from general_ludd.controllers.pause_controller import PauseKind


class PauseEntityRequest(BaseModel):
    """Request body for pausing a task, agent, or infrastructure entity."""

    reason: str = Field("", description="Reason for pausing")


class ResumeEntityRequest(BaseModel):
    """Explicit empty request body for entity resume operations."""


class PauseRequest(BaseModel):
    """Request body for pausing a project or model with optional state."""

    target_id: str = Field(..., min_length=1, description="Project or model identifier")
    reason: str = Field("", description="Reason for pausing")
    resources: dict[str, object] | None = Field(
        None,
        description="Snapshot of daemon state (spend, leases, registries) at pause time",
    )
    last_state: dict[str, object] | None = Field(
        None,
        description="Last working state before pause (phase, cursor, etc.)",
    )


class ResumeRequest(BaseModel):
    """Request body identifying a project or model to resume."""

    target_id: str = Field(..., min_length=1, description="Project or model identifier")


def format_pause_record(record: Any) -> dict[str, object]:
    """Serialize one stored pause record."""
    return {
        "kind": record.kind,
        "target_id": record.target_id,
        "paused_at": record.paused_at,
        "reason": record.reason,
    }


def pause_entity_response(
    record: Any,
    *,
    kind: PauseKind,
    quiesce_status: str,
    quiesce_errors: list[str],
) -> dict[str, object]:
    """Serialize a pause record without inventing quiescence metadata."""
    response: dict[str, object] = {
        "paused": True,
        "kind": kind,
        "target_id": record.target_id,
        "paused_at": record.paused_at,
        "reason": record.reason,
    }
    if quiesce_status != "none":
        response["quiesce_status"] = quiesce_status
        response["quiesce_errors"] = quiesce_errors
    return response


def resume_entity_response(
    record: Any,
    *,
    kind: PauseKind,
    rehydrated_count: int,
    rehydrate_status: str,
    rehydrate_errors: list[str],
) -> dict[str, object]:
    """Serialize resume metadata only when a rehydration path ran."""
    response: dict[str, object] = {
        "resumed": True,
        "kind": kind,
        "target_id": record.target_id,
        "paused_at": record.paused_at,
    }
    if rehydrate_status != "none":
        response["rehydrated_count"] = rehydrated_count
        response["rehydrate_status"] = rehydrate_status
        response["rehydrate_errors"] = rehydrate_errors
    return response


__all__ = (
    "PauseEntityRequest",
    "PauseRequest",
    "ResumeEntityRequest",
    "ResumeRequest",
    "format_pause_record",
    "pause_entity_response",
    "resume_entity_response",
)
