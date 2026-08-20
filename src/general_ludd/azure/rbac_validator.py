"""Validate Azure RBAC action strings and role definitions.

Provides security-critical NotAction enforcement, built-in role lookup, a
provider catalog, and high-level role-definition generation.
"""

from __future__ import annotations

import re
from typing import Any

# Providers recognised by the validator (non-exhaustive).
KNOWN_RBAC_ACTIONS: frozenset[str] = frozenset(
    {
        "Microsoft.Compute/virtualMachines/read",
        "Microsoft.Compute/virtualMachines/write",
        "Microsoft.Compute/virtualMachines/delete",
        "Microsoft.Compute/virtualMachines/start/action",
        "Microsoft.Network/virtualNetworks/read",
        "Microsoft.Network/virtualNetworks/write",
        "Microsoft.Network/virtualNetworks/delete",
        "Microsoft.App/containerApps/read",
        "Microsoft.App/containerApps/write",
        "Microsoft.App/managedEnvironments/read",
        "Microsoft.Storage/storageAccounts/read",
        "Microsoft.Storage/storageAccounts/write",
        "Microsoft.ContainerRegistry/registries/read",
        "Microsoft.ContainerRegistry/registries/write",
        "Microsoft.OperationalInsights/workspaces/read",
        "Microsoft.OperationalInsights/workspaces/query/action",
    }
)

FORBIDDEN_SUFFIX_PATTERNS: tuple[str, ...] = (
    "/list/action",
    "/listkeys/action",
)

SECRET_ACTION_PATTERNS: dict[str, str] = {
    # sharedKeys/read is NOT included — it is the correct and valid action
    # for Microsoft.OperationalInsights/workspaces/sharedKeys/read (reading
    # Log Analytics workspace keys). Azure uses mixed conventions: some
    # key/secret operations use /action (Storage, Key Vault), while
    # sharedKeys legitimately uses /read.
    r"/keys/read$": "Key operations use /action not /read",
    r"/secrets/read$": "Secret operations use /action not /read",
    r"/listCredentials/read$": "Credential listing uses /action not /read",
    r"/listSecrets/read$": "Secret listing uses /action not /read",
}

_VALID_ACTION_RE = re.compile(r"^Microsoft\.\w+(/\w+)+(/(read|write|delete|action))$")

# Security-critical operations that every custom role should deny via NotActions.
SECURITY_CRITICAL_OPS: frozenset[str] = frozenset(
    {
        "Microsoft.Compute/virtualMachines/runCommand/action",
        "Microsoft.Compute/virtualMachines/runCommands/read",
        "Microsoft.Compute/virtualMachines/runCommands/write",
        "Microsoft.Compute/virtualMachines/runCommands/delete",
        "Microsoft.Authorization/roleAssignments/write",
        "Microsoft.Authorization/roleAssignments/delete",
        "Microsoft.Authorization/roleDefinitions/write",
        "Microsoft.Authorization/roleDefinitions/delete",
    }
)

# ---------------------------------------------------------------------------
# Built-in Azure RBAC roles — canonical GUIDs from Microsoft documentation
# ---------------------------------------------------------------------------

