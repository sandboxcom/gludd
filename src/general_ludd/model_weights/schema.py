"""Model weight schema — validated entry for the cold-start routing prior."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, Field
from pydantic.functional_validators import AfterValidator

from general_ludd.schemas.benchmark import TaskRole

_VALID_SOURCES = {"benchmark", "operator", "manual"}


def _validate_source(v: str) -> str:
    if v not in _VALID_SOURCES:
        raise ValueError(
            f"source must be one of {_VALID_SOURCES!r}, got {v!r}"
        )
    return v


class ModelWeightSchema(BaseModel):
    model_id: str = Field(min_length=1)
    task_role: TaskRole
    weight: float = Field(ge=0.0, le=1.0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: Annotated[str, AfterValidator(_validate_source)] = Field(default="manual")
