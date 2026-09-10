"""Tests for pure Azure bootstrap ownership planning."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from general_ludd.self_improve.azure_containerapp_bootstrap_planning import (
    azure_bootstrap_owner_digest,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_settings import (
    AzureContainerAppBootstrapSettings,
)


def test_owner_digest_is_project_scoped_and_deterministic(tmp_path: Path) -> None:
    """Environment ownership binds the same Azure names to one project root."""
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )

    first = azure_bootstrap_owner_digest(tmp_path, settings)
    second = azure_bootstrap_owner_digest(tmp_path, settings)
    other = azure_bootstrap_owner_digest(tmp_path / "other", settings)

    assert first == second
    assert first != other
    assert len(first) == 64
