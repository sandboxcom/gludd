# Feature: Azure Expert Collection

**Spec ID:** AZUR-001
**Status:** In Development
**Target:** development after `v0.1.0-beta.3`
**Collection:** `general_ludd.azure`

## 1. Purpose

Gludd SHALL provide an Azure cloud operations expert that designs least-privilege
IAM roles, validates RBAC definitions against Azure schema, recommends built-in
roles for workload personas, plans VNet/Subnet/NSG network topologies, configures
Container Apps with ACR integration, queries Log Analytics and Resource Graph, and
performs cost analysis. It SHALL prefer managed identity over service principal
keys, default to least-privilege, and never emit credentials in output.

The expert SHALL produce typed, validated, JSON-serializable advisory results. It
SHALL not execute plans against live Azure APIs — it operates on local policy
files, typed contracts, and user-provided data.

## 2. Scope

### 2.1 Included Azure services

- **IAM / RBAC**: custom role definition creation and validation, built-in role
  lookup, persona-to-role mapping, assignment auditing, and managed identity
  lifecycle recommendations.
- **Networking**: VNet design with address space planning, subnet delegation
  (Container Apps, private endpoints), NSG rule generation with default-deny
  egress, public IP and NIC configuration, and private endpoint planning.
- **Container Apps**: environment configuration, container app resource
  specifications (CPU, memory, GPU), revision management (read, restart,
  activate, deactivate), secret references, and scaling rules.
- **Container Registry (ACR)**: registry SKU selection, admin user policy,
  image import, pull/push permission scoping, and listCredentials advisory.
- **Compute**: VM instance management (read, write, delete, start, restart,
  deallocate), disk provisioning, and instance view diagnostics.
- **Monitoring**: Log Analytics workspace configuration, shared key retrieval,
  diagnostic settings, KQL query generation, and timespan-based log retrieval.
- **Resource Graph**: cross-subscription resource inventory queries,
  filtering by type/region/tag, and change history tracking.
- **Cost optimization**: per-service pricing lookup, monthly estimate
  computation, region comparison, and GPU SKU cost analysis.

### 2.2 Excluded

- Direct Azure Management API calls (the module operates on contracts, not
  live infrastructure).
- Execution of Terraform/OpenTofu plans (the expert advises; deployment is
  a separate concern).
- Credential storage or secret generation.
- Billing data retrieval requiring EA/MCA portal access.
- Network throughput modeling or latency simulation.
- Azure Policy definition (separate from RBAC role definitions).

## 3. User-visible roles

| Role | Requirement |
|---|---|
| `rbac_validate` | Validate a custom role definition against Azure RBAC schema: action format, forbidden suffixes, required fields, security-critical denials in NotActions. |
| `iam_design` | Recommend built-in roles (and custom roles if necessary) for a workload persona: map to least-privilege actions, scope to subscription/resource group, prefer managed identity. |
| `iam_audit` | Enumerate existing role assignments for a subscription, flag over-privileged assignments, identify unused custom roles, and report assignments without managed identity. |
| `network_design` | Design a VNet with address space, subnets (with delegations), NSG rules (default-deny egress, explicit allows), and public IP configuration for a named workload. |
| `acr_config` | Generate ACR configuration: SKU, admin-enabled flag, region, pull/push scope, and image import plan. |
| `container_app_config` | Generate Container App deployment configuration: image, CPU, memory, GPU type, min/max replicas, environment reference, and secret mounts. |
| `query_logs` | Build and validate a KQL query against a Log Analytics workspace, with timespan and column projection. |
| `cost_optimize` | Compute estimated hourly and monthly cost for a set of Azure services in a given region using list pricing, with GPU SKU awareness. |

## 4. Required knowledge domains

### 4.1 RBAC validation (`rbac_validator.py`)

The validator SHALL:

- Enforce Azure RBAC action format: `Microsoft.<Provider>/(<ResourceType>/)*<Operation>`
  matching `(read|write|delete|action)` suffixes.