AZURE_BUILTIN_ROLES: dict[str, str] = {
    "Contributor": "b24988ac-6180-42a0-ab88-20f7382dd24c",
    "Owner": "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",
    "Reader": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
    "User Access Administrator": "18d7d88d-d35e-4fb5-a5c3-cc33878ba7b9",
    "Virtual Machine Contributor": "9980e02c-c2be-4d73-94e8-173b1dc7cf3c",
    "Virtual Machine Administrator Login": "1c0163c0-47e6-4577-8991-ea5c82e286e4",
    "Network Contributor": "4d97b98b-1d4f-4787-a291-c67834d212e7",
    "Storage Blob Data Contributor": "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    "Storage Blob Data Reader": "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
    "Storage Blob Data Owner": "b7e6dc6d-f1e8-4753-8033-0f276bb0955b",
    "Storage Queue Data Contributor": "974c5e8b-45b9-4653-ba55-5f855dd0fb88",
    "Storage Table Data Contributor": "0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3",
    "Key Vault Administrator": "00482a5a-887f-4fb3-b363-3b7fe8e74483",
    "Key Vault Secrets User": "4633458b-17de-408a-b874-0445c86b69e6",
    "Key Vault Secrets Officer": "b86a8fe4-44ce-4948-aee5-eccb2c155cd7",
    "Key Vault Reader": "21090545-7ca7-4776-b22c-e363652d74d2",
    "Key Vault Crypto User": "12338af0-0e69-4776-bea7-57ae8d297424",
    "Key Vault Crypto Officer": "14b46e9e-c2b7-41b4-b07b-48a6ebf60603",
    "AcrPull": "7f951dda-4ed3-4680-a7ca-43fe172d538d",
    "AcrPush": "8311e382-0749-4cb8-b61a-304f252e45e2",
    "AcrDelete": "c2f4ef07-c644-48eb-af81-4b1b4947fb11",
    "AcrImageSigner": "6cef56e8-d556-48e5-a04f-b8e64114680f",
    "AcrQuarantineReader": "cdda3590-29a3-44f6-95f2-9f980659eb04",
    "AcrQuarantineWriter": "c8d4ff99-41c3-41a8-9f60-21dfdad596c7",
    "Monitoring Reader": "43d0d8ad-25c7-4714-9337-8ba259a9fe05",
    "Monitoring Contributor": "749f88d5-cbae-40b8-bcfc-e573ddc772fa",
    "Monitoring Metrics Publisher": "3913510d-42f4-4e42-8a64-420c390055eb",
    "Log Analytics Reader": "73c42c96-874c-492b-b04d-ab87d138a893",
    "Log Analytics Contributor": "92aaf0da-9dab-42b6-94a3-d43ce8d16293",
    "Cognitive Services User": "a97b65f3-24c7-4388-baec-2e87135dc908",
    "Cognitive Services Contributor": "25fbc0a9-bd71-4147-89e3-3ca92b02bb1a",
    "DNS Zone Contributor": "befefa01-2a29-4197-83a8-272ff33ce314",
    "Private DNS Zone Contributor": "b12aa53e-6015-4669-85d0-8515ebb3ae7f",
    "Web Plan Contributor": "2cc479cb-7b4d-49a8-b449-8c00fd0f0a4b",
    "Website Contributor": "de139f84-1756-47ae-9be6-808fbbe84772",
    "CDN Profile Contributor": "ec156ff8-a8d1-4d15-830c-5b80698ca432",
    "Cosmos DB Account Reader": "fbdf93bf-df7d-467e-a4d2-9458aa1360c8",
    "Cosmos DB Operator": "2308157d-90b2-4fd8-b5e2-158d84629da3",
    "DocumentDB Account Contributor": "5bd9cd88-fe45-4216-938b-f97437e15450",
    "SQL DB Contributor": "9b7fa17d-e63e-47b0-bb0a-15c516ac86ec",
    "SQL Security Manager": "056cd41c-7e88-42e1-933e-88ba6a50c9c3",
    "SQL Server Contributor": "6d8ee4ec-f05a-4a1d-8b00-a9b17e38b437",
    "Managed Identity Contributor": "e40ec5ff-8bac-4a1b-b43f-8d6b0e3a2155",
    "Managed Identity Operator": "f1a07417-d97a-45cb-824c-7a7467783830",
    "App Configuration Data Owner": "5ae67dd6-50cb-40e7-96ff-dc2bfa4b606b",
    "App Configuration Data Reader": "516239f1-63e1-4d78-a4de-a74fb236a071",
    "Azure Kubernetes Service RBAC Cluster Admin": "b1ff04bb-8a4e-4dc4-8eb5-8693973ce19b",
    "Azure Kubernetes Service RBAC Admin": "3498e952-d568-435e-9b2c-8d77e338d7f7",
    "Azure Kubernetes Service Cluster User": "4abbcc35-e782-43d8-92c5-2d3f1bd2253f",
    "Azure Kubernetes Service Contributor": "ed7f3fbd-7b88-4dd4-9017-9ae7ca4616b2",
}

