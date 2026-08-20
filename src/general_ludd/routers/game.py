"""Multi-model game generation API — POST /api/game/generate-multi.

Delegates to SoftwareGenerator.generate_multi() with project_type="game".
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from general_ludd.cloud.project_types import get_project_type
from general_ludd.cloud.software_generator import ProjectSpec, SoftwareGenerator
from general_ludd.schemas.benchmark import TaskRole

logger = logging.getLogger(__name__)

_GAME_TYPE = "game"
_VALID_GAME_ROLES: frozenset[TaskRole] = frozenset(
    {TaskRole.PLANNER, TaskRole.CODER, TaskRole.REVIEWER}
)


class GameGenerateMultiRequest(BaseModel):
    """Validated role-specific model selection for game generation."""

    description: str
    planner_model: str = "default"
    coder_model: str = "default"
    reviewer_model: str = "default"
    max_review_rounds: int = 3

    @model_validator(mode="after")
    def _at_least_one_non_default_planner_coder_reviewer(self) -> GameGenerateMultiRequest:
        if self.planner_model == "default" and self.coder_model == "default" and self.reviewer_model == "default":
            raise ValueError(
                "at least one of planner_model, coder_model, or reviewer_model must be provided (non-'default')"
            )
        return self


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register the authenticated multi-model game generation route."""

    @app.post("/api/game/generate-multi")
    async def api_game_generate_multi(
        body: GameGenerateMultiRequest,
    ) -> dict[str, object]:
        gateway = getattr(app.state, "_model_gateway", None)
        if gateway is None:
            raise HTTPException(status_code=503, detail="ModelGateway not configured")

        if not body.description.strip():
            raise HTTPException(status_code=422, detail="description is required")

        pt = get_project_type(_GAME_TYPE)
        spec = ProjectSpec(
            name="game",
            project_type=_GAME_TYPE,
            description=body.description,
            prompt_template=pt.prompt_template_coder or pt.prompt_template_planner or body.description,
            expected_output_files=1,
            acceptance_criteria=tuple(pt.acceptance_criteria) if pt.acceptance_criteria else (),
        )

        generator = SoftwareGenerator(gateway=gateway)

        try:
            code = generator.generate_multi(
                spec,
                model_profiles={
                    "planner": body.planner_model,
                    "coder": body.coder_model,
                    "reviewer": body.reviewer_model,
                },
            )
        except Exception as exc:
            logger.exception("Multi-model game generation failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail={"error": "Game generation failed"},
            ) from exc

        return {
            "code": code,
            "design": {
                "description": body.description,
                "planner_model": body.planner_model,
            },
            "review": {
                "model": body.reviewer_model,
                "rounds": body.max_review_rounds,
            },
        }
