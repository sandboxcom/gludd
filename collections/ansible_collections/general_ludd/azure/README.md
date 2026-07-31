# general_ludd.azure

Azure cloud operations collection — IAM role audit, RBAC policy validation,
virtual network design, container app deployment, ACR configuration, Log
Analytics query, resource inventory, and cost optimization.

All roles carry orchestration only (parameter validation, output capture,
JSON marshalling); cloud logic lives in `src/general_ludd/azure/`.

## Implemented roles (`roles/`)

| Role | Purpose |
|---|---|
| `iam_role_audit` | Audit IAM role assignments for a persona. |
| `rbac_policy_validate` | Validate an RBAC custom role definition. |
| `network_design` | Design an Azure virtual network with subnets. |
| `container_app_deploy` | Configure a container app deployment with GPU. |
| `acr_registry_config` | Configure an Azure Container Registry. |
| `log_analytics_query` | Query a Log Analytics workspace. |
| `resource_inventory` | Inventory Azure resources via Resource Graph. |
| `cost_optimize` | Optimize Azure costs for a service type. |

## Python service API (`src/general_ludd/azure/`)

Typed entry points consumed by the roles; the collection never re-implements
the logic below.

| Module | Key exports |
|---|---|
| `core.py` | `audit_iam_assignments`, `validate_rbac_role_definition`, `design_azure_network`, `container_app_config`, `acr_registry_config`, `query_log_analytics`, `inventory_resources`, `optimize_cost`, `AZURE_EXPERT_ROLES` |
| `contracts.py` | `AzureRbacRole`, `IamAssignment`, `NetworkDesign`, `AcrConfig`, `ContainerAppDeployConfig`, `LogAnalyticsQuery`, `PricingResult` |

## Quick start

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.azure.iam_role_audit
      vars:
        role_enabled: true
        persona: terraform_deploy
```

## Tests

```bash
make test TESTFILE='tests/unit/test_azure_core.py'
```

## Safety rules

- No credential paths in defaults, tasks, or output.
- No secrets in JSON verdicts.
- All roles default to `role_enabled: false` — opt-in only.
- Outputs write to `/tmp/gludd-azure-*` by default.
