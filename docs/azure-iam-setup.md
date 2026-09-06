# Azure IAM Setup for General Ludd Agent

This guide covers three topics:

1. The least-privilege Azure IAM role definition
2. How to create and assign the role in the Azure Portal
3. How to configure the agent to use the IAM role for Terraform deployment

## 1. Role Definition

The custom role `General Ludd Accelerator Deployer` is defined in
`config/infra/azure-iam-policy.json`. It grants the minimum permissions needed
to deploy and destroy Container Apps (with GPU inference), Container Registries,
Virtual Networks, Subnets, and Resource Groups via Terraform/OpenTofu.

### What the role permits

| Resource | Actions |
|----------|---------|
| Resource Groups | Create, read, update, delete |
| Container Registry | Create, read, delete, list credentials, push/pull |
| Container App Environment | Create, read, delete, manage storages |
| Container App | Create, read, delete, list secrets, manage revisions |
| Virtual Networks | Create, read, delete, manage subnets |
| Network Security Groups | Create, read, delete, manage rules |
| Public IPs | Create, read, delete, join |
| Network Interfaces | Create, read, delete, join |
| Virtual Machines | Create, read, delete, start, restart, deallocate |
| Disks | Create, read, delete |
| Deployments | Create, read, delete |
| Tags | Read, write |
| Diagnostics & Logs | Read, write diagnostic settings |

### What the role explicitly denies

- No role assignment management (cannot grant permissions to others)
- No resource group move operations
- No ACR build queueing
- No VM run commands (prevents arbitrary script execution on VMs)

### One-line Terraform accelerator role creation

This is the role used by Gludd's Terraform/OpenTofu paths for Azure Container
Apps and VM-backed GPU workers. It does not grant any Cognitive Services or
Azure OpenAI permission. Replace the example subscription ID, then run this
single line from the repository root:

The Azure CLI session running this one-time bootstrap must already have
`Microsoft.Authorization/roleDefinitions/write` at the subscription scope.
Of Azure's built-in roles, `Owner` and `User Access Administrator` provide that
permission; `Contributor` and `Role Based Access Control Administrator` do not.
An existing subscription administrator can run the Gludd command directly, or
temporarily grant the operator `User Access Administrator`:

```bash
az role assignment create --assignee-object-id <operator-object-id> --assignee-principal-type User --role "User Access Administrator" --scope "/subscriptions/<subscription-id>"
```

That grant command must itself be run by an identity that can write role
assignments. If Azure reports `AuthorizationFailed` for
`Microsoft.Authorization/roleDefinitions/write`, the rendered role reached
Azure correctly but the current identity lacks this prerequisite. Activate an
eligible privileged role if PIM is in use, refresh the Azure CLI login if
needed, wait for RBAC propagation, and retry. See Microsoft's
[custom-role troubleshooting guidance][azure-rbac-troubleshoot] and current
[privileged built-in role definitions][azure-privileged-roles].

```bash
make --no-print-directory azure-accelerator-role-args AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 | xargs -0 az
```

