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

## 2. Creating and Assigning the Role in Azure Portal

### Step 1: Create the custom role

1. Sign in to the [Azure Portal](https://portal.azure.com).
2. Search for **Subscriptions** and select your target subscription.
3. Click **Access control (IAM)** in the left sidebar.
4. Click **+ Add** → **Add custom role**.
5. Switch to **JSON** tab and click **Edit**.
6. Replace the JSON with the contents of `config/infra/azure-iam-policy.json`.
7. Replace `"/subscriptions/{subscription_id}"` in `AssignableScopes` with your
   actual subscription ID (found on the Subscription overview page).
8. Click **Save** → **Review + create** → **Create**.

Alternatively, use the Azure CLI:

```bash
# Get your subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Substitute the subscription ID into the policy
sed "s/{subscription_id}/$SUBSCRIPTION_ID/" config/infra/azure-iam-policy.json > /tmp/gludd-role.json

# Create the custom role
az role definition create --role-definition /tmp/gludd-role.json
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

```
https://gpu-inference-<revision>.<region>.azurecontainerapps.io
```

The inference API is available at:

```
GET  https://gpu-inference-<revision>.<region>.azurecontainerapps.io/v1/models
POST https://gpu-inference-<revision>.<region>.azurecontainerapps.io/v1/chat/completions
```