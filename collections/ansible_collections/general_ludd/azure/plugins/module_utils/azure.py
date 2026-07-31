"""Azure cloud module_util for the azure collection.

Standalone accessor that re-exports the public API from
``src/general_ludd/azure/core.py`` — no logic is duplicated here.

Public surface::

    AZURE_EXPERT_ROLES     tuple of 8 role tokens
    audit_iam_assignments(persona, role_enabled)       -> dict
    validate_rbac_role_definition(file, role_enabled)   -> dict
    design_azure_network(app, region, cidr, enabled)    -> dict
    container_app_config(gpu, model, region, enabled)   -> dict
    acr_registry_config(name, sku, region, enabled)     -> dict
    query_log_analytics(workspace, kql, enabled)        -> dict
    inventory_resources(sub_ids, enabled)               -> dict
    optimize_cost(service, region, gpu, enabled)        -> dict
"""

from __future__ import annotations

from general_ludd.azure.core import (
    AZURE_EXPERT_ROLES,
    acr_registry_config,
    audit_iam_assignments,
    container_app_config,
    design_azure_network,
    inventory_resources,
    optimize_cost,
    query_log_analytics,
    validate_rbac_role_definition,
)

__all__ = [
    "AZURE_EXPERT_ROLES",
    "acr_registry_config",
    "audit_iam_assignments",
    "container_app_config",
    "design_azure_network",
    "inventory_resources",
    "optimize_cost",
    "query_log_analytics",
    "validate_rbac_role_definition",
]
