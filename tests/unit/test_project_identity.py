"""Core project identity primitives stay below business orchestration layers."""

from __future__ import annotations

import pytest

from general_ludd.projects.identity import (
    ProjectResourceIdentity as CompatibilityProjectResourceIdentity,
)
from general_ludd.projects.identity import validate_project_id as compatibility_validate_project_id
from general_ludd.schemas.project_identity import (
    ProjectResourceIdentity,
    ProjectWorkIdentity,
    validate_project_id,
)


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


def test_project_work_identity_scopes_runtime_keys_to_the_owner() -> None:
    first = ProjectWorkIdentity("project-a", "TODO-1", "core")
    second = ProjectWorkIdentity("project-b", "TODO-1", "core")

    assert first.scheduler_id == "project-a:TODO-1"
    assert first.resume_shard_id == "project-a:TODO-1"
    assert first.todo_resource == "project:project-a:todo:TODO-1"
    assert first.queue_resource == "project:project-a:queue:core"
    assert first.lease_bucket_key == "core:TODO-1"
    assert first != second


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("todo_id", {"todo_id": "../escape"}),
        ("queue", {"queue": "bad/queue"}),
    ],
)
def test_project_work_identity_rejects_unsafe_components(
    field: str,
    kwargs: dict[str, str],
) -> None:
    values = {"project_id": "project-a", "todo_id": "TODO-1", "queue": "core"}
    values.update(kwargs)

    with pytest.raises(ValueError, match=field):
        ProjectWorkIdentity(**values)
