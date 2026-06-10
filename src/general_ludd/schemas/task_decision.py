"""Task decision schema."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TaskDecision(BaseModel):
    return_id: str
    matched_todo_id: str | None = None
    decision: str
    confidence: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)
    todo_updates: dict[str, Any] = Field(default_factory=dict)
    child_todos: list[dict[str, Any]] = Field(default_factory=list)
    validation_requests: list[str] = Field(default_factory=list)
    git_requests: list[str] = Field(default_factory=list)
    audit_notes: list[str] = Field(default_factory=list)
    policy_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("return_id", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("return_id must not be empty")
        return v

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v

    @classmethod
    def valid_decisions(cls) -> set[str]:
        return {
            "complete",
            "needs_more_work",
            "failed",
            "blocked",
            "manual_hold",
            "ignore_duplicate",
        }

    def model_post_init(self, __context: Any) -> None:
        if self.decision not in self.valid_decisions():
            raise ValueError(f"Invalid decision: {self.decision}. Must be one of {self.valid_decisions()}")
