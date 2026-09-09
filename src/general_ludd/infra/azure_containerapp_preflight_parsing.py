"""Strict parsing of named-environment, usage, and profile-state evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from general_ludd.infra.azure_containerapp_gpu import (
    T4_PROFILE,
    AzureContainerAppGPUProfile,
)
from general_ludd.infra.azure_containerapp_preflight_types import (
    AzureContainerAppEvidenceError,
    AzureContainerAppPreflightError,
    ContainerAppUsage,
    _ConfiguredWorkloadProfile,
    _EnvironmentEvidence,
    _WorkloadProfileState,
)
from general_ludd.infra.azure_containerapp_preflight_validation import (
    count,
    provider_name,
    quota_number,
    validate_path_inputs,
)

_MAX_RECORDS = 512


def _collection(payload: object, label: str) -> Sequence[object]:
    if not isinstance(payload, Mapping):
        raise AzureContainerAppPreflightError(f"{label} response must be an object")
    if payload.get("nextLink"):
        raise AzureContainerAppPreflightError(
            f"paginated {label} response is not accepted"
        )
    values = payload.get("value")
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or len(values) > _MAX_RECORDS
    ):
        raise AzureContainerAppPreflightError(
            f"{label} response must contain a bounded value list"
        )
    return values


def _normalized_location(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _parse_profile_record(value: object) -> _ConfiguredWorkloadProfile:
    if not isinstance(value, Mapping):
        raise ValueError
    name = provider_name(value.get("name"), "workload profile")
    profile_type = provider_name(value.get("workloadProfileType"), "workload profile")
    raw_minimum = value.get("minimumCount", 0)
    raw_maximum = value.get("maximumCount")
    serverless = profile_type == "Consumption" or profile_type.startswith(
        "Consumption-GPU-"
    )
    minimum = 0 if raw_minimum is None and serverless else count(raw_minimum)
    maximum = (
        None if raw_maximum is None and serverless else count(raw_maximum)
    )
    if maximum is not None and minimum > maximum:
        raise ValueError
    return _ConfiguredWorkloadProfile(name, profile_type, minimum, maximum)


def parse_environment(
    payload: object,
    *,
    expected_resource_id: str,
    expected_environment_name: str,
    expected_location: str,
    expected_profile_name: str,
    expected_profile_type: str,
) -> _EnvironmentEvidence:
    """Bind a named environment response to exact requested identity and profile."""
    if not isinstance(payload, Mapping):
        raise AzureContainerAppEvidenceError(
            "environment_response_invalid",
            "Azure Container Apps environment response is invalid",
        )
    resource_id = payload.get("id")
    name = payload.get("name")
    resource_type = payload.get("type")
    if (
        not isinstance(resource_id, str)
        or resource_id.casefold() != expected_resource_id.casefold()
        or not isinstance(name, str)
        or name.casefold() != expected_environment_name.casefold()
        or not isinstance(resource_type, str)
        or resource_type.casefold() != "microsoft.app/managedenvironments"
    ):
        raise AzureContainerAppEvidenceError(
            "environment_identity_mismatch",
            "Azure Container Apps environment identity does not match",
        )
    response_location = payload.get("location")
    if (
        not isinstance(response_location, str)
        or _normalized_location(response_location) != expected_location
    ):
        raise AzureContainerAppEvidenceError(
            "environment_location_mismatch",
            "Azure Container Apps environment location does not match",
        )
    properties = payload.get("properties")
    if not isinstance(properties, Mapping):
        raise AzureContainerAppEvidenceError(
            "environment_response_invalid",
            "Azure Container Apps environment response is invalid",
        )
    if properties.get("provisioningState") != "Succeeded":
        raise AzureContainerAppEvidenceError(
            "environment_not_ready",
            "Azure Container Apps environment is not ready",
        )
    raw_profiles = properties.get("workloadProfiles")
    if (
        not isinstance(raw_profiles, Sequence)
        or isinstance(raw_profiles, (str, bytes))
        or len(raw_profiles) > _MAX_RECORDS
    ):
        raise AzureContainerAppEvidenceError(
            "environment_response_invalid",
            "Azure Container Apps environment workload profile response is invalid",
        )
    try:
        profiles = tuple(_parse_profile_record(value) for value in raw_profiles)
    except (AzureContainerAppPreflightError, ValueError):
        raise AzureContainerAppEvidenceError(
            "environment_response_invalid",
            "Azure Container Apps environment workload profile response is invalid",
        ) from None
    configured = next(
        (profile for profile in profiles if profile.name == expected_profile_name),
        None,
    )
    if configured is None:
        raise AzureContainerAppEvidenceError(
            "workload_profile_missing",
            "Azure Container Apps workload profile is missing",
        )
    if configured.workload_profile_type != expected_profile_type:
        raise AzureContainerAppEvidenceError(
            "workload_profile_type_mismatch",
            "Azure Container Apps workload profile type does not match",
        )
    if configured.maximum_count is not None and configured.maximum_count < 1:
        raise AzureContainerAppEvidenceError(
            "workload_profile_disabled",
            "Azure Container Apps workload profile is disabled",
        )
    return _EnvironmentEvidence(
        configured_profile=configured,
        available_profile_types=tuple(
            sorted({profile.workload_profile_type for profile in profiles})
        ),
    )


def parse_usages(payload: object) -> tuple[ContainerAppUsage, ...]:
    """Parse a bounded non-paginated environment usage response."""
    usages: list[ContainerAppUsage] = []
    for value in _collection(payload, "usage"):
        if not isinstance(value, Mapping):
            raise AzureContainerAppPreflightError(
                "usage response contains an invalid record"
            )
        name_value = value.get("name")
        if not isinstance(name_value, Mapping):
            raise AzureContainerAppPreflightError(
                "usage response contains an invalid name"
            )
        raw_name = name_value.get("value") or name_value.get("localizedValue")
        name = provider_name(raw_name, "usage")
        unit = provider_name(value.get("unit", "Count"), "usage")
        current = quota_number(value.get("currentValue"))
        limit = quota_number(value.get("limit"))
        if current > limit:
            raise AzureContainerAppPreflightError(
                "usage response contains an invalid quota value"
            )
        usages.append(ContainerAppUsage(name, current, limit, unit))
    return tuple(usages)


def quota_for_profile(
    profile: AzureContainerAppGPUProfile,
    usages: Sequence[ContainerAppUsage],
) -> ContainerAppUsage | None:
    """Select the provider quota record matching one supported GPU profile."""
    markers = ("t4", "nc8as") if profile == T4_PROFILE else ("a100", "nc24")
    for usage in usages:
        normalized = re.sub(r"[^a-z0-9]", "", usage.name.casefold())
        if "consumption" in normalized and any(
            marker in normalized for marker in markers
        ):
            return usage
    return None


def _parse_state_record(value: object) -> _WorkloadProfileState:
    if not isinstance(value, Mapping):
        raise ValueError
    name = provider_name(value.get("name"), "workload profile state")
    properties = value.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError
    current = count(properties.get("currentCount"))
    maximum = count(properties.get("maximumCount"))
    minimum = count(properties.get("minimumCount"))
    if minimum > maximum or current > maximum:
        raise ValueError
    return _WorkloadProfileState(name, current, maximum, minimum)


def parse_workload_profile_state(
    payload: object,
    expected_profile_name: str,
) -> tuple[_WorkloadProfileState, int]:
    """Return one exact workload-profile state and the observed record count."""
    try:
        raw_states = _collection(payload, "workload profile state")
        states = tuple(_parse_state_record(value) for value in raw_states)
    except AzureContainerAppPreflightError as exc:
        raise AzureContainerAppEvidenceError(
            "workload_profile_state_invalid", str(exc)
        ) from None
    except ValueError:
        raise AzureContainerAppEvidenceError(
            "workload_profile_state_invalid",
            "Azure Container Apps workload profile has invalid state",
        ) from None
    matched = next(
        (state for state in states if state.name == expected_profile_name),
        None,
    )
    if matched is None:
        raise AzureContainerAppEvidenceError(
            "workload_profile_state_missing",
            "Azure Container Apps workload profile state is missing",
        )
    return matched, len(states)


__all__ = (
    "count",
    "parse_environment",
    "parse_usages",
    "parse_workload_profile_state",
    "provider_name",
    "quota_for_profile",
    "quota_number",
    "validate_path_inputs",
)
