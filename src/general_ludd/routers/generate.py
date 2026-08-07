"""Generic code generation API — POST /api/generate/*.

Exposes project-type-aware generation via SoftwareGenerator.
The existing /api/game/generate-multi endpoint maps to --type=game.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from general_ludd.cloud.project_types import (
    PROJECT_TYPE_REGISTRY,
    available_type_ids,
    get_project_type,
)
from general_ludd.cloud.software_generator import ProjectSpec, SoftwareGenerator

logger = logging.getLogger(__name__)


class GenerateCreateRequest(BaseModel):
    project_type: str
    description: str
    planner_model: str = "default"
    coder_model: str = "default"
    reviewer_model: str = "default"
    max_review_rounds: int = 3

    @model_validator(mode="after")
    def _validate_request(self) -> GenerateCreateRequest:
        if not self.description.strip():
            raise ValueError("description is required")
        if self.project_type not in PROJECT_TYPE_REGISTRY:
            available = ", ".join(sorted(PROJECT_TYPE_REGISTRY))
            raise ValueError(f"Unknown project type: {self.project_type!r}. Available: {available}")
        return self


class GenerateValidateRequest(BaseModel):
    project_type: str
    project_dir: str


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/api/generate/list-types")
    async def api_generate_list_types() -> dict[str, object]:
        items = []
        for type_id in available_type_ids():
            pt_def = PROJECT_TYPE_REGISTRY[type_id]
            items.append(
                {
                    "name": pt_def.type_id,
                    "display_name": pt_def.display_name,
                    "default_entry_point": pt_def.default_entry_point,
                    "output_structure": dict(pt_def.output_structure),
                    "acceptance_criteria": list(pt_def.acceptance_criteria),
                }
            )
        return {"project_types": items}

    @app.post("/api/generate/create")
    async def api_generate_create(
        body: GenerateCreateRequest,
    ) -> dict[str, object]:
        gateway = getattr(app.state, "_model_gateway", None)
        if gateway is None:
            raise HTTPException(status_code=503, detail="ModelGateway not configured")

        pt_def = get_project_type(body.project_type)
        spec = ProjectSpec(
            name=body.project_type,
            project_type=body.project_type,
            description=body.description,
            prompt_template=pt_def.prompt_template_coder or pt_def.prompt_template_planner or body.description,
            expected_output_files=len(pt_def.output_structure),
            acceptance_criteria=tuple(pt_def.acceptance_criteria) if pt_def.acceptance_criteria else (),
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
            logger.exception("Generation failed for type %s: %s", body.project_type, exc)
            raise HTTPException(
                status_code=500,
                detail={"error": f"Generation failed: {exc}"},
            ) from exc

        return {
            "project_type": body.project_type,
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

    @app.post("/api/generate/validate")
    async def api_generate_validate(
        body: GenerateValidateRequest,
    ) -> dict[str, object]:
        from pathlib import Path

        if body.project_type not in PROJECT_TYPE_REGISTRY:
            available = ", ".join(sorted(PROJECT_TYPE_REGISTRY))
            return {
                "valid": False,
                "errors": [f"Unknown project type: {body.project_type!r}. Available: {available}"],
            }

        pt_def = PROJECT_TYPE_REGISTRY[body.project_type]
        path = Path(body.project_dir)
        errors: list[str] = []

        if not path.exists():
            errors.append(f"Project directory does not exist: {body.project_dir}")
        elif not path.is_dir():
            errors.append(f"Path is not a directory: {body.project_dir}")
        else:
            for expected_file in pt_def.output_structure:
                file_path = path / expected_file
                if not file_path.exists():
                    errors.append(f"Expected file not found: {expected_file}")

        return {
            "valid": len(errors) == 0,
            "project_type": body.project_type,
            "errors": errors,
        }
