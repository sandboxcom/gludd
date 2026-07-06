"""Job specification schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


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
    prompt_text: str | None = None
    skill_body: str | None = None
    ansible_roles_path: str | None = None
    templates_dir: str | None = None
    timeout: float | None = None
    human_input: str | None = None

    @field_validator("timeout", mode="before")
    @classmethod
    def _validate_timeout(cls, v: object) -> object:
        if v is None:
            return v
        if not isinstance(v, (int, float, str)):
            raise ValueError("timeout must be a number or None")
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError("timeout must be a number or None") from None
        if fv <= 0:
            raise ValueError(f"timeout must be positive (got {fv})")
        return fv

    @field_validator("job_id", "playbook", "queue", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v
