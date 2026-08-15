# Azure IAM Setup for General Ludd Agent

This guide covers three topics:

1. The least-privilege Azure IAM role definition
2. How to create and assign the role in the Azure Portal
3. How to configure the agent to use the IAM role for Terraform deployment

## 1. Role Definition

The custom role `General Ludd Container App Deployer` is defined in
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

### Updating an existing role

If the custom role already exists and you need to update its permissions,
use `az role definition update` instead of `create`:

```bash
# Substitute the subscription ID into the policy and update the role
az role definition update --role-definition "$(sed "s/{subscription_id}/$SUBSCRIPTION_ID/" config/infra/azure-iam-policy.json)"
```

This uses the same `config/infra/azure-iam-policy.json` file (PascalCase keys)
as the `create` command. The role name (`"General Ludd Container App Deployer"`)
must match the existing role exactly.

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
  --role "General Ludd Container App Deployer" \
  --scopes "/subscriptions/$SUBSCRIPTION_ID"

# Or with a client secret:
az ad sp create-for-rbac \
  --name "gludd-deployer" \
  --sdk-auth \
  --role "General Ludd Container App Deployer" \
  --scopes "/subscriptions/$SUBSCRIPTION_ID"
```

### Step 3: Assign the role

1. In the Azure Portal, go to **Subscriptions** → your subscription.
2. Click **Access control (IAM)**.
3. Click **+ Add** → **Add role assignment**.
4. Search for **General Ludd Container App Deployer** and select it.
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
  --role "General Ludd Container App Deployer" \
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
  --role "General Ludd Container App Deployer" \
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

The managed identity must have the **General Ludd Container App Deployer** role
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
- `General Ludd Container App Deployer` custom role created and assigned
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
