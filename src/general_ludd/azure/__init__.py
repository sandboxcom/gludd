"""Azure expert package — exposes role entry-points, contracts, and
domain advisors for RBAC, IAM, networking, ACR, Container Apps, Log Analytics,
Resource Graph, and cost optimization.

The knowledge base and role functions live in :mod:`general_ludd.azure.core`.
"""

from __future__ import annotations

from general_ludd.azure.contracts import (
    AcrConfig,
    AzureRbacRole,
    ContainerAppDeployConfig,
    IamAssignment,
    LogAnalyticsQuery,
    NetworkDesign,
    PricingResult,
)
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
from general_ludd.azure.iam_advisor import (
    PERSONA_ROLE_MAP,
    audit_existing_assignments,
    recommend_roles_for_persona,
)
from general_ludd.azure.network_designer import (
    DEFAULT_CIDR,
    design_vnet,
    generate_nsg_rules,
)
from general_ludd.azure.rbac_validator import (
    AZURE_BUILTIN_ROLES,
    AZURE_RESOURCE_PROVIDERS,
    FORBIDDEN_SUFFIX_PATTERNS,
    KNOWN_RBAC_ACTIONS,
    PROVIDER_OPERATIONS,
    SECRET_ACTION_PATTERNS,
    check_security_critical_denials,
    generate_role_definition,
    validate_action_string,
    validate_against_azure_schema,
)


def __dir__() -> list[str]:
    """Expose only the documented public Azure package surface."""
    return sorted(__all__)


__all__ = [
    "AZURE_BUILTIN_ROLES",
    "AZURE_EXPERT_ROLES",
    "AZURE_RESOURCE_PROVIDERS",
    "DEFAULT_CIDR",
    "FORBIDDEN_SUFFIX_PATTERNS",
    "KNOWN_RBAC_ACTIONS",
    "PERSONA_ROLE_MAP",
    "PROVIDER_OPERATIONS",
    "SECRET_ACTION_PATTERNS",
    "AcrConfig",
    "AzureRbacRole",
    "ContainerAppDeployConfig",
    "IamAssignment",
    "LogAnalyticsQuery",
    "NetworkDesign",
    "PricingResult",
    "acr_registry_config",
    "audit_existing_assignments",
    "audit_iam_assignments",
    "check_security_critical_denials",
    "container_app_config",
    "design_azure_network",
    "design_vnet",
    "generate_nsg_rules",
    "generate_role_definition",
    "inventory_resources",
    "optimize_cost",
    "query_log_analytics",
    "recommend_roles_for_persona",
    "validate_action_string",
    "validate_against_azure_schema",
    "validate_rbac_role_definition",
]
