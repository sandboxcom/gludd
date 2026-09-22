"""Core project identity primitives stay below business orchestration layers."""

from __future__ import annotations

import pytest

from general_ludd.projects.identity import (
    ProjectResourceIdentity as CompatibilityProjectResourceIdentity,
)
from general_ludd.projects.identity import validate_project_id as compatibility_validate_project_id
from general_ludd.schemas.project_identity import ProjectResourceIdentity, validate_project_id


def test_core_identity_is_the_canonical_compatibility_export() -> None:
    assert CompatibilityProjectResourceIdentity is ProjectResourceIdentity
    assert compatibility_validate_project_id is validate_project_id


def test_core_identity_is_collision_free_and_validated() -> None:
    identities = {
        ProjectResourceIdentity("project-a", "azure", "shared-id"),
        ProjectResourceIdentity("project-b", "azure", "shared-id"),
        ProjectResourceIdentity("project-a", "aws", "shared-id"),
    }

    assert len(identities) == 3
    assert validate_project_id("team.one_2") == "team.one_2"
    with pytest.raises(ValueError, match="project_id must be a bounded identifier"):
        ProjectResourceIdentity("../escape", "azure", "shared-id")
