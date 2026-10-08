"""Small reusable blocks for user-facing daemon configuration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class IssuesConfig(BaseModel):
    """Configure bounded GitHub issue polling."""

    polling_enabled: bool = False
    poll_interval_ticks: int = 300
    github_owner: str = ""
    github_repo: str = ""
    github_label: str = "gludd"


class NotificationsConfig(BaseModel):
    """Configure notification backends and minimum priority."""

    enabled: bool = False
    backends: dict[str, Any] = {"stdout": {}}
    min_priority: str = "high"
