"""Shared boundaries for Terraform tests with external provider dependencies."""

from __future__ import annotations

import json
from typing import NoReturn

import pytest

_MAX_SKIP_REASON_CHARS = 512

_RUNPOD_PROVIDER_SCHEMA_FAILURE_MARKERS = (
    "Failed to obtain provider schema",
    "registry.terraform.io/runpod/runpod",
)
_RUNPOD_INVALID_DATA_SOURCE_SCHEMAS = (
    ("runpod_endpoint_jobs", "jobs"),
    ("runpod_endpoint_workers", "workers"),
)
_PROVIDER_REGISTRY_PATHS = {
    "azure": ("azure/azapi",),
}
_PROVIDER_REGISTRY_UNAVAILABLE_MARKERS = (
    "failed to query available provider packages",
    "could not connect to registry.terraform.io",
    "registry.terraform.io/.well-known/terraform.json",
)
_PROVIDER_REGISTRY_TIMEOUT_MARKERS = (
    "context deadline exceeded",
    "client.timeout exceeded while awaiting headers",
)


def _terraform_diagnostic_text(stdout: str, stderr: str) -> str:
    """Extract provider diagnostic strings from Terraform JSON output."""
    messages = [stderr]
    try:
        payload: object = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        messages.append(stdout)
    else:
        if isinstance(payload, dict):
            diagnostics = payload.get("diagnostics")
            if isinstance(diagnostics, list):
                for diagnostic in diagnostics:
                    if not isinstance(diagnostic, dict):
                        continue
                    for field in ("summary", "detail"):
                        value = diagnostic.get(field)
                        if isinstance(value, str):
                            messages.append(value)
            else:
                messages.append(stdout)
        else:
            messages.append(stdout)
    return " ".join("\n".join(messages).split())


def is_known_external_terraform_provider_failure(
    *,
    provider_family: str,
    stdout: str,
    stderr: str,
) -> bool:
    """Return whether diagnostics match an exact external provider boundary.

    Generic init/validation failures remain hard failures. The accepted
    signatures are a provider-specific registry timeout and the RunPod
    provider's invalid ``runpod_endpoint_jobs.jobs`` or
    ``runpod_endpoint_workers.workers`` data-source schema.
    """
    normalized_family = provider_family.strip().lower()
    diagnostic = _terraform_diagnostic_text(stdout, stderr)
    normalized_diagnostic = diagnostic.lower()
    registry_paths = _PROVIDER_REGISTRY_PATHS.get(normalized_family, ())
    if (
        registry_paths
        and any(path in normalized_diagnostic for path in registry_paths)
        and all(
            marker in normalized_diagnostic
            for marker in _PROVIDER_REGISTRY_UNAVAILABLE_MARKERS
        )
        and any(
            marker in normalized_diagnostic
            for marker in _PROVIDER_REGISTRY_TIMEOUT_MARKERS
        )
    ):
        return True

    if normalized_family != "runpod":
        return False
    if not all(
        marker in diagnostic
        for marker in _RUNPOD_PROVIDER_SCHEMA_FAILURE_MARKERS
    ):
        return False
    return any(
        f'data source "{data_source}"' in diagnostic
        and (
            f'AttributeName("{attribute}"): must have Required, Optional, '
            "or Computed set"
        ) in diagnostic
        for data_source, attribute in _RUNPOD_INVALID_DATA_SOURCE_SCHEMAS
    )


def skip_external_terraform_dependency(reason: str) -> NoReturn:
    """Skip when a provider registry/cache/credential boundary is unavailable.

    Keeping this in one helper makes the conditional environmental boundary
    auditable and prevents every Terraform suite from growing its own skip
    surface. Syntax and formatting checks remain mandatory at each call site.
    """
    normalized = reason.strip()
    if not normalized:
        raise ValueError("Terraform skip reason must be non-empty")
    pytest.skip(normalized[:_MAX_SKIP_REASON_CHARS])
