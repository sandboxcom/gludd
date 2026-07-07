"""Approval gate status endpoint.

Endpoint:
    GET /admin/approval/status  — return approval gate wired status

PSK authentication is applied globally by the daemon middleware.
The shared ApprovalGate instance is stored on ``app.state._approval_gate``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _get_gate(app: FastAPI) -> object:
    return getattr(app.state, "_approval_gate", None)


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    @app.get("/admin/approval/status")
    async def admin_approval_status() -> dict[str, object]:
        gate = _get_gate(app)
        wired = gate is not None
        return {
            "wired": wired,
            "gate_type": type(gate).__name__ if wired else "None",
        }
