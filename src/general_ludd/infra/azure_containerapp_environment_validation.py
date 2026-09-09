"""Fail-closed ARM and Terraform evidence validation for Azure environments."""

from __future__ import annotations

from collections.abc import Mapping

from general_ludd.infra.azure_containerapp_environment_document import (
    document_mapping as _mapping,
)
from general_ludd.infra.azure_containerapp_environment_document import (
    string_document_member as _string_member,
)
from general_ludd.infra.azure_containerapp_environment_document import (
    validated_environment_profiles,
)
from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    CHANGE_FIELDS as _CHANGE_FIELDS,
)
from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    EMPTY_PROVIDER_FIELDS as _EMPTY_PROVIDER_FIELDS,
)
from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    ENVIRONMENT_API_TYPE as _ENVIRONMENT_API_TYPE,
)
from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    PROVIDER_AFTER_FIELDS as _PROVIDER_AFTER_FIELDS,
)
from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    PROVIDER_BOOLEAN_DEFAULTS as _PROVIDER_BOOLEAN_DEFAULTS,
)
from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    RESOURCE_ADDRESS as _RESOURCE_ADDRESS,
)
from general_ludd.infra.azure_containerapp_environment_plan_contract import (
    RESPONSE_EXPORT_VALUES as _RESPONSE_EXPORT_VALUES,
)
from general_ludd.infra.azure_containerapp_environment_types import (
    AzureEnvironmentLifecycleError,
    AzureEnvironmentLifecyclePolicy,
)


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


def _validate_resource_identity(
    value: object,
    policy: AzureEnvironmentLifecyclePolicy,
) -> None:
    if value is None:
        return
    identity = _mapping(value)
    if (
        set(identity) != {"id", "type"}
        or _string_member(identity, "id").casefold()
        != policy.environment_id.casefold()
        or identity.get("type") is not None
    ):
        raise ValueError


def _validate_plan_metadata(
    change: Mapping[str, object],
    policy: AzureEnvironmentLifecyclePolicy,
) -> None:
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
    _validate_resource_identity(change.get("before_identity"), policy)
    _validate_resource_identity(change.get("after_identity"), policy)


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
        _validate_plan_metadata(change, policy)
        return tuple(actions) != ("no-op",)
    except (KeyError, TypeError, ValueError):
        raise AzureEnvironmentLifecycleError("plan") from None


__all__ = ["audit_environment_plan", "validated_environment_profiles"]
