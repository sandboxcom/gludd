"""Compaction-aggressiveness status endpoint.

Endpoint:
    GET /admin/compaction/aggressiveness-status — read controller parameters

The shared CompactionAggressivenessController instance is stored on
``app.state._compaction_aggressiveness_controller``.
"""

from __future__ import annotations

from fastapi import FastAPI

from general_ludd.controllers.compaction_aggressiveness import (
    CompactionAggressivenessController,
)


def _get_controller(app: FastAPI) -> CompactionAggressivenessController | None:
    controller = getattr(app.state, "_compaction_aggressiveness_controller", None)
    if controller is None:
        return None
    return controller if isinstance(controller, CompactionAggressivenessController) else None


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    @app.get("/admin/compaction/aggressiveness-status")
    async def api_compaction_aggressiveness_status() -> dict[str, object]:
        controller = _get_controller(app)
        if controller is None:
            return {"available": False}
        return {
            "available": True,
            "floor": controller.floor,
            "min_samples": controller.min_samples,
            "max_level": controller.max_level,
        }