The Make target validates the UUID, reads the checked-in
`config/infra/azure-iam-policy.json`, verifies that its canonical name is
`General Ludd Accelerator Deployer`, rejects any Cognitive Services action,
materializes the exact subscription scope, and emits one NUL-delimited
`az role definition create` argument vector. Make never authenticates to Azure
and Azure CLI receives the role JSON as one argument, avoiding the long-lived
shell quoting and newline failures reported in [Azure CLI #16940][azure-cli-16940].

#### Provider-registration actions

Azure validates every custom-role action against its current provider-operation
catalog during role creation. The generic-looking
`Microsoft.Resources/subscriptions/providers/register/action` is not a supported
operation and Azure rejects the entire role with `InvalidActionOrNotAction`.
Gludd instead grants only the provider-owned registration actions needed by its
deployment paths: `Microsoft.App/register/action`,
`Microsoft.ContainerRegistry/register/action`, `Microsoft.Compute/register/action`,
`Microsoft.Network/register/action`, `Microsoft.OperationalInsights/register/action`,
and `Microsoft.Insights/register/action`. These are listed in Microsoft's current
[compute][azure-compute-permissions], [container][azure-container-permissions],
[network][azure-network-permissions], and [monitor][azure-monitor-permissions]
permission catalogs.

This distinction has caused years of operator confusion. A 2021 Azure Q&A report
shows a real deployment requesting `Microsoft.Network/register/action` at
subscription scope ([network registration thread][forum-network-register]), while
a 2026 answer still recommends the unsupported generic Microsoft.Resources action
([generic registration thread][forum-generic-register]). The checked-in validator
and renderer therefore pin the provider-specific operations and reject the generic
string before emitting any Azure CLI arguments. When Azure introduces another
deployment dependency, verify its exact operation in the
[provider-operation catalog][azure-provider-operations] rather than generalizing
the resource path.

After Azure reports that role creation succeeded, create a unique test service
principal and write its one-time credential JSON directly to a private file:

```bash
(umask 077; make --no-print-directory azure-accelerator-auth-args AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_ACCELERATOR_SP_NAME=gludd-accelerator-20260905 | xargs -0 az > /tmp/gludd-azure-accelerator-auth.json)
```

The second target assigns only `General Ludd Accelerator Deployer` at the
subscription scope required for Gludd to create and remove its own resource
groups. The JSON file contains the one-time Entra credential and must remain
outside the repository. It is not a sourceable dotenv file; an operator or
secret-injection workflow maps its `clientId`, `tenantId`, and `clientSecret`
fields to `ARM_CLIENT_ID`, `ARM_TENANT_ID`, and `ARM_CLIENT_SECRET`, alongside
the explicit `ARM_SUBSCRIPTION_ID`.

Role definition creation uses Azure Resource Manager, while application and
service-principal creation uses Microsoft Graph. Azure CLI cannot perform both
mutations in one invocation, so these remain two explicit, independently
auditable one-line commands. Neither target accepts or logs a credential.

After the role and test principal have been created, an administrator should
remove any temporary bootstrap grant; this does not remove the new limited
Gludd role or its assignment:

```bash
az role assignment delete --assignee-object-id <operator-object-id> --role "User Access Administrator" --scope "/subscriptions/<subscription-id>"
```

### Updating an existing role

If the custom role already exists and you need to update its permissions,
use `az role definition update` instead of `create`:

```bash
# Substitute the subscription ID into the policy and update the role
az role definition update --role-definition "$(sed "s/{subscription_id}/$SUBSCRIPTION_ID/" config/infra/azure-iam-policy.json)"
```

This uses the same `config/infra/azure-iam-policy.json` file (PascalCase keys)
as the `create` command. The role name (`"General Ludd Accelerator Deployer"`)
must match the existing role exactly.

### Azure OpenAI self-improvement identity

The deployment role above is deliberately not the role used for model
self-improvement. Before issuing a credential, install or update one custom role
named `Gludd Azure OpenAI Self Improvement - <account>` with an assignable scope
limited to the resource group that owns the account. The canonical
`config/infra/azure-self-improve-role.json` template must be materialized
outside the repository with all three brace-delimited identifiers replaced
before following the create/update procedure. Its permission document is
intentionally only:

```json
{
  "Actions": [
    "Microsoft.CognitiveServices/accounts/read",
    "Microsoft.CognitiveServices/accounts/deployments/read"
  ],
  "NotActions": [],
  "DataActions": [
    "Microsoft.CognitiveServices/accounts/OpenAI/responses/write"
  ],
  "NotDataActions": []
}
```

Azure represents a role definition as a management-group or subscription-level
resource; it does not store a role definition on an individual Azure OpenAI
account. The [Azure custom-role scope rules][azure-custom-role-scope] permit
custom-role assignments at management-group, subscription, and resource-group
scopes. Because this role includes a `DataActions` permission, it must not be
assigned at management-group scope. The custom role is assignable at the exact
resource-group scope:

```text
/subscriptions/<subscription-id>/resourceGroups/<resource-group>
```

That definition boundary does not broaden the generated identity. The service
principal role assignment remains narrowed to the exact account resource:

```text
/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<account>
```

This role cannot list or regenerate account keys, change or delete deployments,
read or delete Responses API objects, read stored completions, or administer
RBAC. Microsoft lists `accounts/read`, `accounts/deployments/read`, and
`accounts/OpenAI/responses/write` separately in the
[Azure AI permission catalog][azure-ai-permissions]. Follow Microsoft's
[custom-role create/update procedure][azure-custom-role-cli] to install this
role before running the credential pipeline below. Role installation requires
an operator identity with role-definition administration; that privilege is
never granted to the generated identity.

#### One Azure invocation that writes the private credential

Use a new, unique service-principal name. The Make target validates all values
and writes only NUL-delimited Azure CLI arguments. `xargs -0` preserves spaces
as data and invokes Azure CLI once. Azure creates the Entra application/service
principal, assigns the already-installed custom role at the exact account
scope, and writes its credential JSON directly to a mode-0600-capable file.
This follows Microsoft's warning that
[`az ad sp create-for-rbac` output must be protected][azure-cli-ad-sp]; the
command receives both the custom role and exact resource scope explicitly.

```bash
(umask 077; make --no-print-directory azure-self-improve-auth-args AZURE_SELF_IMPROVE_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_SELF_IMPROVE_RESOURCE_GROUP=gludd-models AZURE_SELF_IMPROVE_ACCOUNT=gludd-self-improve AZURE_SELF_IMPROVE_SP_NAME=gludd-self-improve-20260905 | xargs -0 az > /tmp/gludd-azure-self-improve-auth.json)
```

Replace all four example values. Keep the output outside the repository and
send only its path through an approved private channel. The JSON is not a shell
file and must not be sourced. It contains the Azure CLI `--json-auth` fields.
The operator or secret-injection workflow must read `clientId`, `tenantId`, and
`clientSecret` from that protected JSON and provide them to Gludd as
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_CLIENT_SECRET`. The current
runtime does not load this JSON file directly. The non-secret live-candidate
configuration must also provide the endpoint
`https://<account>.openai.azure.com`, API family `azure_openai`, API version
`v1`, subscription, resource group, account, and deployment name.

A literal `make ... | az ad sp create-for-rbac ...` cannot work: that Azure CLI
command ignores stdin and accepts its role, scope, and name as argv. The
NUL-delimited target emits the complete `ad sp create-for-rbac` argv, including
`--json-auth true`; the `xargs` bridge is the closest safe single-pipeline form.
If validation fails before output, `xargs` can at most invoke bare `az`, never an
unscoped principal-creation command. One Azure
CLI invocation also cannot create or update a custom role and create an Entra
application/service principal: role definitions and assignments use Azure
Resource Manager while application creation uses Microsoft Graph. Combining
those mutations behind Make would hide authority and rollback boundaries, so
the custom role is an explicit prerequisite.

#### ZDD, rollback, and operational bounds

- Wait until `az role definition list --name "Gludd Azure OpenAI Self
  Improvement - <account>" --scope <exact-account-resource-id>` returns exactly
  one role before issuing a credential. The pipeline starts no background work
  and runs one foreground Azure CLI process against the explicit subscription.
- Never rerun with the same service-principal name after an ambiguous failure.
  First inspect that exact display name and scope. Delete only the newly created
  application with `az ad app delete --id <clientId>` before retrying with a new
  name. Removing the application revokes the generated credential and its role
  assignment; deleting or rolling back the custom role remains a separate,
  operator-visible change.
- Azure CLI issue [#31995][azure-cli-31995] records `role assignment create`
  returning `RoleAssignmentExists` instead of behaving idempotently. Issue
  [#31579][azure-cli-31579] records an unexpected Microsoft Graph lookup after
  that collision, even with an object ID and principal type. A unique principal
  per attempt and inspect-before-retry policy avoids treating a failed retry as
  harmless.
- Azure CLI issue [#16940][azure-cli-16940] shows long-lived quoting and newline
  failures around inline role JSON. NUL-delimited argv prevents shell word
  splitting, while role creation/update stays in the documented file-based
  custom-role workflow.

## 2. Creating and Assigning the Role in Azure Portal

Two policy files exist because the Azure Portal JSON editor and the Azure CLI
expect different formats for the same role definition. Both files contain
identical permissions — the only difference is the JSON structure.

| File | Format | Use with |
|------|--------|----------|
| `config/infra/azure-iam-policy-cli.json` | REST API (`properties` / `permissions` wrapper) | **Azure Portal Web UI** — paste into JSON tab |
| `config/infra/azure-iam-policy.json` | PascalCase flat keys (`Name`, `Actions`, ...) | **Azure CLI** — `az role definition create` |

### Step 1a: Create the custom role via Azure Portal (Web UI)

Use `config/infra/azure-iam-policy-cli.json` — this file uses the REST API
format that the Portal JSON editor expects.

1. Sign in to the [Azure Portal](https://portal.azure.com).
2. Search for **Subscriptions** and select your target subscription.
3. Click **Access control (IAM)** in the left sidebar.
4. Click **+ Add** → **Add custom role**.
5. Switch to **JSON** tab and click **Edit**.
6. Replace the JSON with the contents of `config/infra/azure-iam-policy-cli.json`.
7. Replace `"{subscription_id}"` in `assignableScopes` with your
   actual subscription ID (found on the Subscription overview page).
8. Click **Save** → **Review + create** → **Create**.

### Step 1b: Create the custom role via Azure CLI

Use `config/infra/azure-iam-policy.json` — this file uses PascalCase keys
that `az role definition create` expects. The `--role-definition` flag
accepts inline JSON (do NOT use `@file` syntax with this format).

```bash
# Get your subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Substitute the subscription ID into the policy and create the role
# Use inline JSON (not @file) — the CLI expects PascalCase keys for --role-definition
az role definition create --role-definition "$(sed "s/{subscription_id}/$SUBSCRIPTION_ID/" config/infra/azure-iam-policy.json)"
```

### Step 2: Create a managed identity or service principal

**Option A: Managed Identity (recommended for production)**

1. In the Azure Portal, go to the resource where the agent runs (e.g., a VM
   or Container App).
2. Under **Settings**, click **Identity**.
3. Set **Status** to **On** and click **Save**.
4. Copy the **Principal ID** (you'll need it for role assignment).

**Option B: Service Principal (for local dev or CI)**

```bash
# Create a service principal
az ad sp create-for-rbac \
  --name "gludd-deployer" \
  --create-cert \
  --role "General Ludd Accelerator Deployer" \
  --scopes "/subscriptions/$SUBSCRIPTION_ID"

# Or with a client secret:
az ad sp create-for-rbac \
  --name "gludd-deployer" \
  --sdk-auth \
  --role "General Ludd Accelerator Deployer" \
  --scopes "/subscriptions/$SUBSCRIPTION_ID"
```

### Step 3: Assign the role

1. In the Azure Portal, go to **Subscriptions** → your subscription.
2. Click **Access control (IAM)**.
3. Click **+ Add** → **Add role assignment**.
4. Search for **General Ludd Accelerator Deployer** and select it.
5. Under **Members**, click **+ Select members**.
6. Search for your managed identity or service principal and select it.
7. Click **Review + assign**.

CLI equivalent:

```bash
# Get the principal ID of your managed identity or service principal
PRINCIPAL_ID=$(az ad sp list --display-name "gludd-deployer" --query '[].id' -o tsv)

# Assign the role
az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "General Ludd Accelerator Deployer" \
  --scope "/subscriptions/$SUBSCRIPTION_ID"
```

### Step 4: Record the credentials

For a service principal with client secret, record these four values:

| Environment Variable | Source |
|---------------------|--------|
| `ARM_SUBSCRIPTION_ID` | Subscription ID from Azure Portal |
| `ARM_TENANT_ID` | Tenant ID from Azure Portal or `az account show` |
| `ARM_CLIENT_ID` | App ID of the service principal |
| `ARM_CLIENT_SECRET` | Client secret generated during SP creation |

For a managed identity on an Azure VM/Container App, no credentials are needed
— the managed identity is used automatically by the Azure SDK. Set
`ARM_USE_MSI=true` and `ARM_SUBSCRIPTION_ID` only.

#### Automated env file creation (az CLI)

Run these commands to query Azure and create `/etc/general-ludd/env`:

**Service Principal with client secret:**

```bash
sudo mkdir -p /etc/general-ludd

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

az ad sp create-for-rbac \
  --name "gludd-deployer" \
  --role "General Ludd Accelerator Deployer" \
  --scopes "/subscriptions/$SUBSCRIPTION_ID" \
  --output json \
  | sudo tee -a /tmp/gludd-sp-credentials.tmp > /dev/null

CLIENT_ID=$(jq -r .appId /tmp/gludd-sp-credentials.tmp)
CLIENT_SECRET=$(jq -r .password /tmp/gludd-sp-credentials.tmp)

sudo tee /etc/general-ludd/env > /dev/null <<GLUDD_EOF
# General Ludd Azure authentication — generated $(date -u +%Y-%m-%dT%H:%M:%SZ)
ARM_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
ARM_TENANT_ID=$TENANT_ID
ARM_CLIENT_ID=$CLIENT_ID
ARM_CLIENT_SECRET=$CLIENT_SECRET
GLUDD_EOF

sudo chmod 600 /etc/general-ludd/env
rm -f /tmp/gludd-sp-credentials.tmp

echo "/etc/general-ludd/env created with mode 600"
```

**Managed Identity (Azure VM / Container App):**

```bash
sudo mkdir -p /etc/general-ludd

SUBSCRIPTION_ID=$(az account show --query id -o tsv)

sudo tee /etc/general-ludd/env > /dev/null <<GLUDD_EOF
# General Ludd Azure authentication — managed identity $(date -u +%Y-%m-%dT%H:%M:%SZ)
ARM_USE_MSI=true
ARM_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
GLUDD_EOF

sudo chmod 600 /etc/general-ludd/env
echo "/etc/general-ludd/env created with mode 600"
```

**Application registered in Entra ID (existing SP, manual credential):**

```bash
sudo mkdir -p /etc/general-ludd

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

sudo tee /etc/general-ludd/env > /dev/null <<GLUDD_EOF
# General Ludd Azure authentication — Entra ID application $(date -u +%Y-%m-%dT%H:%M:%SZ)
ARM_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
ARM_TENANT_ID=$TENANT_ID
ARM_CLIENT_ID=<your-app-id>
ARM_CLIENT_SECRET=<your-client-secret>
GLUDD_EOF

sudo chmod 600 /etc/general-ludd/env
echo "/etc/general-ludd/env created — update ARM_CLIENT_ID and ARM_CLIENT_SECRET"
```

## 3. Configuring the Agent

The General Ludd agent uses `DeploymentManager` to run Terraform/OpenTofu
lifecycle operations (`init`, `apply`, `destroy`). Authentication credentials
are injected as environment variables before each Terraform invocation.

### Option A: Environment variables (simplest)

Set the ARM variables in `/etc/general-ludd/env` or your shell environment:

```bash
# /etc/general-ludd/env
ARM_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ARM_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ARM_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ARM_CLIENT_SECRET=your-client-secret

# For managed identity auth, use instead:
# ARM_USE_MSI=true
# ARM_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

The agent's `DeploymentManager` reads these from the environment and passes them
through to Terraform subprocess calls.

**Using a non-standard env file path** (e.g. `/tmp/general-ludd.env`):

```bash
# Source the env file to export variables into the current shell
source /tmp/general-ludd.env

# Or for temporary testing, source + run in one command:
source /tmp/general-ludd.env && python -m general_ludd.daemon

# For the daemon via systemd with a custom path, edit the unit override:
sudo mkdir -p /etc/systemd/system/general-ludd.service.d
sudo tee /etc/systemd/system/general-ludd.service.d/env-file-override.conf > /dev/null <<EOF
[Service]
EnvironmentFile=-/tmp/general-ludd.env
EOF
sudo systemctl daemon-reload
```

**Testing gludd against Azure** (local checkout, non-systemd):

The repository's full-provision harness sources the credential file without
printing its contents. Validate the path without creating resources first, then
run the costly test explicitly:

```bash
make test-e2e-azure-provision-sourced AZURE_E2E_ENV_FILE=/tmp/general-ludd.env AZURE_E2E_VALIDATE_ONLY=1
make test-e2e-azure-provision-sourced AZURE_E2E_ENV_FILE=/tmp/general-ludd.env AZURE_E2E_VALIDATE_ONLY=0
```

The default `AZURE_E2E_ENV_FILE` is `/tmp/general-ludd.env`; override it for a
different operator-managed path. An unreadable path fails before Terraform is
invoked.

After any interrupted provision run, use the same credential pointer to request
deletion and wait for Azure to report that every E2E resource group is absent:

```bash
make azure-cleanup-e2e AZURE_E2E_ENV_FILE=/tmp/general-ludd.env AZURE_CLEANUP_TIMEOUT_SECS=900 AZURE_CLEANUP_POLL_SECS=10 AZURE_CLI=az
```

The target only selects resource groups whose names start with `gludd-gpu`. It
prints `CLEANUP_SCAN`, every deletion request, and a `CLEANUP_POLL` heartbeat on
each query. It succeeds only after printing
`CLEANUP_VERIFIED leaked_resources=0`; an Azure query/delete failure or timeout
returns nonzero. The credential file is sourced without printing its values.

This verified-absence rule is intentional: operators have reported both
[long-running Azure deallocation][forum-vm-deallocating] and Azure objects left
outside Terraform state after failed applies
([research and acceptance contract][azure-terraform-e2e-research]). The cleanup
target is a narrow E2E safety net; it does not replace Terraform/ARM ownership
reconciliation for production resources.

```bash
# 1. Source your Azure credentials
source /tmp/general-ludd.env

# 2. Set the config directory (required when running from a repo checkout)
export GLUDD_CONFIG_DIR="$PWD/config"

# 3. Run a smoke test
make smoke

# 4. Run Azure-specific IAM tests
make validate-azure-iam
make test TESTFILE='tests/unit/test_validate_azure_iam_policy.py'

# 5. Full Azure E2E test (auto-provision GPU → test → destroy)
make test-e2e-azure-provision
```

[azure-terraform-e2e-research]: research/AZURE_TERRAFORM_EVENT_ELASTICITY_E2E_EVIDENCE.md
[forum-vm-deallocating]: https://learn.microsoft.com/en-us/answers/questions/261/trending-on-msdn-virtual-machine-stuck-in-dealloca

### Option B: Secret aliases (recommended for production)

If using OpenBao/Vault for secrets management, reference the credentials by
alias rather than storing them in plaintext:

In your `ComputeConfig`, set `provider_auth_aliases`:

```python
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

config = ComputeConfig(
    provider=ComputeProvider.AZURE,
    gpu_type=GPUType.T4,
    model_name="meta-llama/Meta-Llama-3-8B-Instruct",
    deploy_type="containerapp",
    region="eastus",
    provider_auth_aliases={
        "ARM_SUBSCRIPTION_ID": "AZURE_SUBSCRIPTION_ID",
        "ARM_TENANT_ID": "AZURE_TENANT_ID",
        "ARM_CLIENT_ID": "AZURE_CLIENT_ID",
        "ARM_CLIENT_SECRET": "AZURE_CLIENT_SECRET",
    },
)
```

The `DeploymentManager` will resolve these aliases via the configured
`SecretsManager` (OpenBao) or `EnvSecretsManager` (env vars) before running
Terraform, and will clean them up afterward.

### Option C: Managed Identity (Azure-hosted agents)

If the agent runs on an Azure VM or Container App with a system-assigned or
user-assigned managed identity:

```python
config = ComputeConfig(
    provider=ComputeProvider.AZURE,
    gpu_type=GPUType.T4,
    model_name="meta-llama/Meta-Llama-3-8B-Instruct",
    deploy_type="containerapp",
    region="eastus",
    provider_auth_aliases={
        "ARM_USE_MSI": "AZURE_USE_MSI",
        "ARM_SUBSCRIPTION_ID": "AZURE_SUBSCRIPTION_ID",
    },
)
```

The managed identity must have the **General Ludd Accelerator Deployer** role
assigned (see Step 3 above).

### Deploying

```python
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.secrets.env import EnvSecretsManager

secrets = EnvSecretsManager()  # or SecretsManager for OpenBao
mgr = DeploymentManager(secrets_resolver=secrets)

instance = await mgr.deploy(config)
print(f"API endpoint: {instance.endpoint_url}")

# ... use the inference endpoint ...

# Destroy when done
await mgr.destroy(instance.instance_id)
```

### Getting the API URL

After `deploy()` completes, the `ComputeInstance.endpoint_url` field contains
the URL to access the API service. For Container Apps, this is the
`latest_revision_fqdn` output from Terraform, formatted as:

```text
https://gpu-inference-<revision>.<region>.azurecontainerapps.io
```

The inference API is available at:

```text
GET  https://gpu-inference-<revision>.<region>.azurecontainerapps.io/v1/models
POST https://gpu-inference-<revision>.<region>.azurecontainerapps.io/v1/chat/completions
```

## 4. Azure Game E2E Smoke Tests

The project includes a full E2E test that provisions Azure GPU compute (A100 or
H100), runs an LLM to generate game code (Doom-like hallway, Quake-like arena),
runs the generated game headless, captures frames, and compares them against
reference gameplay using SSIM similarity metrics. All inference runs exclusively
on Azure GPU resources.

### Prerequisites

- Azure subscription with GPU quota (NCasT4_v3, NC_A100_v4, or ND_H100_v5)
- `General Ludd Accelerator Deployer` custom role created and assigned
- `/tmp/general-ludd.env` (or another explicit `AZURE_E2E_ENV_FILE`) readable
  with Azure credentials; keep this file outside the repository

### Quick start — pre-provisioned endpoint

```bash
source /tmp/general-ludd.env
export GLUDD_CONFIG_DIR="$PWD/config"
export AZURE_BASE_URL="https://gpu-inference-xxx.eastus.azurecontainerapps.io/v1"
export AZURE_MODEL="Qwen/Qwen2.5-Coder-7B-Instruct"
make test-e2e-games
```

### Full provision — deploy GPU on-demand

Acquire the approved reference windows before any Azure resource can be
created, then verify the exact cache with network access disabled:

```bash
make game-reference-preflight \
  GAME_E2E_REFERENCE_NETWORK=1 \
  GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e \
  GAME_E2E_REFERENCE_VALIDATE_ONLY=0

make game-reference-preflight \
  GAME_E2E_REFERENCE_NETWORK=0 \
  GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e \
  GAME_E2E_REFERENCE_VALIDATE_ONLY=0

make test-e2e-games-provision \
  AZURE_E2E_ENV_FILE=/tmp/general-ludd.env \
  AZURE_E2E_VALIDATE_ONLY=0 \
  GAME_E2E_TIMEOUT_SECS=3600 \
  GAME_E2E_REFERENCE_NETWORK=0 \
  GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e \
  GLUDD_E2E_MAX_SPEND_USD=5
```

The target sources only the explicit env file, streams live provisioning/test
events to the console and audit log, and enforces a configurable wall-clock and
per-test timeout of at least 3600 seconds. Validate the file and arguments without
provisioning by changing `AZURE_E2E_VALIDATE_ONLY=1`; that mode does not replace
the offline media preflight or authenticate to Azure.

The run records its computed Azure estimate and refuses a plan above
`GLUDD_E2E_MAX_SPEND_USD`; do not substitute a static cost guess for that
SKU-, region-, and duration-specific evidence. Timestamped logs and result JSON
are retained under `.gate-logs/e2e-azure/games-provision-*`. On a reference
failure, `azure_game_preflight_failed` must appear and
`azure_game_deploy_started` must not. After any paid or interrupted run, retain
independent `CLEANUP_VERIFIED leaked_resources=0` evidence. The exact event
sequence, cleanup commands, destructive prefix scope, official Azure deletion
semantics, and long-lived operator reports are documented in the
[FPS game E2E reliability runbook][fps-game-runbook].

### Available Make targets

| Target | What it does |
|---|---|
| `make test-e2e-games` | Game E2E via pre-provisioned Azure endpoint (AZURE_BASE_URL). Skips if unset. |
| `make game-reference-preflight GAME_E2E_REFERENCE_NETWORK=0 GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e GAME_E2E_REFERENCE_VALIDATE_ONLY=0` | Verify every provenance-pinned FPS clip and the combined game runtime before Azure spend. Use network `1` only for the separate acquisition step. |
| `make test-e2e-games-provision AZURE_E2E_ENV_FILE=/tmp/general-ludd.env AZURE_E2E_VALIDATE_ONLY=0 GAME_E2E_TIMEOUT_SECS=3600 GAME_E2E_REFERENCE_NETWORK=0 GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e GLUDD_E2E_MAX_SPEND_USD=5` | Source the explicit env file, then stream GPU provision → game gen → controls → capture/compare → destroy. Timeout must be ≥3600 seconds. |
| `make azure-cleanup-inspect AZURE_E2E_ENV_FILE=/tmp/general-ludd.env AZURE_CLI=az` | Inventory matching E2E groups without mutation. |
| `make azure-cleanup-e2e AZURE_E2E_ENV_FILE=/tmp/general-ludd.env AZURE_CLEANUP_TIMEOUT_SECS=1800 AZURE_CLEANUP_POLL_SECS=10 AZURE_CLI=az` | Delete all `gludd-gpu*` groups and poll until Azure proves zero remain; do not overlap another Gludd E2E run. |
| `make test-e2e-azure` | Azure env-pointer E2E — model call + billing |
| `make test-e2e-azure-provision` | Full deploy → inference → destroy |
| `make test-e2e-providers` | All provider E2E (skips unconfigured) |

### Test files

```text
tests/e2e/game_e2e/test_game_fidelity.py   # Doom+Quake gen, SSIM frame compare (10 tests)
tests/e2e/providers/test_azure_e2e.py       # Model call, billing, discovery (3 tests)
tests/e2e/providers/test_azure_provision_e2e.py  # Full deploy E2E (opt-in)
src/general_ludd/cloud/game_e2e.py          # 561-line orchestrator
```

[fps-game-runbook]: research/FPS_GAME_E2E_RELIABILITY.md#operator-runbook-preflight-paid-run-and-cleanup
[azure-ai-permissions]: https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/ai-machine-learning
[azure-custom-role-scope]: https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles#custom-role-properties
[azure-custom-role-cli]: https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles-cli
[azure-cli-ad-sp]: https://learn.microsoft.com/en-us/cli/azure/ad/sp#az-ad-sp-create-for-rbac
[azure-cli-31995]: https://github.com/Azure/azure-cli/issues/31995
[azure-cli-31579]: https://github.com/Azure/azure-cli/issues/31579
[azure-cli-16940]: https://github.com/Azure/azure-cli/issues/16940
[azure-privileged-roles]: https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged
[azure-rbac-troubleshoot]: https://learn.microsoft.com/en-us/azure/role-based-access-control/troubleshooting
[azure-compute-permissions]: https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute
[azure-container-permissions]: https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/containers
[azure-network-permissions]: https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/networking
[azure-monitor-permissions]: https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/monitor
[azure-provider-operations]: https://learn.microsoft.com/en-us/azure/role-based-access-control/resource-provider-operations
[forum-network-register]: https://learn.microsoft.com/en-us/answers/questions/524560/load-balancer-access-problem
[forum-generic-register]: https://learn.microsoft.com/en-us/answers/questions/5903482/trying-to-create-custom-role
