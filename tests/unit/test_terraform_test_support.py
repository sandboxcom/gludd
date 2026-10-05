"""Tests for the shared Terraform external-dependency test boundary."""

from __future__ import annotations

import json

import pytest

from tests.terraform_test_support import (
    is_known_external_terraform_provider_failure,
    skip_external_terraform_dependency,
)


def test_external_dependency_skip_preserves_a_bounded_reason() -> None:
    reason = "provider registry unavailable: " + ("detail " * 200)

    with pytest.raises(pytest.skip.Exception) as exc_info:
        skip_external_terraform_dependency(reason)

    rendered = str(exc_info.value)
    assert rendered.startswith("provider registry unavailable:")
    assert len(rendered) <= 512


def test_external_dependency_skip_rejects_an_empty_reason() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        skip_external_terraform_dependency("   ")


@pytest.mark.parametrize(
    ("data_source", "attribute"),
    [
        ("runpod_endpoint_jobs", "jobs"),
        ("runpod_endpoint_workers", "workers"),
    ],
)
def test_runpod_schema_conversion_failure_is_external(
    data_source: str,
    attribute: str,
) -> None:
    diagnostic = f"""
    Failed to obtain provider schema: Could not load the schema for provider
    registry.terraform.io/runpod/runpod: Error converting data source schema:
    The schema for the data source "{data_source}" couldn't be converted into a
    usable type. AttributeName("{attribute}"): must have Required, Optional,
    or Computed set.
    """

    assert is_known_external_terraform_provider_failure(
        provider_family="runpod",
        stdout=json.dumps({"diagnostics": [{"detail": diagnostic}]}),
        stderr="",
    )


def test_azure_provider_registry_timeout_is_external() -> None:
    diagnostic = """
    Error: Failed to query available provider packages
    Could not retrieve the list of available versions for provider azure/azapi:
    could not connect to registry.terraform.io: failed to request discovery
    document: GET https://registry.terraform.io/.well-known/terraform.json
    giving up after 4 attempt(s): context deadline exceeded
    """

    assert is_known_external_terraform_provider_failure(
        provider_family="azure",
        stdout=diagnostic,
        stderr="",
    )


@pytest.mark.parametrize(
    ("provider_family", "diagnostic"),
    [
        ("aws", "runpod_endpoint_jobs AttributeName(\"jobs\")"),
        ("runpod", "Failed to obtain provider schema"),
        (
            "runpod",
            "registry.terraform.io/runpod/runpod runpod_endpoint_jobs "
            "AttributeName(\"other\"): must have Required, Optional, or "
            "Computed set.",
        ),
        (
            "azure",
            "Failed to query available provider packages for azure/azapi: "
            "the selected version does not match the configuration",
        ),
        (
            "azure",
            "could not connect to registry.terraform.io for hashicorp/aws: "
            "context deadline exceeded",
        ),
    ],
)
def test_unrelated_or_incomplete_provider_failure_is_not_external(
    provider_family: str,
    diagnostic: str,
) -> None:
    assert not is_known_external_terraform_provider_failure(
        provider_family=provider_family,
        stdout=diagnostic,
        stderr="",
    )
