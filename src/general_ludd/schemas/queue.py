"""Queue schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
