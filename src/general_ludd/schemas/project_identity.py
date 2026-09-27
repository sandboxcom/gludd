"""Canonical project and project-owned resource identity value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PROJECT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_WORK_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def validate_project_id(project_id: str) -> str:
    """Return a safe bounded project identifier or fail closed."""
    if not isinstance(project_id, str) or _PROJECT_ID_RE.fullmatch(project_id) is None:
        raise ValueError("project_id must be a bounded identifier")
    return project_id


@dataclass(frozen=True, order=True)
class ProjectResourceIdentity:
    """Collision-free identity for a resource owned by one Gludd project."""

    project_id: str
    provider: str
    instance_id: str

    def __post_init__(self) -> None:
        """Validate every component before the identity can enter a registry."""
        validate_project_id(self.project_id)
        if not self.provider:
            raise ValueError("provider must not be empty")
        if not self.instance_id:
            raise ValueError("instance_id must not be empty")


@dataclass(frozen=True, order=True, slots=True)
class ProjectWorkIdentity:
    """Stable project-owned identity for one schedulable todo.

    Scheduler, resume, and execution-lease identifiers include the project
    owner. Database uniqueness is an implementation detail, not an ownership
    boundary: imports and migrations can legitimately preserve the same
    business todo identifier in two projects.
    """

    project_id: str
    todo_id: str
    queue: str = "core"

    def __post_init__(self) -> None:
        """Reject unsafe or ambiguous runtime identity components."""
        validate_project_id(self.project_id)
        self._validate_component(self.todo_id, "todo_id")
        self._validate_component(self.queue, "queue")

    @staticmethod
    def _validate_component(value: str, field_name: str) -> None:
        if not isinstance(value, str) or _WORK_COMPONENT_RE.fullmatch(value) is None:
            raise ValueError(f"{field_name} must be a bounded identifier")

    @property
    def scheduler_id(self) -> str:
        """Return the collision-free scheduler graph node identifier."""
        return f"{self.project_id}:{self.todo_id}"

    @property
    def resume_shard_id(self) -> str:
        """Return the durable checkpoint shard identifier."""
        return self.scheduler_id

    @property
    def todo_resource(self) -> str:
        """Return the project-scoped exclusive todo resource label."""
        return f"project:{self.project_id}:todo:{self.todo_id}"

    @property
    def queue_resource(self) -> str:
        """Return the project-scoped queue resource label."""
        return f"project:{self.project_id}:queue:{self.queue}"

    @property
    def lease_bucket_key(self) -> str:
        """Return the collision-free project-owned execution bucket key."""
        return (
            f"project:{self.project_id}:queue:{self.queue}:todo:{self.todo_id}"
        )

    @classmethod
    def from_lease_bucket_key(cls, bucket_key: str) -> ProjectWorkIdentity | None:
        """Parse one canonical project-owned lease key, or return ``None``.

        Returning ``None`` for legacy/unowned or malformed keys lets recovery
        code retain compatibility without ever guessing a project owner.
        """
        if not isinstance(bucket_key, str):
            return None
        parts = bucket_key.split(":")
        if (
            len(parts) != 6
            or parts[0] != "project"
            or parts[2] != "queue"
            or parts[4] != "todo"
        ):
            return None
        try:
            return cls(project_id=parts[1], queue=parts[3], todo_id=parts[5])
        except ValueError:
            return None
