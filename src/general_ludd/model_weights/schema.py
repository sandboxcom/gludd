"""Typed schema for per-model / per-role weight configuration.

Each ``ModelRoleWeight`` entry records how heavily a specific model should be
preferred for a given ``TaskRole``.  A ``ModelWeightConfig`` groups all entries
for a deployment and validates that:

- every weight is in [0.0, 1.0]
- model_id and (implicitly) role are non-empty
- no duplicate (model_id, role) pairs exist within one config

This schema intentionally stays minimal.  It records *relative preference*
weights, not probabilities that must sum to 1.  The router is free to
normalise however it likes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from general_ludd.schemas.benchmark import TaskRole


class ModelRoleWeight(BaseModel):
    """Weight for one (model_id, role) pair."""

    model_id: str = Field(..., description="Opaque model identifier (e.g. 'claude-sonnet-4-6')")
    role: TaskRole
    weight: float = Field(..., ge=0.0, le=1.0, description="Preference weight in [0.0, 1.0]")

    @field_validator("model_id", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("model_id must not be empty")
        return v


class ModelWeightConfig(BaseModel):
    """Collection of per-model/per-role weights for a deployment."""

    entries: list[ModelRoleWeight] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_pairs(self) -> ModelWeightConfig:
        seen: set[tuple[str, TaskRole]] = set()
        for entry in self.entries:
            key = (entry.model_id, entry.role)
            if key in seen:
                raise ValueError(
                    f"duplicate (model_id, role) pair: {entry.model_id!r}, {entry.role!r}"
                )
            seen.add(key)
        return self

    def weight_for(self, model_id: str, role: TaskRole, default: float = 0.0) -> float:
        """Return the configured weight for *model_id* + *role*, or *default*."""
        for entry in self.entries:
            if entry.model_id == model_id and entry.role == role:
                return entry.weight
        return default
