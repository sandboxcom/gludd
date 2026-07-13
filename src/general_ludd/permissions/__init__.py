"""Tool permission scoping for the agent framework (AG.4)."""

from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    PermissionEvaluator,
    ToolAction,
    ToolPermission,
    ToolPermissionSpec,
)

__all__ = [
    "CapabilityLattice",
    "PermissionEvaluator",
    "ToolAction",
    "ToolPermission",
    "ToolPermissionSpec",
]
