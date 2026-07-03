"""Model weight schema — validated entry for the cold-start routing prior."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from general_ludd.schemas.benchmark import TaskRole

_VALID_SOURCES = {"benchmark", "operator", "manual"}


class ModelWeightSchema(BaseModel):
    model_id: str = Field(min_length=1)
    task_role: TaskRole
    weight: float = Field(ge=0.0, le=1.0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = Field(default="manual")

    def __init__(self, **data):
        super().__init__(**data)
        if self.source not in _VALID_SOURCES:
            raise ValueError(
                f"source must be one of {_VALID_SOURCES!r}, got {self.source!r}"
            )
