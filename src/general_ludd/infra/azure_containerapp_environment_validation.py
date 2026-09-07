"""Fail-closed ARM and Terraform evidence validation for Azure environments."""

from __future__ import annotations

from collections.abc import Mapping

from general_ludd.infra.azure_containerapp_environment_types import (
    _LIFECYCLE_VERSION,
    _MANAGED_BY,
    _PROFILE_PAIRS,
    AzureEnvironmentLifecycleError,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
)

_ENVIRONMENT_API_TYPE = "Microsoft.App/managedEnvironments@2025-07-01"
_ENVIRONMENT_RESOURCE_TYPE = "Microsoft.App/managedEnvironments"
_RESOURCE_ADDRESS = "module.environment.azapi_resource.managed_environment"
_PLATFORM_CONSUMPTION_PROFILE = ("Consumption", "Consumption")
_RESPONSE_EXPORT_VALUES = (
    "id",
    "name",
    "properties.provisioningState",
    "properties.workloadProfiles",
    "tags",
)
_PROVIDER_AFTER_FIELDS = frozenset(
    {
        "body",
        "create_headers",
        "create_query_parameters",
        "delete_headers",
        "delete_query_parameters",
        "id",
        "identity",
        "ignore_body_changes",
        "ignore_casing",
        "ignore_missing_property",
        "ignore_null_property",
        "ignore_other_items_in_list",
        "list_unique_id_property",
        "location",
        "locks",
        "name",
        "output",
        "parent_id",
        "read_headers",
        "read_query_parameters",
        "replace_triggers_external_values",
        "replace_triggers_refs",
        "response_export_values",
        "retry",
        "schema_validation_enabled",
        "sensitive_body",
        "sensitive_body_version",
        "tags",
        "timeouts",
        "type",
        "update_headers",
        "update_query_parameters",
    }
)
_EMPTY_PROVIDER_FIELDS = frozenset(
    {
        "create_headers",
        "create_query_parameters",
        "delete_headers",
        "delete_query_parameters",
        "identity",
        "ignore_body_changes",
        "ignore_other_items_in_list",
        "list_unique_id_property",
        "locks",
        "read_headers",
        "read_query_parameters",
        "replace_triggers_external_values",
        "replace_triggers_refs",
        "sensitive_body",
        "sensitive_body_version",
        "update_headers",
        "update_query_parameters",
    }
)
_PROVIDER_BOOLEAN_DEFAULTS = {
    "ignore_casing": False,
    "ignore_missing_property": True,
    "ignore_null_property": False,
    "schema_validation_enabled": True,
}
_CHANGE_FIELDS = frozenset(
    {
        "actions",
        "after",
        "after_sensitive",
        "after_unknown",
        "before",
        "before_sensitive",
        "generated_config",
        "importing",
        "replace_paths",
    }
)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError
    return value


def _string_member(value: Mapping[str, object], key: str) -> str:
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
        profile = _mapping(raw_profile)
        pair = (
            _string_member(profile, "name"),
            _string_member(profile, "workloadProfileType"),
        )
        if pair == _PLATFORM_CONSUMPTION_PROFILE:
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
        root = _mapping(document)
        identity = (
            _string_member(root, "id").casefold(),
            _string_member(root, "name"),
            _string_member(root, "type").casefold(),
            _string_member(root, "location").casefold(),
        )
        expected_identity = (
            policy.environment_id.casefold(),
            policy.environment_name,
            _ENVIRONMENT_RESOURCE_TYPE.casefold(),
            policy.location.casefold(),
        )
        if identity != expected_identity:
            raise ValueError
        tags = _mapping(root.get("tags"))
        required_tags = (
            policy.ownership_tags
            if require_current_tags
            else {
                "gludd-managed-by": _MANAGED_BY,
                "gludd-lifecycle-version": _LIFECYCLE_VERSION,
                "gludd-owner-digest": policy.owner_digest,
            }
        )
        if any(tags.get(key) != value for key, value in required_tags.items()):
            raise ValueError
        properties = _mapping(root.get("properties"))
        if _string_member(properties, "provisioningState") != "Succeeded":
            raise ValueError
        profiles = _profile_tuple(properties.get("workloadProfiles"))
        if require_desired_profiles and not set(policy.profiles).issubset(profiles):
            raise ValueError
        return profiles
    except (KeyError, TypeError, ValueError):
        raise AzureEnvironmentLifecycleError("ownership") from None


