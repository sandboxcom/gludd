"""Canonical project and project-owned resource identity value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PROJECT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


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
