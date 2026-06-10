from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

from general_ludd.schemas.todo import (
    ResourceProfile,
    RiskLevel,
    Todo,
    WorkType,
)


class TaskDefinition(BaseModel):
    name: str
    description: str = ""
    target_agent: str = "build"
    queue: str = "core"
    work_type: str = "code"
    priority: int = 0
    tags: list[str] = []
    dependencies: list[str] = []
    acceptance_criteria: list[str] = []
    test_commands: list[str] = []
    model_profile: str | None = None
    prompt_profile: str | None = None
    resource_profile: str = "low_resource"
    risk_level: str = "low"
    vars: dict[str, Any] = {}

    @field_validator("name", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    def to_todo(self) -> Todo:
        return Todo(
            title=self.name,
            description=self.description,
            assigned_agent=self.target_agent,
            queue=self.queue,
            work_type=WorkType(self.work_type),
            priority=self.priority,
            tags=list(self.tags),
            risk_level=RiskLevel(self.risk_level),
            resource_profile=ResourceProfile(self.resource_profile),
            acceptance_criteria=list(self.acceptance_criteria),
            test_commands=list(self.test_commands),
            dependencies=list(self.dependencies),
            model_profile=self.model_profile,
            prompt_profile=self.prompt_profile,
        )