- Reject forbidden suffix patterns: `/list/action`, `/get/action`, `/create/action`,
  `/update/action` — with explanations for each.
- Require `Name`, `Description`, `Actions`, `NotActions`, `AssignableScopes` fields.
- Flag missing security-critical `NotActions` entries: `runCommand/action`,
  `runCommands/*`, `roleAssignments/write`, `roleAssignments/delete`,
  `roleDefinitions/write`, `roleDefinitions/delete`.
- Detect duplicate actions and warn on short descriptions (<20 chars).

### 4.2 IAM design (`iam_advisor.py`)

The IAM advisor SHALL:

- Map common workload personas (`container-app-developer`, `data-scientist`,
  `infra-admin`, `read-only-auditor`, `devops-engineer`) to Azure built-in
  roles via `PERSONA_ROLE_MAP`.
- Scope assignments to subscription or resource group level.
- Prefer managed identity over service principal with client secret; emit
  a warning when a service principal is unavoidable.
- Recommend custom role creation only when no built-in role satisfies the
  required action set, and include the exact action diff.

### 4.3 Network design (`network_designer.py`)

The network designer SHALL:

- Plan IPv4 address spaces in RFC 1918 ranges with at least a /16 VNet.
- Generate subnets sized for Container Apps delegation (`/23` minimum),
  private endpoints (`/28`), and AzureFirewall/bastion where specified.
- Assign `Microsoft.App/environments` delegation to Container Apps subnets.
- Generate NSG rules: default-deny inbound from internet, default-deny
  outbound, explicit allows for required Azure service tags and FQDNs.
- Include public IP and NIC configuration when internet-facing endpoints
  are required.

### 4.4 Container Apps and ACR (`core.py`)

The container advisor SHALL:

- Map workload requirements (CPU cores, memory GiB, GPU type) to valid
  Azure Container App SKU combinations.
- Configure ACR SKU (Basic/Standard/Premium) based on throughput and
  geo-replication needs.
- Set `admin_enabled: false` by default; require explicit opt-in.
- Scope ACR permissions to `pull/read` for runtime and `push/write` only
  for build identities.
- Include revision management actions (restart, activate, deactivate) in
  the role definition.
- Generate `listSecrets/action` permission only when explicitly required.

### 4.5 Cost optimization (`core.py`)

The cost advisor SHALL:

- Look up list pricing for compute (VM SKUs, Container App vCPU/GPU),
  networking (public IP, NAT Gateway, VNet peering), ACR (SKU-based),
  and Log Analytics (ingestion + retention).
- Compute `hourly_rate` and `monthly_estimate` (730 hours/month).
- Flag services with significant GPU cost impact.
- Disclaim that estimates exclude enterprise agreements, reservations,
  spot pricing, and Azure Hybrid Benefit.

## 5. Interfaces and data contracts

All contracts are defined in `src/general_ludd/azure/contracts.py` as typed
dataclasses with `field(default_factory=list)` for collection fields.

### 5.1 `AzureRbacRole`

```text
name: str
description: str
actions: list[str]           # allowed operations
not_actions: list[str]       # explicitly denied operations
data_actions: list[str]      # data-plane actions
assignable_scopes: list[str] # subscription/RG scopes
```

### 5.2 `IamAssignment`

```text
persona: str                 # workload persona key
role_name: str               # Azure role name
scope: str                   # assignment scope
is_builtin: bool = True      # built-in vs custom
```

### 5.3 `NetworkDesign`

```text
vnet_name: str
address_space: str           # CIDR block
subnets: list[dict[str, str]]  # [{name, cidr, delegation}]
nsg_rules: list[dict[str, str]]  # [{name, priority, direction, ...}]
```

### 5.4 `AcrConfig`

```text
name: str
sku: str                     # Basic / Standard / Premium
admin_enabled: bool = False
region: str = ""
```

