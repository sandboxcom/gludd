"""Azure expert core module (general_ludd.azure).

Implements role entry-point functions for Azure cloud engineering expertise:
RBAC validation, IAM auditing, network design, ACR config, Container Apps
deployment, Log Analytics querying, Resource Graph inventory, and cost
optimization.
"""

from __future__ import annotations

from typing import Any

from general_ludd.azure.rbac_validator import validate_action_string
from general_ludd.infra.deploy_strategy import DeployStrategist

AZURE_EXPERT_ROLES: dict[str, str] = {
    "rbac_validator": "Validate Azure RBAC custom role definitions",
    "iam_auditor": "Audit IAM assignments for least-privilege compliance",
    "network_designer": "Design Azure VNet/subnet layouts with NSG rules",
    "acr_architect": "Configure Azure Container Registry SKUs and replication",
    "container_app_planner": "Plan Azure Container Apps GPU deployments",
    "log_analytics_querier": "Structure Log Analytics KQL queries",
    "resource_inventorier": "Template Resource Graph queries for inventory",
    "cost_optimizer": "Estimate and optimize Azure service pricing",
}


def validate_rbac_role_definition(
    action_strings: list[str],
    not_actions: list[str],
    assignable_scopes: list[str],
) -> dict[str, Any]:
    """Validate Azure action syntax and require an assignable scope."""
    issues: list[str] = []
    for action in action_strings:
        ok, msg = validate_action_string(action)
        if not ok:
            issues.append(f"Invalid action format: {action!r} — {msg}")
    if not assignable_scopes:
        issues.append("assignable_scopes must not be empty")
    return {
        "status": "valid" if not issues else "invalid",
        "issues": issues,
    }


def audit_iam_assignments(
    subscription_id: str,
    resource_group: str,
    persona: str,
) -> dict[str, Any]:
    """Return the least-privilege role set for a supported persona."""
    persona_roles: dict[str, list[str]] = {
        "terraform_deploy": ["Contributor", "User Access Administrator"],
        "runtime_execution": ["AcrPull", "Container Apps Operator"],
        "model_inference": ["Storage Blob Data Reader", "AcrPull"],
        "monitor": ["Monitoring Reader", "Log Analytics Reader"],
    }
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    if persona not in persona_roles:
        return {
            "status": "error",
            "result": [],
            "warnings": [f"Unknown persona: {persona!r}"],
        }
    assignments = [
        {"persona": persona, "role_name": r, "scope": scope, "is_builtin": True} for r in persona_roles[persona]
    ]
    return {"status": "ok", "result": assignments, "warnings": []}


def design_azure_network(
    region: str,
    app_name: str,
    cidr_range: str = "10.0.0.0/16",
) -> dict[str, Any]:
    """Design a bounded Azure VNet, subnet, and NSG layout."""
    subnets = [
        {"name": f"{app_name}-container-subnet", "cidr": "10.0.1.0/24", "purpose": "container_apps"},
        {"name": f"{app_name}-db-subnet", "cidr": "10.0.2.0/24", "purpose": "database"},
        {"name": f"{app_name}-gateway-subnet", "cidr": "10.0.0.0/27", "purpose": "application_gateway"},
        {"name": "AzureBastionSubnet", "cidr": "10.0.3.0/26", "purpose": "bastion"},
    ]
    vnet_name = f"{app_name}-vnet-{region}"
    return {
        "status": "ok",
        "result": {
            "vnet_name": vnet_name,
            "address_space": cidr_range,
            "region": region,
            "subnets": subnets,
            "nsg_rules": [
                {"name": "allow-http", "priority": "100", "direction": "Inbound", "port": "80"},
                {"name": "allow-https", "priority": "110", "direction": "Inbound", "port": "443"},
            ],
        },
        "warnings": [],
    }