def _expected_plan_after(policy: AzureEnvironmentLifecyclePolicy) -> dict[str, object]:
    return {
        "type": _ENVIRONMENT_API_TYPE,
        "name": policy.environment_name,
        "parent_id": policy.resource_group_id,
        "location": policy.location,
        "tags": policy.ownership_tags,
        "body": {
            "properties": {
                "workloadProfiles": [
                    {
                        "name": profile.profile_name,
                        "workloadProfileType": profile.workload_profile_type,
                    }
                    for profile in policy.profiles
                ]
            }
        },
    }


def _empty_provider_value(value: object) -> bool:
    return value is None or value == {} or value == []


def _null_mapping(value: object) -> bool:
    return value is None or (
        isinstance(value, Mapping) and all(item is None for item in value.values())
    )


def _validate_plan_after(value: object, policy: AzureEnvironmentLifecyclePolicy) -> None:
    after = _mapping(value)
    expected = _expected_plan_after(policy)
    if set(after) - _PROVIDER_AFTER_FIELDS:
        raise ValueError
    if any(after.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError
    if any(
        key in after and not _empty_provider_value(after[key])
        for key in _EMPTY_PROVIDER_FIELDS
    ):
        raise ValueError
    if any(
        key in after and after[key] is not expected_value
        for key, expected_value in _PROVIDER_BOOLEAN_DEFAULTS.items()
    ):
        raise ValueError
    resource_id = after.get("id")
    if resource_id is not None and (
        not isinstance(resource_id, str)
        or resource_id.casefold() != policy.environment_id.casefold()
    ):
        raise ValueError
    response_exports = after.get("response_export_values")
    if response_exports is not None and (
        not isinstance(response_exports, list)
        or tuple(response_exports) != _RESPONSE_EXPORT_VALUES
    ):
        raise ValueError
    if not _null_mapping(after.get("retry")) or not _null_mapping(after.get("timeouts")):
        raise ValueError


def _validate_plan_metadata(change: Mapping[str, object]) -> None:
    if set(change) - _CHANGE_FIELDS:
        raise ValueError
    after_unknown = change.get("after_unknown")
    if after_unknown not in ({}, None):
        unknown = _mapping(after_unknown)
        for name, item in unknown.items():
            if name in {"id", "output"}:
                if item is not True:
                    raise ValueError
            elif not _false_or_empty_metadata(item):
                raise ValueError
    if any(
        not _false_or_empty_metadata(change.get(field))
        for field in ("after_sensitive", "before_sensitive")
    ):
        raise ValueError
    if change.get("replace_paths") not in (None, []):
        raise ValueError
    if change.get("importing") is not None or change.get("generated_config") is not None:
        raise ValueError


def _false_or_empty_metadata(value: object) -> bool:
    """Accept provider metadata shapes only when no nested flag is true."""
    if value is None or value is False:
        return True
    if isinstance(value, Mapping):
        return all(_false_or_empty_metadata(item) for item in value.values())
    if isinstance(value, list):
        return all(_false_or_empty_metadata(item) for item in value)
    return False


def audit_environment_plan(
    plan: object,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    existed_before: bool,
) -> bool:
    """Require exactly one bounded environment create, update, or no-op."""
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be a Terraform plan object")
    if not isinstance(policy, AzureEnvironmentLifecyclePolicy):
        raise ValueError("policy must be AzureEnvironmentLifecyclePolicy")
    if not isinstance(existed_before, bool):
        raise ValueError("existed_before must be boolean")
    try:
        changes = plan.get("resource_changes")
        if not isinstance(changes, list) or len(changes) != 1:
            raise ValueError
        resource = _mapping(changes[0])
        identity = (
            resource.get("address"),
            resource.get("mode"),
            resource.get("type"),
            resource.get("name"),
            resource.get("provider_name"),
        )
        if identity != (
            _RESOURCE_ADDRESS,
            "managed",
            "azapi_resource",
            "managed_environment",
            "registry.terraform.io/azure/azapi",
        ):
            raise ValueError
        change = _mapping(resource.get("change"))
        actions = change.get("actions")
        allowed = ({("no-op",), ("update",)} if existed_before else {("create",)})
        if not isinstance(actions, list) or tuple(actions) not in allowed:
            raise ValueError
        if not existed_before and change.get("before") is not None:
            raise ValueError
        _validate_plan_after(change.get("after"), policy)
        _validate_plan_metadata(change)
        return tuple(actions) != ("no-op",)
    except (KeyError, TypeError, ValueError):
        raise AzureEnvironmentLifecycleError("plan") from None


__all__ = ["audit_environment_plan", "validated_environment_profiles"]
