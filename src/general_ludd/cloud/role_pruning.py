"""Resource-aware pruning for generated cloud IAM role templates."""

from __future__ import annotations

from typing import Any


def _azure_action_matches(action: str, resource_types: set[str]) -> bool:
    action_lower = action.lower()
    for resource_type in resource_types:
        if resource_type in action_lower or resource_type == "*" or "/*" in action:
            return True
        if resource_type in (
            "compute",
            "network",
            "storage",
            "containerregistry",
            "containerservice",
            "app",
            "operationalinsights",
            "insights",
            "authorization",
            "managedidentity",
            "keyvault",
        ) and action_lower.startswith(f"microsoft.{resource_type}/"):
            return True
    return False


def _aws_action_matches(action: str, resource_types: set[str]) -> bool:
    action_lower = action.lower()
    if any(resource_type in action_lower or resource_type == "*" for resource_type in resource_types):
        return True
    service_prefix = action.split(":")[0].lower() if ":" in action else ""
    return service_prefix in resource_types


def prune_by_resource_types(
    provider: str,
    role_def: dict[str, Any],
    resource_types: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Best-effort prune a role to actions matching requested resources."""
    warnings: list[str] = []
    if not resource_types:
        return role_def, warnings

    keep = {resource_type.lower() for resource_type in resource_types}
    if provider == "azure":
        actions = role_def.get("Actions", [])
        filtered = [action for action in actions if _azure_action_matches(action, keep)]
        removed = len(actions) - len(filtered)
        if removed > 0:
            warnings.append(
                f"Pruned {removed} Azure action(s) not matching resource types {sorted(keep)}"
            )
        role_def["Actions"] = filtered
    elif provider == "aws":
        for statement in role_def.get("policy", []):
            if not isinstance(statement, dict):
                continue
            actions = statement.get("Action", [])
            if isinstance(actions, list):
                filtered = [action for action in actions if _aws_action_matches(action, keep)]
                removed = len(actions) - len(filtered)
                if removed > 0:
                    warnings.append(
                        f"Pruned {removed} AWS action(s) not matching resource types {sorted(keep)}"
                    )
                statement["Action"] = filtered
    elif provider == "gcp":
        warnings.append("GCP resource-type pruning not supported — using full role template")
    return role_def, warnings


__all__ = ["prune_by_resource_types"]
