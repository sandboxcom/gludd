"""HTTP router: human-in-the-loop review endpoints.

PSK-gated (admin-only). Surfaces:

  - ``POST /admin/review/approve/{thread_id}``
        Resume a paused review graph with a human decision.
  - ``GET  /admin/review/pending``
        List currently paused review gates.

All endpoints require the daemon PSK (admin middleware enforces it on every
``/admin/*`` path).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ApproveRequest(BaseModel):
    decision: str = Field(
        description="Human decision: approved, denied, or needs_more_work",
        examples=["approved"],
    )


class PendingGate(BaseModel):
    thread_id: str = Field(description="Thread identifier for the paused gate")


class PendingResponse(BaseModel):
    pending: list[PendingGate]
    count: int
    available: bool
    enabled: bool


def register(app: FastAPI, daemon_state: dict[str, Any]) -> None:
    def _human_gate() -> Any:
        return daemon_state.get("human_gate")

    @app.post(
        "/admin/review/approve/{thread_id}",
        response_model=None,
        summary="Resume a paused review gate with a human decision",
    )
    async def admin_review_approve(thread_id: str, body: ApproveRequest) -> dict[str, Any]:
        gate = _human_gate()
        if gate is None:
            raise HTTPException(status_code=503, detail="HumanGate not available")
        ok = await gate.resume(thread_id, body.decision)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"No pending gate for thread {thread_id}",
            )
        return {"status": "ok", "thread_id": thread_id, "decision": body.decision}

    @app.get(
        "/admin/review/pending",
        response_model=None,
        summary="List currently paused review gates",
    )
    async def admin_review_pending() -> dict[str, Any]:
        gate = _human_gate()
        if gate is None:
            raise HTTPException(status_code=503, detail="HumanGate not available")
        return {
            "pending": [
                {"thread_id": tid} for tid in gate.pending_thread_ids
            ],
            "count": gate.pending_count,
            "available": gate.available,
            "enabled": gate.enabled,
        }
