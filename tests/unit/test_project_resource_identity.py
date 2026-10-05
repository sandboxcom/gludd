"""Project identity contracts shared by resource-owning subsystems."""

from __future__ import annotations

import pytest

from general_ludd.projects.identity import ProjectResourceIdentity, validate_project_id


@pytest.mark.parametrize("project_id", ["default", "project-a", "team.one_2"])
def test_validate_project_id_preserves_bounded_identifiers(project_id: str) -> None:
    assert validate_project_id(project_id) == project_id


@pytest.mark.parametrize("project_id", ["", "../escape", "has space", "a" * 129])
def test_validate_project_id_rejects_unsafe_or_unbounded_values(project_id: str) -> None:
    with pytest.raises(ValueError, match="project_id must be a bounded identifier"):
        validate_project_id(project_id)


def test_resource_identity_includes_project_provider_and_instance() -> None:
    azure_a = ProjectResourceIdentity("project-a", "azure", "shared-id")
    azure_b = ProjectResourceIdentity("project-b", "azure", "shared-id")
    aws_a = ProjectResourceIdentity("project-a", "aws", "shared-id")

    assert len({azure_a, azure_b, aws_a}) == 3


def test_resource_identity_validates_project_at_construction() -> None:
    with pytest.raises(ValueError, match="project_id must be a bounded identifier"):
        ProjectResourceIdentity("../escape", "azure", "instance")


@pytest.mark.parametrize(
    ("provider", "instance_id", "message"),
    [
        ("", "instance", "provider must not be empty"),
        ("azure", "", "instance_id must not be empty"),
    ],
)
def test_resource_identity_rejects_empty_provider_or_instance(
    provider: str,
    instance_id: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ProjectResourceIdentity("project-a", provider, instance_id)