### 5.5 `ContainerAppDeployConfig`

```text
name: str
image: str                   # fully-qualified ACR image path
cpu: str                     # e.g. "2.0"
memory: str                  # e.g. "4Gi"
gpu_type: str = ""           # empty or "nvidia"
min_replicas: int = 0
```

### 5.6 `LogAnalyticsQuery`

```text
workspace_id: str
query: str                   # KQL query string
timespan: str = "P1D"        # ISO 8601 duration
```

### 5.7 `PricingResult`

```text
service_type: str
region: str
hourly_rate: float
monthly_estimate: float      # hourly_rate * 730
```

## 6. Safety and security

### AZUR-SAFE-001: No credentials in output

The expert SHALL never emit connection strings, access keys, client secrets,
certificates, or shared keys as plaintext in any result. Managed identity is
the preferred authentication mechanism; when a service principal is necessary,
only the App ID is surfaced with a warning.

### AZUR-SAFE-002: Least-privilege by default

Every role recommendation SHALL start from the narrowest action set and expand
only with explicit, documented justification. Built-in roles are preferred over
custom definitions. A recommended custom role SHALL include the exact diff from
the nearest built-in role.

### AZUR-SAFE-003: Security-critical denials

The following actions SHALL appear in `NotActions` for every custom role
that includes compute or authorization actions:

- `Microsoft.Compute/virtualMachines/runCommand/action`
- `Microsoft.Compute/virtualMachines/runCommands/*`
- `Microsoft.Authorization/roleAssignments/write`
- `Microsoft.Authorization/roleAssignments/delete`
- `Microsoft.Authorization/roleDefinitions/write`
- `Microsoft.Authorization/roleDefinitions/delete`

The `rbac_validator` SHALL reject roles missing these denials when the
corresponding `Actions` include `Microsoft.Compute/*` or
`Microsoft.Authorization/*`.

### AZUR-SAFE-004: Network default-deny

NSG rule generation SHALL default to deny-all inbound from internet and
deny-all outbound. Explicit allow rules are added only for documented
Azure service tags and workload-specific FQDNs.

### AZUR-SAFE-005: Fail closed on validation

Invalid action format, missing required fields, forbidden suffix patterns,
missing security-critical denials, and empty action lists SHALL all result
in validation failure. Warnings (short descriptions, duplicate actions,
non-subscription scopes) SHALL not block validation.

## 7. Integration with existing artifacts

### 7.1 IAM policy validator

`scripts/validate_azure_iam_policy.py` validates `config/infra/azure-iam-policy.json`
against Azure RBAC schema. The `rbac_validator.py` module mirrors this logic in
importable form so the same checks are available both at CI time and as a library.

The two validators SHALL remain in sync: any new check added to one must be
added to the other, and both SHALL be covered by the same test fixture set.

### 7.2 Reference IAM policy

`config/infra/azure-iam-policy.json` serves as the canonical example of a
least-privilege Container App Deployer role. Every RBAC validation test SHALL
use this file as a golden positive case.

### 7.3 Ansible collection

The ansible collection at `collections/ansible_collections/general_ludd/azure/`
wraps the Python modules and carries no independent Azure logic. Roles SHALL
accept the same typed contracts as kwargs and SHALL return structured JSON
results compatible with ansible module output.

## 8. Implementation layout

