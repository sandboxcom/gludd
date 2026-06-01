"""Job specification schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JobSpec(BaseModel):
    job_id: str
    todo_id: str | None = None
    return_id: str | None = None
    project_id: str | None = None
    playbook: str
    queue: str
    work_type: str = "unknown"
    resource_profile: str = "low_resource"
    model_profile: str | None = None
    prompt_profile: str | None = None
    vars_namespace_refs: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None
    budget_context: dict[str, Any] = Field(default_factory=dict)
    candidate_todos: list[str] = Field(default_factory=list)
    artifact_summaries: list[str] = Field(default_factory=list)
    plan_artifact: str | None = None
