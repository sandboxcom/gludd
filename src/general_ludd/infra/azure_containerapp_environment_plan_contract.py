"""Immutable provider-schema contract for Azure environment plan audits."""

from __future__ import annotations

ENVIRONMENT_API_TYPE = "Microsoft.App/managedEnvironments@2025-07-01"
ENVIRONMENT_RESOURCE_TYPE = "Microsoft.App/managedEnvironments"
RESOURCE_ADDRESS = "module.environment.azapi_resource.managed_environment"
PLATFORM_CONSUMPTION_PROFILE = ("Consumption", "Consumption")
RESPONSE_EXPORT_VALUES = (
    "id",
    "name",
    "properties.provisioningState",
    "properties.workloadProfiles",
    "tags",
)
PROVIDER_AFTER_FIELDS = frozenset(
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
EMPTY_PROVIDER_FIELDS = frozenset(
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
PROVIDER_BOOLEAN_DEFAULTS = {
    "ignore_casing": False,
    "ignore_missing_property": True,
    "ignore_null_property": False,
    "schema_validation_enabled": True,
}
CHANGE_FIELDS = frozenset(
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

__all__ = (
    "CHANGE_FIELDS",
    "EMPTY_PROVIDER_FIELDS",
    "ENVIRONMENT_API_TYPE",
    "ENVIRONMENT_RESOURCE_TYPE",
    "PLATFORM_CONSUMPTION_PROFILE",
    "PROVIDER_AFTER_FIELDS",
    "PROVIDER_BOOLEAN_DEFAULTS",
    "RESOURCE_ADDRESS",
    "RESPONSE_EXPORT_VALUES",
)
