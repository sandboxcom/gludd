"""Compatibility contract for the former project-layer identity module."""

from __future__ import annotations

from general_ludd.projects.identity import ProjectResourceIdentity, validate_project_id
from general_ludd.schemas.project_identity import (
    ProjectResourceIdentity as CoreProjectResourceIdentity,
)
from general_ludd.schemas.project_identity import validate_project_id as core_validate_project_id


def test_project_identity_module_reexports_the_core_primitives() -> None:
    assert ProjectResourceIdentity is CoreProjectResourceIdentity
    assert validate_project_id is core_validate_project_id