# ---------------------------------------------------------------------------
# Azure resource provider namespaces
# ---------------------------------------------------------------------------

AZURE_RESOURCE_PROVIDERS: dict[str, str] = {
    "Microsoft.Compute": "Virtual Machines, VM Scale Sets, Disks, Images, Snapshots, Availability Sets",
    "Microsoft.Network": (
        "Virtual Networks, Subnets, NSGs, Public IPs, Network Interfaces, Load Balancers, VPN Gateways, Route Tables"
    ),
    "Microsoft.Storage": "Storage Accounts, Blob Services, File Shares, Queues, Tables",
    "Microsoft.ContainerRegistry": "Azure Container Registry — repositories, webhooks, tasks, tokens",
    "Microsoft.App": "Azure Container Apps — managed environments, container apps, revisions, Dapr components",
    "Microsoft.Web": "App Service Plans, Web Apps, Function Apps, Logic Apps",
    "Microsoft.KeyVault": "Key Vault — keys, secrets, certificates, HSMs",
    "Microsoft.Resources": "Resource Groups, Deployments, Tags, Providers, Policy Assignments",
    "Microsoft.Authorization": "Role Assignments, Role Definitions, Policy Assignments",
    "Microsoft.OperationalInsights": "Log Analytics Workspaces, Tables, Linked Services",
    "Microsoft.Insights": "Metrics, Diagnostic Settings, Alerts, Activity Logs, Scheduled Query Rules",
    "Microsoft.Sql": "SQL Servers, Elastic Pools, Managed Instances",
    "Microsoft.DocumentDB": "Cosmos DB, MongoDB, Cassandra, Gremlin, Table API",
    "Microsoft.ServiceBus": "Service Bus Namespaces, Queues, Topics",
    "Microsoft.EventHub": "Event Hub Namespaces, Event Hubs, Consumer Groups",
    "Microsoft.CognitiveServices": "Cognitive Services Accounts, OpenAI, Language, Vision, Speech",
    "Microsoft.ApiManagement": "API Management Services — APIs, Products, Subscriptions",
    "Microsoft.DataFactory": "Data Factories — Pipelines, Linked Services, Datasets, Triggers",
    "Microsoft.Search": "Azure Cognitive Search Services",
    "Microsoft.EventGrid": "Event Grid Topics, Domains, System Topics, Partner Namespaces",
    "Microsoft.Relay": "Azure Relay Namespaces, Hybrid Connections, WCF Relays",
    "Microsoft.Batch": "Batch Accounts — Pools, Jobs, Tasks, Applications",
    "Microsoft.DBforPostgreSQL": "Azure Database for PostgreSQL — Flexible Servers, Firewall Rules",
    "Microsoft.DBforMySQL": "Azure Database for MySQL — Flexible Servers, Firewall Rules",
    "Microsoft.ContainerService": "Azure Kubernetes Service (AKS) — Managed Clusters, Node Pools",
    "Microsoft.ManagedIdentity": "User Assigned Managed Identities",
}

# ---------------------------------------------------------------------------
# Verified Azure RBAC operations grouped by resource provider
# ---------------------------------------------------------------------------

