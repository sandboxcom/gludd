"""Focused module-boundary tests for environment retention orchestration."""

from general_ludd.infra.azure_containerapp_environment_retention import (
    release_azure_containerapp_environment,
)


def test_retention_module_exposes_the_owned_release_boundary() -> None:
    """Keep release orchestration independently importable from its facade."""
    assert callable(release_azure_containerapp_environment)
