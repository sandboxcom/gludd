"""Azure IAM validator — re-exports from ``general_ludd.azure.rbac_validator``
and adds the Portal / REST-API upload-format generator.
"""

from __future__ import annotations

from typing import Any

from general_ludd.azure.rbac_validator import (
    AZURE_BUILTIN_ROLES,
    AZURE_RESOURCE_PROVIDERS,
    PROVIDER_OPERATIONS,
    SECURITY_CRITICAL_OPS,
    check_security_critical_denials,
    generate_role_definition,
    validate_action_string,
    validate_against_azure_schema,
)


def azure_generate_portal_json(role_cli_json: dict[str, Any]) -> dict[str, Any]:
    """Convert an Azure CLI-formatted role definition (PascalCase keys) into
    the REST API / Portal upload format (``properties`` wrapper with camelCase
    keys).

    The CLI convention uses ``Name``, ``Actions``, ``NotActions``,
    ``AssignableScopes``, etc.  The Portal REST API nests everything under a
    ``properties`` key and uses ``roleName``, ``permissions``, etc.
    """
    props: dict[str, Any] = {
        "roleName": role_cli_json.get("Name", ""),
        "description": role_cli_json.get("Description", ""),
        "assignableScopes": role_cli_json.get("AssignableScopes", []),
        "permissions": [
            {
                "actions": role_cli_json.get("Actions", []),
                "notActions": role_cli_json.get("NotActions", []),
                "dataActions": role_cli_json.get("DataActions", []),
                "notDataActions": role_cli_json.get("NotDataActions", []),
            }
        ],
    }
    return {"properties": props}


__all__ = [
    "AZURE_BUILTIN_ROLES",
    "AZURE_RESOURCE_PROVIDERS",
    "PROVIDER_OPERATIONS",
    "SECURITY_CRITICAL_OPS",
    "azure_generate_portal_json",
    "check_security_critical_denials",
    "generate_role_definition",
    "validate_action_string",
    "validate_against_azure_schema",
]
