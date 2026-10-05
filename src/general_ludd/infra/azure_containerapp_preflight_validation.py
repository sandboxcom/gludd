"""Validate bounded Azure path segments and scalar provider evidence."""

from __future__ import annotations

import math
import re
import uuid

from general_ludd.infra.azure_containerapp_preflight_types import (
    AzureContainerAppPreflightError,
)

_LOCATION_PATTERN = re.compile(r"[a-z][a-z0-9]{1,31}")
_RESOURCE_GROUP_PATTERN = re.compile(r"(?=.{1,90}\Z)[A-Za-z0-9_().-]+(?<!\.)")
_RESOURCE_NAME_PATTERN = re.compile(r"(?=.{1,64}\Z)[A-Za-z0-9_.-]+")
_MAX_PROVIDER_NAME = 200
_MAX_COUNT = 1_000_000


def _validated_path_name(value: str, label: str, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be a safe Azure resource name")


def validate_path_inputs(
    subscription_id: str,
    resource_group: str,
    environment_name: str,
    workload_profile_name: str,
    location: str,
) -> None:
    """Validate every user-controlled ARM path segment before authentication."""
    try:
        canonical_subscription = str(uuid.UUID(subscription_id))
    except (ValueError, AttributeError):
        canonical_subscription = ""
    if subscription_id != canonical_subscription:
        raise ValueError("subscription_id must be a canonical UUID")
    _validated_path_name(resource_group, "resource_group", _RESOURCE_GROUP_PATTERN)
    _validated_path_name(environment_name, "environment_name", _RESOURCE_NAME_PATTERN)
    _validated_path_name(
        workload_profile_name,
        "workload_profile_name",
        _RESOURCE_NAME_PATTERN,
    )
    if not isinstance(location, str) or _LOCATION_PATTERN.fullmatch(location) is None:
        raise ValueError("location must be a lowercase Azure region identifier")


def provider_name(value: object, label: str) -> str:
    """Return one bounded printable provider label."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_PROVIDER_NAME
        or any(ord(character) < 32 for character in value)
    ):
        raise AzureContainerAppPreflightError(
            f"{label} response contains an invalid name"
        )
    return value


def quota_number(value: object) -> float:
    """Return one finite, non-negative quota number."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise AzureContainerAppPreflightError(
            "usage response contains an invalid quota value"
        )
    return float(value)


def count(value: object) -> int:
    """Return one bounded, non-negative provider count."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_COUNT
    ):
        raise ValueError
    return value


__all__ = ("count", "provider_name", "quota_number", "validate_path_inputs")
