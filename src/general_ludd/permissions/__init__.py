"""Tool permission scoping for the agent framework (AG.4)."""

from general_ludd.permissions.infra_access import (
    InfraAccessPolicy,
    load_infra_access_policy,
)
from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    PermissionEvaluator,
    ToolAction,
    ToolPermission,
    ToolPermissionSpec,
)

__all__ = [
    "CapabilityLattice",
    "InfraAccessPolicy",
    "PermissionEvaluator",
    "ToolAction",
    "ToolPermission",
    "ToolPermissionSpec",
    "load_infra_access_policy",
]
