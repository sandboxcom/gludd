"""Regression tests for extracted Terraform collection metadata parsing."""

from __future__ import annotations

from general_ludd.collections import terraform_metadata
from general_ludd.collections.importer import (
    _is_floating_version,
    _iter_provider_entries,
    _parse_required_providers,
    _parse_tfvars_keys,
    _parse_variable_names,
)


def test_collection_importer_reexports_canonical_metadata_parsers() -> None:
    """Existing collection integrations retain their parser identities."""
    assert _is_floating_version is terraform_metadata.is_floating_version
    assert _iter_provider_entries is terraform_metadata.iter_provider_entries
    assert _parse_required_providers is terraform_metadata.parse_required_providers
    assert _parse_tfvars_keys is terraform_metadata.parse_tfvars_keys
    assert _parse_variable_names is terraform_metadata.parse_variable_names
