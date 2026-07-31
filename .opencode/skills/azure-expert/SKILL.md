---
name: azure-expert
description: "Use for Azure cloud operations: IAM role design and RBAC validation, managed identity and service principal management, Container App and ACR configuration, VNet/subnet/NSG network design, Log Analytics querying, Resource Graph inventory, cost optimization analysis, and Entra ID integration. Trigger keywords: azure, IAM, RBAC, role definition, managed identity, service principal, Container App, ACR, Network Security Group, VNet, subnet, Log Analytics, Resource Graph, cost optimization, entra, subscription, resource group."
location: "/Users/shawnwilson/gludd/.opencode/skills/azure-expert/SKILL.md"
---

# Azure Expert

An Azure cloud operations expert that designs least-privilege IAM roles, validates
RBAC definitions, plans network topologies, configures Container Apps with ACR,
queries Log Analytics and Resource Graph, and performs cost analysis. Backed by the
typed service modules under `src/general_ludd/azure/` and the IAM policy validator at
`scripts/validate_azure_iam_policy.py`.

## When to Use

- "Design a least-privilege RBAC role for deploying Container Apps with GPU compute."
- "Validate this role definition against Azure RBAC schema."
- "Which built-in roles cover ACR pull and push?"
- "Design a VNet with subnets for Container Apps and private endpoints."
- "Generate NSG rules that allow Container Apps egress."
- "Query Log Analytics for container app crash counts over the last 24 hours."
- "Inventory all Container Apps and ACR registries across subscriptions."
- "Estimate monthly cost for a GPU-enabled Container App environment."

## Available Roles

The role router maps a request to one of eight domain functions. Every role returns
a typed contract (`contracts.py`) or a structured advisory result.

| Role | Entry point | Owning module |
|---|---|---|
| `rbac_validate` | `validate_rbac_role_definition(role)` | `core`, `rbac_validator` |
| `iam_design` | `recommend_roles_for_persona(persona)` | `iam_advisor` |
| `iam_audit` | `audit_iam_assignments(sub_id)` | `core`, `iam_advisor` |
| `network_design` | `design_azure_network(requirement)` | `network_designer`, `core` |
| `acr_config` | `acr_registry_config(spec)` | `core` |
| `container_app_config` | `container_app_config(spec)` | `core` |
| `query_logs` | `query_log_analytics(query)` | `core` |
| `cost_optimize` | `optimize_cost(sub_id, services)` | `core`, `contracts` |

Resource Graph inventory (`inventory_resources`) and NSG rule generation
(`generate_nsg_rules`) are always-available helpers. The `PERSONA_ROLE_MAP` in
`iam_advisor` maps common workload personas to built-in roles.

Data contracts: `AzureRbacRole`, `IamAssignment`, `NetworkDesign`, `AcrConfig`,
`ContainerAppDeployConfig`, `LogAnalyticsQuery`, `PricingResult` — all in `contracts.py`.

## Safety Boundaries

- **Never emit credentials in output.** Managed identity is preferred over service
  principal client secrets. If a service principal is unavoidable, log a warning
  and surface only the App ID — never the secret or certificate value.
- **Least-privilege by default.** Every role recommendation SHALL start from the
  narrowest set of actions that satisfy the workload and expand only with explicit
  justification. Built-in roles are preferred over custom definitions.
- **Security-critical denials are non-negotiable.** The validator in
  `rbac_validator.py` enforces that `NotActions` includes `runCommand`, role
  assignment write/delete, and role definition write/delete. A role missing these
  is rejected regardless of other validity.
- **Network designs default to restricted egress.** NSG rules default-deny outbound;
  explicit allow rules are added only for documented endpoints. AzureFirewall or
  NAT Gateway is recommended for production egress.
- **Cost estimates are advisory estimates from list pricing — not committed spend.**
  Every `PricingResult` carries `region`, `hourly_rate`, and `monthly_estimate` with
  the pricing API timestamp. Estimates do not reflect enterprise agreements,
  reservations, or spot discounts.
- **IAM audit output is read-only.** The audit functions enumerate assignments but
  never mutate them. Removal or reassignment requires explicit user authorization.
- The module performs no network I/O to Azure management APIs — it operates on
  local JSON policy files, typed contracts, and user-provided data.

## Usage Examples

```python
from general_ludd.azure import (
    AzureRbacRole,
    NetworkDesign,
    audit_iam_assignments,
    design_azure_network,
    recommend_roles_for_persona,
    validate_rbac_role_definition,
)

role = AzureRbacRole(
    name="Container App Deployer",
    description="Least-privilege role for deploying Container Apps.",
    actions=[
        "Microsoft.App/containerApps/read",
        "Microsoft.App/containerApps/write",
        "Microsoft.App/containerApps/delete",
        "Microsoft.ContainerRegistry/registries/pull/read",
    ],
    not_actions=[],
    assignable_scopes=["/subscriptions/{subscription_id}"],
)
result = validate_rbac_role_definition(role)
# -> {"valid": True, "errors": [], "warnings": ["Missing security-critical denials"]}
```

```python
persona_roles = recommend_roles_for_persona("container-app-developer")
# -> [{"builtin": True, "role_name": "AcrPull", "scope": "/subscriptions/{sub}"}, ...]
```

```python
network = design_azure_network(
    NetworkDesign(vnet_name="gludd-vnet", address_space="10.0.0.0/16")
)
# -> NetworkDesign with subnets, NSG rules, and delegation configs populated
```

## See Also

- `docs/specs/FEATURE_AZURE_EXPERT.md` — capability spec and acceptance tests
- `scripts/validate_azure_iam_policy.py` — IAM policy schema validator
- `config/infra/azure-iam-policy.json` — reference Container App Deployer role
- `src/general_ludd/azure/` — service modules (core, contracts, advisors)
- `collections/ansible_collections/general_ludd/azure/` — ansible collection wrappers
