"""Queue schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class Queue(BaseModel):
    queue_name: str
    queue_enabled: bool = True
    priority_weight: int = 100
    resource_profile: str = "low_resource"
    hard_cap: int = 10
    soft_cap: int = 5
    pid_group: str | None = None
    allowed_playbooks: list[str] = Field(default_factory=list)
    allowed_model_profiles: list[str] = Field(default_factory=list)
    allowed_prompt_profiles: list[str] = Field(default_factory=list)
    required_molecule_coverage_profile: str | None = None
    max_error_rate: float = 0.5
    retry_policy: dict[str, Any] = Field(default_factory=dict)

    @field_validator("queue_name", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("queue_name must not be empty")
        import re
        if not re.match(r"^[a-z0-9_\-]+$", v):
            raise ValueError("queue_name must match ^[a-z0-9_\\-]+$")
        return v

    @field_validator("hard_cap")
    @classmethod
    def _hard_cap_min(cls, v: int) -> int:
        if v < 1:
            raise ValueError("hard_cap must be at least 1")
        return v

    @field_validator("max_error_rate")
    @classmethod
    def _error_rate_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("max_error_rate must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _caps_consistent(self) -> Queue:
        if self.soft_cap > self.hard_cap:
            raise ValueError("soft_cap must not exceed hard_cap")
        return self


INITIAL_QUEUES: list[Queue] = [
    Queue(
        queue_name="intake",
        resource_profile="low_resource",
        allowed_playbooks=["noop.yml"],
    ),
    Queue(
        queue_name="core",
        resource_profile="low_resource",
        allowed_playbooks=["noop.yml"],
    ),
    Queue(
        queue_name="worker",
        resource_profile="hybrid",
        allowed_playbooks=[
            "noop.yml",
            "return_review.yml",
            "validate_task.yml",
        ],
    ),
    Queue(
        queue_name="ansible",
        resource_profile="local_heavy",
        allowed_playbooks=["noop.yml"],
    ),
    Queue(
        queue_name="model",
        resource_profile="ai_heavy",
        allowed_playbooks=["noop.yml", "return_review.yml"],
    ),
    Queue(
        queue_name="qa",
        resource_profile="local_heavy",
        allowed_playbooks=[
            "noop.yml",
            "validate_task.yml",
            "molecule_test.yml",
        ],
    ),
    Queue(
        queue_name="infra",
        resource_profile="hybrid",
        allowed_playbooks=["noop.yml", "openbao_bootstrap.yml"],
    ),
    Queue(
        queue_name="dependency",
        resource_profile="network_heavy",
        allowed_playbooks=["noop.yml", "dependency_update.yml"],
    ),
    Queue(
        queue_name="git",
        resource_profile="low_resource",
        allowed_playbooks=[
            "noop.yml",
            "git_repo_init.yml",
            "git_manage_worktree.yml",
            "git_automate_change.yml",
        ],
    ),
    Queue(
        queue_name="self_improve",
        resource_profile="hybrid",
        allowed_playbooks=[
            "noop.yml",
            "self_improve_harness.yml",
            "reload_harness.yml",
        ],
    ),
    Queue(
        queue_name="audit",
        resource_profile="ai_heavy",
        allowed_playbooks=["noop.yml", "gap_analysis.yml", "log_audit.yml"],
    ),
    Queue(
        queue_name="manual_hold",
        queue_enabled=False,
        resource_profile="low_resource",
        allowed_playbooks=[],
    ),
]
