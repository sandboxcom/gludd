# general_ludd.azure

Azure cloud operations collection — IAM role audit, RBAC policy validation,
virtual network design, container app deployment, ACR configuration, Log
Analytics query, resource inventory, and cost optimization.

Roles own declarative orchestration and bounded policy decisions. Collection
filters normalize provider observations, select allowed lifecycle transitions,
and expose sanitized namespaced variables. Statistical learning and durable
calibration remain in Gludd's typed domain engine; checksum-pinned OpenTofu is
the sole Azure resource writer. The compatible `cloud.terraform` module is kept
as the mature Ansible integration while its binary path selects MPL-2.0
OpenTofu instead of the HashiCorp Terraform CLI.

## Implemented roles (`roles/`)

| Role | Purpose |
|---|---|
| `iam_role_audit` | Audit IAM role assignments for a persona. |
| `rbac_policy_validate` | Validate an RBAC custom role definition. |
| `network_design` | Design an Azure virtual network with subnets. |
| `container_app_deploy` | Observe and reconcile an audited Container Apps GPU stack through OpenTofu. |
| `acr_registry_config` | Configure an Azure Container Registry. |
| `log_analytics_query` | Query a Log Analytics workspace. |
| `resource_inventory` | Inventory Azure resources via Resource Graph. |
| `cost_optimize` | Optimize Azure costs for a service type. |

## Python service API (`src/general_ludd/azure/`)

Compatibility entry points used by older controller integrations. New resource
observation uses `azure.azcollection.azure_rm_resource_info`; audited lifecycle
execution uses `cloud.terraform` with `/usr/local/bin/tofu`.

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
- `gludd_azure_containerapp` contains only bounded, content-free lifecycle facts.
- Azure mutation modules are forbidden; MPL-2.0 OpenTofu is the sole writer.
- Saved apply plans are independently SHA-256 verified before execution.
