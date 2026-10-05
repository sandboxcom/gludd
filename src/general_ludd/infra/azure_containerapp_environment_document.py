"""Fail-closed validation of Azure managed-environment ARM documents."""

from __future__ import annotations

from collections.abc import Mapping

from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    ENVIRONMENT_RESOURCE_TYPE,
    PLATFORM_CONSUMPTION_PROFILE,
)
from general_ludd.infra.azure_containerapp_environment_types import (
    _LIFECYCLE_VERSION,
    _MANAGED_BY,
    _PROFILE_PAIRS,
    AzureEnvironmentLifecycleError,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
)


def document_mapping(value: object) -> Mapping[str, object]:
    """Return a mapping or reject the untrusted provider value."""
    if not isinstance(value, Mapping):
        raise ValueError
    return value


def string_document_member(value: Mapping[str, object], key: str) -> str:
    """Return a required string member from an untrusted provider mapping."""
    member = value.get(key)
    if not isinstance(member, str):
        raise ValueError
    return member


def _profile_tuple(value: object) -> tuple[AzureEnvironmentProfile, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= len(_PROFILE_PAIRS) + 1:
        raise ValueError
    profiles: list[AzureEnvironmentProfile] = []
    platform_consumption_seen = False
    for raw_profile in value:
        profile = document_mapping(raw_profile)
        pair = (
            string_document_member(profile, "name"),
            string_document_member(profile, "workloadProfileType"),
        )
        if pair == PLATFORM_CONSUMPTION_PROFILE:
            if platform_consumption_seen:
                raise ValueError
            platform_consumption_seen = True
            continue
        profiles.append(AzureEnvironmentProfile(*pair))
    result = tuple(sorted(profiles))
    if len(result) != len(set(result)):
        raise ValueError
    return result


def validated_environment_profiles(
    document: object,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    require_desired_profiles: bool,
    require_current_tags: bool,
) -> tuple[AzureEnvironmentProfile, ...]:
    """Validate identity, ownership, readiness, and bounded GPU profiles."""
    try:
        root = document_mapping(document)
    except (TypeError, ValueError):
        raise AzureEnvironmentLifecycleError(
            "ownership", reason="response"
        ) from None
    identity_fields = (
        ("id", policy.environment_id.casefold(), "identity_resource_id", True),
        ("name", policy.environment_name, "identity_name", False),
        (
            "type",
            ENVIRONMENT_RESOURCE_TYPE.casefold(),
            "identity_resource_type",
            True,
        ),
        ("location", policy.location.casefold(), "identity_location", True),
    )
    for key, expected, reason, case_insensitive in identity_fields:
        try:
            observed = string_document_member(root, key)
        except (KeyError, TypeError, ValueError):
            raise AzureEnvironmentLifecycleError(
                "ownership", reason=reason
            ) from None
        if key == "location":
            observed = "".join(
                character for character in observed.casefold() if character.isalnum()
            )
        elif case_insensitive:
            observed = observed.casefold()
        if observed != expected:
            raise AzureEnvironmentLifecycleError("ownership", reason=reason)
    try:
        tags = document_mapping(root.get("tags"))
        required_tags = (
            policy.ownership_tags
            if require_current_tags
            else {
                "gludd-managed-by": _MANAGED_BY,
                "gludd-lifecycle-version": _LIFECYCLE_VERSION,
                "gludd-owner-digest": policy.owner_digest,
            }
        )
    except (KeyError, TypeError, ValueError):
        raise AzureEnvironmentLifecycleError("ownership", reason="tags") from None
    if any(tags.get(key) != value for key, value in required_tags.items()):
        raise AzureEnvironmentLifecycleError("ownership", reason="tags")
    try:
        properties = document_mapping(root.get("properties"))
        provisioning_state = string_document_member(properties, "provisioningState")
    except (KeyError, TypeError, ValueError):
        raise AzureEnvironmentLifecycleError(
            "ownership", reason="readiness"
        ) from None
    if provisioning_state != "Succeeded":
        raise AzureEnvironmentLifecycleError("ownership", reason="readiness")
    try:
        profiles = _profile_tuple(properties.get("workloadProfiles"))
    except (KeyError, TypeError, ValueError):
        raise AzureEnvironmentLifecycleError(
            "ownership", reason="profiles"
        ) from None
    if require_desired_profiles and not set(policy.profiles).issubset(profiles):
        raise AzureEnvironmentLifecycleError("ownership", reason="profiles")
    return profiles


__all__ = [
    "document_mapping",
    "string_document_member",
    "validated_environment_profiles",
]