PROVIDER_OPERATIONS: dict[str, frozenset[str]] = {
    "Microsoft.Resources": frozenset(
        {
            "Microsoft.Resources/subscriptions/resourceGroups/read",
            "Microsoft.Resources/subscriptions/resourceGroups/write",
            "Microsoft.Resources/subscriptions/resourceGroups/delete",
            "Microsoft.Resources/subscriptions/resourceGroups/moveResources/action",
            "Microsoft.Resources/subscriptions/locations/read",
            "Microsoft.Resources/subscriptions/providers/read",
            "Microsoft.Resources/subscriptions/providers/register/action",
            "Microsoft.Resources/deployments/read",
            "Microsoft.Resources/deployments/write",
            "Microsoft.Resources/deployments/delete",
            "Microsoft.Resources/deployments/operationstatuses/read",
            "Microsoft.Resources/deployments/operations/read",
            "Microsoft.Resources/deployments/cancel/action",
            "Microsoft.Resources/deployments/validate/action",
            "Microsoft.Resources/deployments/exportTemplate/action",
            "Microsoft.Resources/tags/read",
            "Microsoft.Resources/tags/write",
            "Microsoft.Resources/tags/delete",
            "Microsoft.Resources/subscriptions/read",
            "Microsoft.Resources/subscriptions/resourceGroups/resources/read",
            "Microsoft.Resources/providers/read",
            "Microsoft.Resources/checkResourceName/action",
        }
    ),
    "Microsoft.ContainerRegistry": frozenset(
        {
            "Microsoft.ContainerRegistry/registries/read",
            "Microsoft.ContainerRegistry/registries/write",
            "Microsoft.ContainerRegistry/registries/delete",
            "Microsoft.ContainerRegistry/registries/listCredentials/action",
            "Microsoft.ContainerRegistry/registries/importImage/action",
            "Microsoft.ContainerRegistry/registries/pull/read",
            "Microsoft.ContainerRegistry/registries/push/write",
            "Microsoft.ContainerRegistry/registries/regenerateCredential/action",
            "Microsoft.ContainerRegistry/registries/builds/read",
            "Microsoft.ContainerRegistry/registries/builds/write",
            "Microsoft.ContainerRegistry/registries/scheduleRun/action",
            "Microsoft.ContainerRegistry/registries/tasks/read",
            "Microsoft.ContainerRegistry/registries/tasks/write",
            "Microsoft.ContainerRegistry/registries/tasks/delete",
        }
    ),
    "Microsoft.App": frozenset(
        {
            "Microsoft.App/managedEnvironments/read",
            "Microsoft.App/managedEnvironments/write",
            "Microsoft.App/managedEnvironments/delete",
            "Microsoft.App/managedEnvironments/join/action",
            "Microsoft.App/managedEnvironments/storages/read",
            "Microsoft.App/managedEnvironments/storages/write",
            "Microsoft.App/managedEnvironments/storages/delete",
            "Microsoft.App/containerApps/read",
            "Microsoft.App/containerApps/write",
            "Microsoft.App/containerApps/delete",
            "Microsoft.App/containerApps/listSecrets/action",
            "Microsoft.App/containerApps/revisions/read",
            "Microsoft.App/containerApps/revisions/restart/action",
            "Microsoft.App/containerApps/revisions/activate/action",
            "Microsoft.App/containerApps/revisions/deactivate/action",
            "Microsoft.App/locations/managedEnvironmentOperationResults/read",
            "Microsoft.App/locations/managedEnvironmentOperationStatuses/read",
            "Microsoft.App/locations/containerAppOperationResults/read",
            "Microsoft.App/locations/containerAppOperationStatuses/read",
            "Microsoft.App/containerApps/authConfigs/read",
            "Microsoft.App/containerApps/authConfigs/write",
            "Microsoft.App/managedEnvironments/certificates/read",
            "Microsoft.App/managedEnvironments/certificates/write",
        }
    ),
    "Microsoft.Network": frozenset(
        {
            "Microsoft.Network/virtualNetworks/read",
            "Microsoft.Network/virtualNetworks/write",
            "Microsoft.Network/virtualNetworks/delete",
            "Microsoft.Network/virtualNetworks/subnets/read",
            "Microsoft.Network/virtualNetworks/subnets/write",
            "Microsoft.Network/virtualNetworks/subnets/delete",
            "Microsoft.Network/virtualNetworks/subnets/join/action",
            "Microsoft.Network/virtualNetworks/subnets/prepareNetworkPolicies/action",
            "Microsoft.Network/virtualNetworks/subnets/unprepareNetworkPolicies/action",
            "Microsoft.Network/networkSecurityGroups/read",
            "Microsoft.Network/networkSecurityGroups/write",
            "Microsoft.Network/networkSecurityGroups/delete",
            "Microsoft.Network/networkSecurityGroups/securityRules/read",
            "Microsoft.Network/networkSecurityGroups/securityRules/write",
            "Microsoft.Network/networkSecurityGroups/securityRules/delete",
            "Microsoft.Network/publicIPAddresses/read",
            "Microsoft.Network/publicIPAddresses/write",
            "Microsoft.Network/publicIPAddresses/delete",
            "Microsoft.Network/publicIPAddresses/join/action",
            "Microsoft.Network/networkInterfaces/read",
            "Microsoft.Network/networkInterfaces/write",
            "Microsoft.Network/networkInterfaces/delete",
            "Microsoft.Network/networkInterfaces/join/action",
            "Microsoft.Network/loadBalancers/read",
            "Microsoft.Network/loadBalancers/write",
            "Microsoft.Network/loadBalancers/delete",
            "Microsoft.Network/routeTables/read",
            "Microsoft.Network/routeTables/write",
            "Microsoft.Network/routeTables/delete",
            "Microsoft.Network/applicationGateways/read",
            "Microsoft.Network/applicationGateways/write",
            "Microsoft.Network/applicationGateways/delete",
            "Microsoft.Network/applicationSecurityGroups/read",
            "Microsoft.Network/applicationSecurityGroups/write",
            "Microsoft.Network/applicationSecurityGroups/delete",
            "Microsoft.Network/applicationSecurityGroups/join/action",
            "Microsoft.Network/privateEndpoints/read",
            "Microsoft.Network/privateEndpoints/write",
            "Microsoft.Network/privateEndpoints/delete",
            "Microsoft.Network/privateDnsZones/read",
            "Microsoft.Network/privateDnsZones/write",
            "Microsoft.Network/virtualNetworks/peer/action",
            "Microsoft.Network/virtualNetworks/virtualNetworkPeerings/read",
            "Microsoft.Network/virtualNetworks/virtualNetworkPeerings/write",
        }
    ),
    "Microsoft.Compute": frozenset(
        {
            "Microsoft.Compute/skus/read",
            "Microsoft.Compute/locations/usages/read",
            "Microsoft.Compute/locations/vmSizes/read",
            "Microsoft.Compute/virtualMachines/read",
            "Microsoft.Compute/virtualMachines/write",
            "Microsoft.Compute/virtualMachines/delete",
            "Microsoft.Compute/virtualMachines/start/action",
            "Microsoft.Compute/virtualMachines/restart/action",
            "Microsoft.Compute/virtualMachines/deallocate/action",
            "Microsoft.Compute/virtualMachines/instanceView/read",
            "Microsoft.Compute/virtualMachines/extensions/read",
            "Microsoft.Compute/virtualMachines/extensions/write",
            "Microsoft.Compute/virtualMachines/extensions/delete",
            "Microsoft.Compute/virtualMachines/powerOff/action",
            "Microsoft.Compute/virtualMachines/runCommand/action",
            "Microsoft.Compute/virtualMachines/runCommands/read",
            "Microsoft.Compute/virtualMachines/runCommands/write",
            "Microsoft.Compute/virtualMachines/runCommands/delete",
            "Microsoft.Compute/disks/read",
            "Microsoft.Compute/disks/write",
            "Microsoft.Compute/disks/delete",
            "Microsoft.Compute/snapshots/read",
            "Microsoft.Compute/snapshots/write",
            "Microsoft.Compute/snapshots/delete",
            "Microsoft.Compute/images/read",
            "Microsoft.Compute/images/write",
            "Microsoft.Compute/images/delete",
            "Microsoft.Compute/availabilitySets/read",
            "Microsoft.Compute/availabilitySets/write",
            "Microsoft.Compute/availabilitySets/delete",
            "Microsoft.Compute/sshPublicKeys/read",
            "Microsoft.Compute/sshPublicKeys/write",
            "Microsoft.Compute/virtualMachineScaleSets/read",
            "Microsoft.Compute/virtualMachineScaleSets/write",
            "Microsoft.Compute/virtualMachineScaleSets/delete",
        }
    ),
    "Microsoft.Authorization": frozenset(
        {
            "Microsoft.Authorization/roleAssignments/read",
            "Microsoft.Authorization/roleAssignments/write",
            "Microsoft.Authorization/roleAssignments/delete",
            "Microsoft.Authorization/roleDefinitions/read",
            "Microsoft.Authorization/roleDefinitions/write",
            "Microsoft.Authorization/roleDefinitions/delete",
        }
    ),
    "Microsoft.OperationalInsights": frozenset(
        {
            "Microsoft.OperationalInsights/workspaces/read",
            "Microsoft.OperationalInsights/workspaces/write",
            "Microsoft.OperationalInsights/workspaces/delete",
            "Microsoft.OperationalInsights/workspaces/sharedKeys/read",
            "Microsoft.OperationalInsights/workspaces/query/action",
        }
    ),
    "Microsoft.Insights": frozenset(
        {
            "Microsoft.Insights/diagnosticSettings/read",
            "Microsoft.Insights/diagnosticSettings/write",
            "Microsoft.Insights/diagnosticSettings/delete",
            "Microsoft.Insights/metrics/read",
            "Microsoft.Insights/alertRules/read",
            "Microsoft.Insights/alertRules/write",
            "Microsoft.Insights/alertRules/delete",
            "Microsoft.Insights/actionGroups/read",
            "Microsoft.Insights/actionGroups/write",
            "Microsoft.Insights/actionGroups/delete",
            "Microsoft.Insights/scheduledQueryRules/read",
            "Microsoft.Insights/scheduledQueryRules/write",
            "Microsoft.Insights/scheduledQueryRules/delete",
        }
    ),
    "Microsoft.Storage": frozenset(
        {
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Storage/storageAccounts/write",
            "Microsoft.Storage/storageAccounts/delete",
            "Microsoft.Storage/storageAccounts/listKeys/action",
            "Microsoft.Storage/storageAccounts/blobServices/read",
            "Microsoft.Storage/storageAccounts/blobServices/write",
            "Microsoft.Storage/storageAccounts/fileServices/read",
            "Microsoft.Storage/storageAccounts/fileServices/write",
            "Microsoft.Storage/storageAccounts/queueServices/read",
            "Microsoft.Storage/storageAccounts/queueServices/write",
            "Microsoft.Storage/storageAccounts/tableServices/read",
            "Microsoft.Storage/storageAccounts/tableServices/write",
        }
    ),
    "Microsoft.KeyVault": frozenset(
        {
            "Microsoft.KeyVault/vaults/read",
            "Microsoft.KeyVault/vaults/write",
            "Microsoft.KeyVault/vaults/delete",
            "Microsoft.KeyVault/vaults/secrets/read",
            "Microsoft.KeyVault/vaults/keys/read",
        }
    ),
    "Microsoft.Web": frozenset(
        {
            "Microsoft.Web/serverfarms/read",
            "Microsoft.Web/serverfarms/write",
            "Microsoft.Web/serverfarms/delete",
            "Microsoft.Web/sites/read",
            "Microsoft.Web/sites/write",
            "Microsoft.Web/sites/delete",
            "Microsoft.Web/sites/start/action",
            "Microsoft.Web/sites/stop/action",
        }
    ),
    "Microsoft.Sql": frozenset(
        {
            "Microsoft.Sql/servers/read",
            "Microsoft.Sql/servers/write",
            "Microsoft.Sql/servers/delete",
            "Microsoft.Sql/servers/databases/read",
            "Microsoft.Sql/servers/databases/write",
            "Microsoft.Sql/servers/databases/delete",
        }
    ),
    "Microsoft.DocumentDB": frozenset(
        {
            "Microsoft.DocumentDB/databaseAccounts/read",
            "Microsoft.DocumentDB/databaseAccounts/write",
            "Microsoft.DocumentDB/databaseAccounts/delete",
            "Microsoft.DocumentDB/databaseAccounts/listKeys/action",
        }
    ),
    "Microsoft.ServiceBus": frozenset(
        {
            "Microsoft.ServiceBus/namespaces/read",
            "Microsoft.ServiceBus/namespaces/write",
            "Microsoft.ServiceBus/namespaces/delete",
            "Microsoft.ServiceBus/namespaces/queues/read",
            "Microsoft.ServiceBus/namespaces/queues/write",
            "Microsoft.ServiceBus/namespaces/queues/delete",
        }
    ),
    "Microsoft.EventHub": frozenset(
        {
            "Microsoft.EventHub/namespaces/read",
            "Microsoft.EventHub/namespaces/write",
            "Microsoft.EventHub/namespaces/delete",
        }
    ),
    "Microsoft.CognitiveServices": frozenset(
        {
            "Microsoft.CognitiveServices/accounts/read",
            "Microsoft.CognitiveServices/accounts/write",
            "Microsoft.CognitiveServices/accounts/delete",
            "Microsoft.CognitiveServices/accounts/listKeys/action",
        }
    ),
    "Microsoft.ContainerService": frozenset(
        {
            "Microsoft.ContainerService/managedClusters/read",
            "Microsoft.ContainerService/managedClusters/write",
            "Microsoft.ContainerService/managedClusters/delete",
        }
    ),
    "Microsoft.ManagedIdentity": frozenset(
        {
            "Microsoft.ManagedIdentity/userAssignedIdentities/read",
            "Microsoft.ManagedIdentity/userAssignedIdentities/write",
            "Microsoft.ManagedIdentity/userAssignedIdentities/delete",
        }
    ),
}

