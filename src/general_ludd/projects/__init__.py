"""Projects — multi-project management and workspace isolation."""

__all__ = (
    "ProjectAllocationError",
    "ProjectManager",
    "ProjectWeight",
    "ProjectWorkspace",
    "seed_from_config",
)

from general_ludd.projects.manager import (
    ProjectAllocationError,
    ProjectManager,
    ProjectWeight,
    seed_from_config,
)
from general_ludd.projects.workspace import ProjectWorkspace
