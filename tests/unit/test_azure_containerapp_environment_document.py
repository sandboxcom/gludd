"""Focused module-boundary tests for environment ARM document validation."""

from general_ludd.infra.azure_containerapp_environment_document import (
    validated_environment_profiles,
)


def test_document_module_exposes_the_profile_validation_boundary() -> None:
    """Keep untrusted ARM parsing independently importable from plan auditing."""
    assert callable(validated_environment_profiles)
