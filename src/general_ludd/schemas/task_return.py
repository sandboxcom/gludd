"""Task return schema."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


class TaskReturnStatus(enum.StrEnum):
    CREATED = "created"
    CLAIMED_FOR_REVIEW = "claimed_for_review"
    REVIEWED = "reviewed"
    ARCHIVED = "archived"


class TaskReturn(BaseModel):
    return_id: str
    todo_id: str | None = None
    job_id: str
    playbook: str
    queue: str
    work_type: str = "unknown"
    resource_profile: str = "low_resource"
    status: TaskReturnStatus = TaskReturnStatus.CREATED
    exit_code: int = 0
    result_summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    logs_ref: str | None = None
    diff_ref: str | None = None
    test_results_ref: str | None = None
    molecule_results_ref: str | None = None
    coverage_results_ref: str | None = None
    model_usage_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer_worker_id: str | None = None
    schema_version: int = 1

    @field_validator("return_id", "job_id", "playbook", "queue", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v