# ---------------------------------------------------------------------------
# Flat set of every verified action for fast membership lookup
# ---------------------------------------------------------------------------

_ALL_KNOWN_ACTIONS: frozenset[str] = frozenset(action for ops in PROVIDER_OPERATIONS.values() for action in ops)


def all_known_actions() -> frozenset[str]:
    """Return the flat set of every action registered in PROVIDER_OPERATIONS."""
    return _ALL_KNOWN_ACTIONS


# ---------------------------------------------------------------------------
# Action-string validation
# ---------------------------------------------------------------------------

_RE_SECRET_PATTERNS = {re.compile(pat, re.IGNORECASE): msg for pat, msg in SECRET_ACTION_PATTERNS.items()}


def validate_action_string(action: str) -> tuple[bool, str]:
    """Validate a single Azure RBAC action string.

    Returns ``(ok, message)`` — *ok* is ``True`` when the string is a
    well-formed provider/data/action path and does not use ``/read`` for
    key/secret/credential operations.
    """
    if not isinstance(action, str) or not action:
        return False, "empty or non-string action"

    for suffix in FORBIDDEN_SUFFIX_PATTERNS:
        if action.endswith(suffix):
            return False, f"forbidden suffix: {suffix}"

    if not _VALID_ACTION_RE.match(action):
        return False, "malformed action string — expected Microsoft.<Provider>/<resourceType>/<verb>"

    for pattern, message in _RE_SECRET_PATTERNS.items():
        if pattern.search(action):
            return False, f"Action '{action}' may need /action instead of /read — {message.lower()}"

    return True, "ok"


