"""Compaction-aggressiveness status endpoint.

Endpoint:
    GET /admin/compaction/aggressiveness-status — read controller parameters

The shared CompactionAggressivenessController instance is stored on
``app.state._compaction_aggressiveness_controller``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def _get_controller(app: FastAPI) -> Any:
    return getattr(app.state, "_compaction_aggressiveness_controller", None)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get("/admin/compaction/aggressiveness-status")
    async def api_compaction_aggressiveness_status() -> dict[str, Any]:
        controller = _get_controller(app)
        if controller is None:
            return {"available": False}
        return {
            "available": True,
            "floor": controller.floor,
            "min_samples": controller.min_samples,
            "max_level": controller.max_level,
        }