```text
src/general_ludd/azure/
├── __init__.py              # public API surface
├── contracts.py             # typed dataclasses
├── core.py                  # role router + domain functions
├── iam_advisor.py           # PERSONA_ROLE_MAP, recommend_roles_for_persona
├── network_designer.py      # design_vnet, generate_nsg_rules
└── rbac_validator.py        # validate_action_string, check_security_critical_denials

collections/ansible_collections/general_ludd/azure/
├── galaxy.yml
├── README.md
└── roles/
    ├── rbac_validate/
    ├── iam_design/
    ├── iam_audit/
    ├── network_design/
    ├── acr_config/
    ├── container_app_config/
    ├── query_logs/
    └── cost_optimize/

scripts/
└── validate_azure_iam_policy.py    # CI-side policy validator

config/infra/
└── azure-iam-policy.json           # reference Container App Deployer role

tests/
├── unit/azure/test_contracts.py
├── unit/azure/test_rbac_validator.py
├── unit/azure/test_iam_advisor.py
├── unit/azure/test_network_designer.py
├── unit/azure/test_core.py
└── unit/test_validate_azure_iam_policy.py
```

## 9. Measurable acceptance tests

### AZUR-AT-001: RBAC action validation

At least 200 test cases covering valid actions, invalid formats, forbidden
suffixes, duplicate detection, and edge cases (empty string, non-ASCII, missing
provider). The golden `azure-iam-policy.json` SHALL pass with zero errors.

### AZUR-AT-002: Security-critical denials

For every role that includes `Microsoft.Compute/*` or `Microsoft.Authorization/*`
actions, missing `runCommand`, `roleAssignments/write`, `roleAssignments/delete`,
`roleDefinitions/write`, or `roleDefinitions/delete` in `NotActions` SHALL produce
a validation error.

### AZUR-AT-003: Persona-to-role mapping

Every persona key in `PERSONA_ROLE_MAP` SHALL map to at least one built-in role.
Unknown personas SHALL return an empty list with a diagnostic message, not raise.

### AZUR-AT-004: Network design

Golden tests covering /16, /20, and /24 VNets with 2–6 subnets SHALL produce
non-overlapping CIDR blocks and valid delegation assignments. NSG default-deny
rules SHALL precede explicit allows in priority order.

### AZUR-AT-005: Cost estimates

Compute cost estimates for at least 10 common Container App SKU combinations
(including GPU) SHALL produce `monthly_estimate` within 5% of the Azure Pricing
Calculator for the same region and SKU.

### AZUR-AT-006: Contract serialization

All seven dataclasses SHALL round-trip through `dataclasses.asdict` / JSON
serialization without loss. Optional fields with defaults SHALL not appear
as `null` in serialized output.

### AZUR-AT-007: No credential leakage

Property-based tests SHALL generate 1,000 arbitrary result objects and assert
no field contains a pattern matching a connection string, access key, client
secret, or certificate PEM.

### AZUR-AT-008: CI integration

`scripts/validate_azure_iam_policy.py` SHALL exit 0 on the reference policy
file and exit 1 on intentionally broken variants. It SHALL be wired into
`make gate` via a dedicated target (`make validate-azure-iam`).

### AZUR-AT-009: Quality gate

- Overall changed-code coverage SHALL be at least 85%.
- Every changed production file SHALL be at least 75%.
- Unit and integration suites SHALL pass with warnings treated as errors.
- Lint (`ruff`), typecheck (`mypy`), and security scan (`bandit`) SHALL pass.

## 10. Expected test count

~100+ unit tests across:

| Module | Approximate tests |
|---|---|
| `test_rbac_validator.py` | 40 (action format, forbidden suffixes, security denials, edge cases) |
| `test_iam_advisor.py` | 20 (persona mapping, scope assignment, built-in vs custom) |
| `test_network_designer.py` | 20 (CIDR allocation, subnet sizing, NSG rules) |
| `test_core.py` | 15 (role routing, acr_config, container_app_config, cost, log query) |
| `test_contracts.py` | 10 (serialization, defaults, field validation) |
| `test_validate_azure_iam_policy.py` | 10 (golden file, error variants, integration) |

## 11. Definition of done

The feature is implemented only when all AZUR acceptance tests are automated,
the RBAC validator and its CI-side script agree on every check, the `PERSONA_ROLE_MAP`
covers all documented personas, credential-free output is mechanically enforced,
and the full project gate is green on the exact commit proposed for merge.