def check_security_critical_denials(not_actions: list[str], actions: list[str] | None = None) -> list[str]:
    """Return the set of security-critical ops *not* covered by *not_actions*.

    If *actions* is provided, only checks security ops that appear in the
    role's actions — a role that does not grant security-critical operations
    does not need to deny them.
    """
    relevant_ops: frozenset[str] = SECURITY_CRITICAL_OPS
    if actions is not None:
        relevant_ops = SECURITY_CRITICAL_OPS & frozenset(actions)

    if not not_actions:
        return sorted(relevant_ops)

    denied_set = frozenset(not_actions)
    missing = sorted(relevant_ops - denied_set)
    return missing


# ---------------------------------------------------------------------------
# Role-definition generator
# ---------------------------------------------------------------------------

_ACTIONS_BY_PROVIDER: dict[str, list[str]] = {}


def _lazy_provider_map() -> dict[str, list[str]]:
    """Build a reverse map from provider namespace to known actions.

    The role-definition generator uses the map to populate actions automatically.
    """
    if _ACTIONS_BY_PROVIDER:
        return _ACTIONS_BY_PROVIDER

    for provider, ops in PROVIDER_OPERATIONS.items():
        for action in ops:
            _ACTIONS_BY_PROVIDER.setdefault(provider, []).append(action)
    return _ACTIONS_BY_PROVIDER


