from __future__ import annotations

import contextlib
import logging
from typing import cast

from fastapi import FastAPI, HTTPException, Request

from general_ludd.models.performance_router import (
    DEFAULT_STRATEGIES,
    ModelPerformanceRepository,
    ModelPerformanceRouter,
)

logger = logging.getLogger(__name__)


def _get_performance_router(app: FastAPI) -> ModelPerformanceRouter | None:
    return getattr(app.state, "_model_performance_router", None)


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    if not hasattr(app.state, "_model_performance_router"):
        app.state._model_performance_router = None

    @app.get(
        "/admin/models/performance",
        summary="Model performance summary data",
        description="Return aggregated performance data for model+task-type combos.",
    )
    async def admin_models_performance(
        service: str | None = None,
        task_type: str | None = None,
    ) -> dict[str, object]:
        router = _get_performance_router(app)
        if router is None or router._repo is None:
            return {"performance": [], "note": "ModelPerformanceRouter not wired"}
        try:
            repo = cast(ModelPerformanceRepository, router._repo)
            data = await repo.get_summary(
                service=service,
                task_type=task_type,
            )
            return {"performance": data}
        except Exception as exc:
            logger.warning("performance summary failed: %s", exc)
            return {"performance": [], "error": str(exc)}

    @app.get(
        "/admin/models/ranking",
        summary="Ranked models for a task type",
        description="Return ranked list of (service, model_name) for a task type.",
    )
    async def admin_models_ranking(
        task_type: str,
        strategy: str = "balanced",
    ) -> dict[str, object]:
        if not task_type:
            raise HTTPException(status_code=422, detail="task_type is required")
        if strategy not in DEFAULT_STRATEGIES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown strategy {strategy!r}. Valid: {', '.join(sorted(DEFAULT_STRATEGIES))}",
            )
        router = _get_performance_router(app)
        if router is None:
            raise HTTPException(status_code=503, detail="ModelPerformanceRouter not wired")
        try:
            ranking = await router.get_rankings(task_type, strategy=strategy)
            return {
                "task_type": task_type,
                "strategy": strategy,
                "ranking": ranking,
            }
        except Exception as exc:
            logger.warning("ranking failed for %s: %s", task_type, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get(
        "/admin/models/router/status",
        summary="Current router configuration",
        description="Return current routing strategies and active model selections.",
    )
    async def admin_models_router_status() -> dict[str, object]:
        router = _get_performance_router(app)
        if router is None:
            return {"status": "not_initialized"}
        return {
            "status": "active",
            "config": router.get_config(),
        }

    @app.put(
        "/admin/models/router/config",
        summary="Update router configuration",
        description="Set routing strategy for a task type.",
    )
    async def admin_models_router_config(request: Request) -> dict[str, object]:
        router = _get_performance_router(app)
        if router is None:
            raise HTTPException(status_code=503, detail="ModelPerformanceRouter not wired")
        body: dict[str, object] = {}
        with contextlib.suppress(Exception):
            body = await request.json()
        task_type = body.get("task_type", "")
        strategy = body.get("strategy", "balanced")
        if not task_type:
            raise HTTPException(status_code=422, detail="task_type is required")
        if strategy not in DEFAULT_STRATEGIES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown strategy {strategy!r}. Valid: {', '.join(sorted(DEFAULT_STRATEGIES))}",
            )
        router.set_strategy(cast(str, task_type), strategy)
        return {
            "task_type": task_type,
            "strategy": strategy,
            "updated": True,
        }