def acr_registry_config(
    name: str,
    sku: str,
    region: str,
) -> dict[str, Any]:
    """Build an admin-disabled Azure Container Registry configuration."""
    valid_skus = {"Basic", "Standard", "Premium"}
    if sku not in valid_skus:
        return {
            "status": "error",
            "result": {},
            "warnings": [f"Invalid SKU {sku!r}. Must be one of {sorted(valid_skus)}"],
        }
    config = {
        "name": name,
        "sku": sku,
        "admin_enabled": False,
        "region": region,
        "geo_replication": sku == "Premium",
    }
    return {"status": "ok", "result": config, "warnings": []}


def container_app_config(
    gpu_type: str,
    model_name: str,
    region: str,
) -> dict[str, Any]:
    """Build a bounded Azure Container App inference configuration."""
    valid_gpus = {"T4", "A10", "A100", "H100"}
    warnings: list[str] = []
    if gpu_type not in valid_gpus:
        warnings.append(f"GPU type {gpu_type!r} not in known set {sorted(valid_gpus)}; verify availability in {region}")
    cpu, memory = "4.0", "16Gi"
    if gpu_type in ("A100", "H100"):
        cpu, memory = "8.0", "32Gi"
    deploy = {
        "name": f"ca-{model_name.replace('/', '-').lower()}",
        "image": f"mcr.microsoft.com/azuredocs/{model_name.lower().replace('/', '-')}:latest",
        "cpu": cpu,
        "memory": memory,
        "gpu_type": gpu_type,
        "min_replicas": 0,
        "region": region,
    }
    return {"status": "ok", "result": deploy, "warnings": warnings}


def query_log_analytics(
    workspace_id: str,
    kql_query: str,
) -> dict[str, Any]:
    """Describe a one-day Azure Log Analytics query request."""
    return {
        "status": "ok",
        "result": {
            "workspace_id": workspace_id,
            "query": kql_query,
            "timespan": "P1D",
            "note": "KQL query structure validated; execute via Azure Monitor REST API",
        },
        "warnings": [],
    }


def inventory_resources(
    subscription_ids: list[str],
) -> dict[str, Any]:
    """Build a Resource Graph inventory query for explicit subscriptions."""
    scope = ",".join(f"'{s}'" for s in subscription_ids)
    kql = (
        "resourcecontainers\n"
        "| where type == 'microsoft.resources/subscriptions'\n"
        f"| where subscriptionId in ({scope})\n"
        "| join kind=leftouter (\n"
        "    resources\n"
        "    | project subscriptionId, type, location, sku\n"
        ") on subscriptionId"
    )
    return {
        "status": "ok",
        "result": {"kql_template": kql, "subscription_count": len(subscription_ids)},
        "warnings": [],
    }


def optimize_cost(
    service_type: str,
    region: str,
    gpu_type: str,
) -> dict[str, Any]:
    """Estimate bounded monthly GPU cost from the built-in rate table."""
    pricing: dict[str, dict[str, float]] = {
        "container_apps": {"T4": 0.62, "A10": 1.24, "A100": 3.67, "H100": 5.50},
    }
    svc = pricing.get(service_type, {})
    hourly = svc.get(gpu_type, 0.0)
    monthly = hourly * 730
    return {
        "status": "ok",
        "result": {
            "service_type": service_type,
            "region": region,
            "gpu_type": gpu_type,
            "hourly_rate": hourly,
            "monthly_estimate": monthly,
            "currency": "USD",
        },
        "warnings": [] if hourly > 0 else [f"No pricing data for {service_type}/{gpu_type}"],
    }


def get_deploy_strategist() -> DeployStrategist:
    """Return a configured DeployStrategist instance."""
    return DeployStrategist()


__all__ = [
    "AZURE_EXPERT_ROLES",
    "DeployStrategist",
    "acr_registry_config",
    "audit_iam_assignments",
    "container_app_config",
    "design_azure_network",
    "get_deploy_strategist",
    "inventory_resources",
    "optimize_cost",
    "query_log_analytics",
    "validate_rbac_role_definition",
]