def generate_role_definition(
    name: str,
    description: str,
    providers: list[str],
    scope: str = "/subscriptions/{subscription_id}",
) -> dict[str, Any]:
    """Generate a valid Azure custom-role JSON definition from high-level intent.

    *providers* is a list of Azure resource provider namespaces (e.g.
    ``["Microsoft.Compute", "Microsoft.Network"]``).  The function looks up
    each provider in ``PROVIDER_OPERATIONS`` and automatically includes every
    known action for that provider in the role's ``Actions``.

    Security-critical actions (``SECURITY_CRITICAL_OPS``) that appear in the
    generated ``Actions`` are moved to ``NotActions``.

    Returns a dict suitable for ``az role definition create``.
    """
    provider_map = _lazy_provider_map()
    actions: set[str] = set()
    missing: list[str] = []

    for provider in providers:
        known = provider_map.get(provider)
        if known is None:
            missing.append(provider)
            continue
        actions.update(known)

    if missing:
        raise KeyError(f"Unknown provider namespaces: {missing!r}")

    not_actions = sorted(actions & SECURITY_CRITICAL_OPS)
    actions -= SECURITY_CRITICAL_OPS
    actions_list = sorted(actions)

    return {
        "Name": name,
        "Description": description,
        "Actions": actions_list,
        "NotActions": not_actions,
        "AssignableScopes": [scope],
        "DataActions": [],
        "NotDataActions": [],
    }


