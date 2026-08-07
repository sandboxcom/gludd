"""Multi-model game generation API — POST /api/game/generate-multi.

Exposes :class:`MultiModelGamePipeline` through the daemon so that any
authenticated client can trigger a PLANNER → CODER → REVIEWER pipeline
with configurable per-role model selection and review rounds.
"""

from __future__ import annotations

import logging
from typing import cast

from fastapi import FastAPI, HTTPException, Request

from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline

logger = logging.getLogger(__name__)


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/api/game/generate-multi")
    async def api_game_generate_multi(request: Request) -> dict[str, object]:
        gateway = getattr(app.state, "_model_gateway", None)
        if gateway is None:
            raise HTTPException(
                status_code=503,
                detail="ModelGateway not configured",
            )

        body: dict[str, object] = await request.json()
        description = cast(str, body.get("description", ""))
        if not description.strip():
            raise HTTPException(
                status_code=422,
                detail="description is required",
            )

        planner_model = cast(str, body.get("planner_model", "default"))
        coder_model = cast(str, body.get("coder_model", "default"))
        reviewer_model = cast(str, body.get("reviewer_model", "default"))
        max_review_rounds = cast(int, body.get("max_review_rounds", 3))

        pipeline = MultiModelGamePipeline(gateway)

        try:
            code = pipeline.generate(
                description,
                planner_model=planner_model,
                coder_model=coder_model,
                reviewer_model=reviewer_model,
                max_review_rounds=max_review_rounds,
            )
        except Exception as exc:
            logger.exception("Multi-model game generation failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail={"error": f"Game generation failed: {exc}"},
            ) from exc

        return {
            "code": code,
            "design": {
                "description": description,
                "planner_model": planner_model,
            },
            "review": {
                "model": reviewer_model,
                "rounds": max_review_rounds,
            },
        }