# ---------------------------------------------------------------------------
# Schema-level validation
# ---------------------------------------------------------------------------


def validate_against_azure_schema(role_json: dict[str, Any]) -> tuple[bool, list[str]]:
    """Deep-validate a custom-role JSON against Azure RBAC schema rules.

    Checks: required fields, action format, known-action membership (warning
    only), case normalization, security-critical denials, and secret-key
    patterns on /read.

    Returns ``(ok, messages)`` — *ok* is ``True`` when no hard errors were
    found.
    """
    errors: list[str] = []

    required = ["Name", "Description", "Actions", "NotActions", "AssignableScopes"]
    for field in required:
        if field not in role_json:
            errors.append(f"Missing required field: {field}")
            return False, errors

    name = role_json.get("Name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append("Name must be a non-empty string")

    description = role_json.get("Description", "")
    if not isinstance(description, str) or len(description) < 20:
        errors.append("Description must be a string of at least 20 characters")

    actions = role_json.get("Actions", [])
    if not isinstance(actions, list):
        errors.append("Actions must be a list")
        actions = []

    not_actions = role_json.get("NotActions", [])
    if not isinstance(not_actions, list):
        errors.append("NotActions must be a list")
        not_actions = []

    scopes = role_json.get("AssignableScopes", [])
    if not isinstance(scopes, list) or not scopes:
        errors.append("AssignableScopes must be a non-empty list")

    unknown_actions: list[str] = []
    for action in actions:
        ok, msg = validate_action_string(action)
        if not ok:
            errors.append(f"Invalid action '{action}': {msg}")
        elif action not in _ALL_KNOWN_ACTIONS:
            unknown_actions.append(action)

    for action in not_actions:
        ok, msg = validate_action_string(action)
        if not ok:
            errors.append(f"Invalid not-action '{action}': {msg}")

    missing_security = check_security_critical_denials(not_actions, actions)
    if missing_security:
        errors.append(f"NotActions missing security-critical denials: {missing_security!r}")

    if unknown_actions:
        errors.append(
            f"WARNING: {len(unknown_actions)} action(s) not in known provider catalog: "
            f"{unknown_actions[:5]!r}{'...' if len(unknown_actions) > 5 else ''}"
        )

    return len([e for e in errors if not e.startswith("WARNING:")]) == 0, errors
