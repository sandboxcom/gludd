# Azure Access Guide — Least Privilege for gludd

Status: UNCOMMITTED working draft. Scope: every Azure facility gludd connects to (or
is planned to connect to). Goal: grant gludd the **minimum** Azure RBAC roles and
Microsoft Graph permissions, deliver credentials safely, and verify each grant with a
read-only call.

> **How gludd actually authenticates (read this first).**
> The shipped connectors do **not** construct an Azure SDK credential object. Each one
> reads a **pre-acquired bearer token** from an environment variable whose *name* you
> choose via the connector config key `token_env`, then sends it as
> `Authorization: Bearer <token>`. See:
>
> | Connector | File | Config keys | Endpoint hit | Token audience the bearer must target |
> |---|---|---|---|---|
> | Azure Monitor / Log Analytics | `src/general_ludd/connectors/azure_monitor.py` | `workspace_id`, `token_env`, `base_url` (default `https://api.loganalytics.io`) | `POST {base_url}/v1/workspaces/{workspace_id}/query` | `https://api.loganalytics.io/.default` |
> | Azure Resource Graph | `src/general_ludd/connectors/azure_resource_graph.py` | `subscriptions`, `token_env`, `base_url` (default `https://management.azure.com`) | `POST {base_url}/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01` | `https://management.azure.com/.default` |
> | Entra ID sign-in logs | *(connector not yet implemented — `entra_signin.py` absent)* | planned: `token_env` | planned: `GET https://graph.microsoft.com/v1.0/auditLogs/signIns` | `https://graph.microsoft.com/.default` |
> | Compute deploy (AKS / VMSS) | *(no deploy connector exists yet)* | planned | ARM control plane under `https://management.azure.com` | `https://management.azure.com/.default` |
>
> Because gludd consumes a **token**, not a credential, "credential delivery" below is
> really **how you mint a token of the right audience** and place it in the `token_env`
> variable. Ranked options (Managed Identity > Workload Identity Federation >
> client-secret) are in [§5](#5-credential-delivery-ranked). The RBAC role/Graph
> permission you assign to that identity is what makes the token *authorized*; the
> audience is what makes it *accepted*.

---

## Table of contents

1. [Facility: Azure Monitor / Log Analytics](#facility-azure-monitor--log-analytics)
2. [Facility: Azure Resource Graph](#facility-azure-resource-graph)
3. [Facility: Entra ID sign-in logs (via Microsoft Graph)](#facility-entra-id-sign-in-logs-via-microsoft-graph)
4. [Facility: Compute deploy (AKS / VM / VMSS)](#facility-compute-deploy-aks--vm--vmss)
5. [Custom RBAC role JSON: `gludd-observability-reader`](#custom-rbac-role-json-gludd-observability-reader)
6. [§5 Credential delivery, ranked](#5-credential-delivery-ranked)
7. [Keys / URLs / env vars table](#keys--urls--env-vars-table)
8. [End-to-end smoke test](#end-to-end-smoke-test)

---

## Facility: Azure Monitor / Log Analytics

Reads logs and metric-style numeric columns by running KQL against a Log Analytics
workspace (`api.loganalytics.io`). The bearer token's audience **must** be
`https://api.loganalytics.io/.default`.

### 1. Minimal role

Choose **one** at the **narrowest scope** (the workspace resource, not the whole
subscription):

| Role | Built-in? | Why minimal |
|---|---|---|
| **Log Analytics Reader** (`73c42c96-874c-492b-b04d-ab87d138a893`) | built-in | Read access to workspace data + run queries. **Preferred.** |
| **Monitoring Reader** (`43d0d8ad-25c7-4714-9337-8ba259a9fe05`) | built-in | If you also read Azure Monitor metrics/alerts beyond this workspace. |

Assign at the **workspace** scope:
`/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<workspaceName>`.

> Note: `workspace_id` in gludd config is the workspace **GUID** (the
> `customerId`), not the ARM resource id. You need the ARM resource id for the
> *assignment scope*; you need the GUID for gludd config. Map them with the
> verification call below.

### 2. Custom role

Not needed — `Log Analytics Reader` is already least-privilege for query. If you must
hand-roll, see [`gludd-observability-reader`](#custom-rbac-role-json-gludd-observability-reader),
which folds this facility in.

### 3. Where / how to apply

**Portal**
1. Portal > Log Analytics workspaces > *your workspace* > **Access control (IAM)**.
2. **+ Add** > **Add role assignment**.
3. Role: **Log Analytics Reader** > Next.
4. **Assign access to**: *Managed identity* (preferred) or *User, group, or service principal*; select gludd's identity > **Review + assign**.

**az CLI**
```bash
# Resolve workspace ARM id + GUID (GUID == gludd's workspace_id)
WS_ID=$(az monitor log-analytics workspace show \
  -g "<rg>" -n "<workspaceName>" --query id -o tsv)
az monitor log-analytics workspace show \
  -g "<rg>" -n "<workspaceName>" --query customerId -o tsv   # -> gludd workspace_id

az role assignment create \
  --assignee-object-id "<gludd-identity-objectId>" \
  --assignee-principal-type ServicePrincipal \
  --role "Log Analytics Reader" \
  --scope "$WS_ID"
```

### Read-only verification

```bash
# Mint a Log Analytics token and run the same query gludd's health check uses.
TOKEN=$(az account get-access-token \
  --resource "https://api.loganalytics.io" --query accessToken -o tsv)
WS_GUID="<gludd workspace_id GUID>"
curl -s -X POST \
  "https://api.loganalytics.io/v1/workspaces/${WS_GUID}/query" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query":"print 1","timespan":"PT5M"}'
# Expect HTTP 200 with a tables payload. 403 => role/scope wrong. 401 => bad audience.
```

---

## Facility: Azure Resource Graph

Infrastructure inventory via KQL against ARM
(`management.azure.com/providers/Microsoft.ResourceGraph/resources`). Token audience
**must** be `https://management.azure.com/.default`.

### 1. Minimal role

| Role | Built-in? | Scope |
|---|---|---|
| **Reader** (`acdd72a7-3385-48ef-bd42-f606fba81ae7`) | built-in | Subscription (or management group spanning the subs in `config['subscriptions']`). |

Resource Graph returns **only** resources the identity can already *read* — so plain
**Reader** scoped to each subscription is exactly least-privilege. No data-plane action
is required; the Resource Graph provider itself needs no extra permission beyond Reader.

### 2. Custom role

Not needed. `Reader` is minimal. The custom
[`gludd-observability-reader`](#custom-rbac-role-json-gludd-observability-reader) is an
optional tighter alternative if you object to Reader's breadth — but be aware Resource
Graph filters by the resources Reader grants, so a *narrower* custom role will simply
return *fewer* rows.

### 3. Where / how to apply

**Portal**
1. Portal > **Subscriptions** > *each subscription in `config['subscriptions']`* > **Access control (IAM)**.
2. **+ Add** > **Add role assignment** > Role **Reader** > Next.
3. Assign to gludd's managed identity / service principal > **Review + assign**.

**az CLI**
```bash
for SUB in "<sub-1>" "<sub-2>"; do
  az role assignment create \
    --assignee-object-id "<gludd-identity-objectId>" \
    --assignee-principal-type ServicePrincipal \
    --role "Reader" \
    --scope "/subscriptions/${SUB}"
done
```

### Read-only verification

```bash
TOKEN=$(az account get-access-token \
  --resource "https://management.azure.com" --query accessToken -o tsv)
curl -s -X POST \
  "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"subscriptions":["<sub-1>"],"query":"Resources | limit 1"}'
# Expect HTTP 200 with one resource row. 403 => no Reader on that subscription.
```

---

## Facility: Entra ID sign-in logs (via Microsoft Graph)

> **Not yet implemented in gludd** (`connectors/entra_signin.py` does not exist). This
> section is the target contract so the identity is ready when the connector lands. The
> token audience will be `https://graph.microsoft.com/.default` and the call will be
> `GET https://graph.microsoft.com/v1.0/auditLogs/signIns`.

Sign-in logs are a **Microsoft Graph application permission**, NOT an Azure RBAC role.
RBAC (Reader / Monitoring Reader) does **nothing** here; Graph permissions are separate.

### 1. Minimal permission

| Graph permission | Type | Consent | Why |
|---|---|---|---|
| **`AuditLog.Read.All`** | **Application** | **Admin consent required** | Reads `/auditLogs/signIns`. |
| **`Directory.Read.All`** | **Application** | **Admin consent required** | Microsoft requires this *alongside* `AuditLog.Read.All` to enumerate sign-in logs. |

Use **Application** permissions (daemon, no signed-in user). Do **not** grant
`Directory.ReadWrite.All` or any `*.ReadWrite.*` — read-only only.

### 2. App Registration setup

The Graph identity is an **App Registration** (service principal). See
[§5](#5-credential-delivery-ranked) for how the secret/federation is delivered; the
*permissions* are attached here.

### 3. Where / how to apply

**Portal**
1. Entra admin center > **Identity** > **Applications** > **App registrations** > your gludd app (or **+ New registration**).
2. **API permissions** > **+ Add a permission** > **Microsoft Graph** > **Application permissions**.
3. Search and check **`AuditLog.Read.All`** and **`Directory.Read.All`** > **Add permissions**.
4. Click **Grant admin consent for &lt;tenant&gt;** (requires Privileged Role Admin / Global Admin). Status must show a green check for both.

**az CLI**
```bash
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"   # Microsoft Graph
# App-role (application permission) ids on Microsoft Graph:
AUDITLOG_READ_ALL="b0afded3-3588-46d8-8b3d-9842eff778da"   # AuditLog.Read.All
DIRECTORY_READ_ALL="7ab1d382-f21e-4acd-a863-ba3e13f7da61"  # Directory.Read.All
APP_ID="<gludd app (client) id>"

az ad app permission add --id "$APP_ID" \
  --api "$GRAPH_APP_ID" \
  --api-permissions "${AUDITLOG_READ_ALL}=Role" "${DIRECTORY_READ_ALL}=Role"

# Admin-consent (grants the app roles tenant-wide):
az ad app permission admin-consent --id "$APP_ID"
```

### Read-only verification

```bash
TOKEN=$(az account get-access-token \
  --resource "https://graph.microsoft.com" --query accessToken -o tsv)
curl -s -H "Authorization: Bearer ${TOKEN}" \
  "https://graph.microsoft.com/v1.0/auditLogs/signIns?\$top=1"
# Expect HTTP 200 + a value[] array. 403 "Insufficient privileges" => consent missing.
```

---

## Facility: Compute deploy (AKS / VM / VMSS)

> **No deploy connector exists yet.** gludd needs to deploy compute for model serving
> (AKS or VMSS). Unlike the read-only facilities above, this **writes** to Azure, so it
> gets the tightest possible scope: **one resource group**, never the subscription.

### 1. Minimal role

Two acceptable approaches, in order of preference:

| Approach | Role | Scope | Notes |
|---|---|---|---|
| **A (preferred for AKS)** | **Azure Kubernetes Service Contributor Role** (`ed7f3fbb-7b10-4bd5-95c0-92b85d5f3a86`) | the model-serving **resource group** | Manage AKS clusters only; no broad write. |
| **A (preferred for VMSS)** | custom `gludd-compute-deployer` (below) | the model-serving **resource group** | VM/VMSS/network/disk write only. |
| **B (simplest)** | **Contributor** (`b24988ac-6189-42b0-9b3a-c9f6b13c8b9c`) | the model-serving **resource group ONLY** | Broad but RG-scoped. Acceptable if you cannot maintain a custom role. **Never** assign Contributor at subscription scope. |

Custom least-priv deploy role (VMSS/VM model serving), assign at RG scope:

```json
{
  "Name": "gludd-compute-deployer",
  "IsCustom": true,
  "Description": "Deploy/scale model-serving compute (VM/VMSS) in one resource group only.",
  "Actions": [
    "Microsoft.Compute/virtualMachineScaleSets/read",
    "Microsoft.Compute/virtualMachineScaleSets/write",
    "Microsoft.Compute/virtualMachineScaleSets/delete",
    "Microsoft.Compute/virtualMachineScaleSets/scale/action",
    "Microsoft.Compute/virtualMachineScaleSets/restart/action",
    "Microsoft.Compute/virtualMachines/read",
    "Microsoft.Compute/virtualMachines/write",
    "Microsoft.Compute/virtualMachines/delete",
    "Microsoft.Compute/disks/read",
    "Microsoft.Compute/disks/write",
    "Microsoft.Compute/disks/delete",
    "Microsoft.Network/networkInterfaces/read",
    "Microsoft.Network/networkInterfaces/write",
    "Microsoft.Network/networkInterfaces/join/action",
    "Microsoft.Network/virtualNetworks/subnets/read",
    "Microsoft.Network/virtualNetworks/subnets/join/action",
    "Microsoft.Network/loadBalancers/read",
    "Microsoft.Network/loadBalancers/write",
    "Microsoft.Resources/deployments/read",
    "Microsoft.Resources/deployments/write",
    "Microsoft.Resources/subscriptions/resourceGroups/read"
  ],
  "NotActions": [],
  "DataActions": [],
  "NotDataActions": [],
  "AssignableScopes": [
    "/subscriptions/<sub>/resourceGroups/<model-serving-rg>"
  ]
}
```

### 3. Where / how to apply

**Portal**
1. Portal > **Resource groups** > *model-serving RG* > **Access control (IAM)** > **+ Add** > **Add role assignment**.
2. Pick **Azure Kubernetes Service Contributor Role** / custom `gludd-compute-deployer` / RG-scoped **Contributor** > assign to gludd's managed identity.

**az CLI**
```bash
RG_SCOPE="/subscriptions/<sub>/resourceGroups/<model-serving-rg>"

# (custom role only) create it once:
az role definition create --role-definition gludd-compute-deployer.json

az role assignment create \
  --assignee-object-id "<gludd-identity-objectId>" \
  --assignee-principal-type ServicePrincipal \
  --role "gludd-compute-deployer" \
  --scope "$RG_SCOPE"
```

### Read-only verification (pre-flight, no resources mutated)

```bash
# Confirm the identity can SEE the RG and list compute (write is exercised at deploy time).
TOKEN=$(az account get-access-token \
  --resource "https://management.azure.com" --query accessToken -o tsv)
curl -s -H "Authorization: Bearer ${TOKEN}" \
  "https://management.azure.com/subscriptions/<sub>/resourceGroups/<model-serving-rg>/providers/Microsoft.Compute/virtualMachineScaleSets?api-version=2023-09-01"
# Expect HTTP 200 (empty value[] is fine). 403 => role/scope wrong.
```

---

## Custom RBAC role JSON: `gludd-observability-reader`

Single least-privilege role that covers **both** read-only facilities (Log Analytics +
Resource Graph) without granting full `Reader`. Note `Reader`/`Log Analytics Reader`
remain the simpler choice; use this only when you must minimize the action surface.

```json
{
  "Name": "gludd-observability-reader",
  "IsCustom": true,
  "Description": "Read-only observability access for gludd: Log Analytics query + Resource Graph inventory. No write, no secrets.",
  "Actions": [
    "Microsoft.OperationalInsights/workspaces/read",
    "Microsoft.OperationalInsights/workspaces/query/read",
    "Microsoft.OperationalInsights/workspaces/query/*/read",
    "Microsoft.OperationalInsights/workspaces/search/action",
    "Microsoft.Insights/metrics/read",
    "Microsoft.Insights/metricDefinitions/read",
    "Microsoft.ResourceGraph/resources/read",
    "Microsoft.Resources/subscriptions/read",
    "Microsoft.Resources/subscriptions/resourceGroups/read",
    "Microsoft.Resources/subscriptions/resources/read"
  ],
  "NotActions": [],
  "DataActions": [
    "Microsoft.OperationalInsights/workspaces/query/*/read"
  ],
  "NotDataActions": [],
  "AssignableScopes": [
    "/subscriptions/<sub>"
  ]
}
```

> The `DataActions` line grants the **data-plane** query right Log Analytics needs;
> the matching `Actions` line covers control-plane discovery. `Microsoft.ResourceGraph/resources/read`
> only returns what the identity can otherwise read, so pair this role's
> `Microsoft.Resources/.../read` actions to make inventory useful.

**Create + assign**
```bash
az role definition create --role-definition gludd-observability-reader.json
az role assignment create \
  --assignee-object-id "<gludd-identity-objectId>" \
  --assignee-principal-type ServicePrincipal \
  --role "gludd-observability-reader" \
  --scope "/subscriptions/<sub>"
```

### App Registration setup (shared by all SP-based facilities)

```bash
# 1. Create the app + service principal
az ad app create --display-name "gludd-observability"
APP_ID=$(az ad app list --display-name "gludd-observability" --query "[0].appId" -o tsv)
az ad sp create --id "$APP_ID"
SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)   # <- objectId for --assignee-object-id

# 2. Attach Graph permissions for the sign-in facility (see that section)
# 3. Deliver a credential (federation preferred — see §5); secret fallback:
#    az ad app credential reset --id "$APP_ID" --display-name gludd --years 1
```

---

## §5 Credential delivery, ranked

gludd needs a **bearer token** of the correct audience in the env var named by
`config['token_env']`. Rank of how to mint/deliver it:

### (a) Managed Identity — preferred when gludd runs IN Azure

System-assigned (tied to the host) or user-assigned (portable across hosts). No secret
to store or rotate. Assign the RBAC roles / Graph permissions above to the managed
identity's object id.

```bash
# Inside the Azure host (AKS pod with workload identity, VM, Container App):
# Mint the Log Analytics-audience token and export into gludd's token_env:
export GLUDD_LOGANALYTICS_TOKEN=$(curl -s \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://api.loganalytics.io" \
  -H "Metadata: true" | jq -r .access_token)
# Repeat per audience: https://management.azure.com (Resource Graph),
# https://graph.microsoft.com (sign-in logs).
```
Set `config['token_env'] = "GLUDD_LOGANALYTICS_TOKEN"` etc. No `AZURE_CLIENT_SECRET`.

### (b) Workload Identity Federation — preferred when gludd runs OUTSIDE Azure

For gludd in GitHub Actions, GitLab, or another cloud/k8s: federate the App
Registration with an external OIDC issuer — **no stored secret**.

```bash
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "gludd-ci",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<org>/<repo>:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'
```
Provide `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_FEDERATED_TOKEN_FILE` (or the
CI OIDC token); exchange it for an audience-scoped token and place it in `token_env`.

### (c) Client-secret service principal — last resort

Only when neither (a) nor (b) is possible. Requires storing and rotating a secret.

```bash
az ad app credential reset --id "$APP_ID" --display-name gludd --years 1
# Provides: appId (AZURE_CLIENT_ID), password (AZURE_CLIENT_SECRET), tenant (AZURE_TENANT_ID)
```
Acquire the token (one per audience) and feed it into `token_env`:
```bash
export GLUDD_RESOURCEGRAPH_TOKEN=$(curl -s \
  "https://login.microsoftonline.com/${AZURE_TENANT_ID}/oauth2/v2.0/token" \
  -d "client_id=${AZURE_CLIENT_ID}" \
  -d "client_secret=${AZURE_CLIENT_SECRET}" \
  -d "grant_type=client_credentials" \
  -d "scope=https://management.azure.com/.default" | jq -r .access_token)
```
Store the secret in Key Vault, scope it to one client, rotate ≤ 1 year, and never
commit it.

---

## Keys / URLs / env vars table

| Env var | Meaning | How to obtain | Maps to role / permission |
|---|---|---|---|
| *(value of `config['token_env']` for Monitor, e.g. `GLUDD_LOGANALYTICS_TOKEN`)* | Bearer token, audience `https://api.loganalytics.io` | Managed identity IMDS / federation / client-credentials grant with `scope=https://api.loganalytics.io/.default` | **Log Analytics Reader** on the workspace |
| *(value of `config['token_env']` for Resource Graph, e.g. `GLUDD_RESOURCEGRAPH_TOKEN`)* | Bearer token, audience `https://management.azure.com` | same flows, `scope=https://management.azure.com/.default` | **Reader** on each subscription (or `gludd-observability-reader`) |
| *(value of `config['token_env']` for sign-in, e.g. `GLUDD_GRAPH_TOKEN`)* | Bearer token, audience `https://graph.microsoft.com` | same flows, `scope=https://graph.microsoft.com/.default` | **`AuditLog.Read.All` + `Directory.Read.All`** (application, admin-consented) |
| *(value of `config['token_env']` for deploy)* | Bearer token, audience `https://management.azure.com` | same flows | **AKS Contributor** / `gludd-compute-deployer` / RG-scoped Contributor |
| `AZURE_TENANT_ID` | Entra tenant (directory) id | `az account show --query tenantId -o tsv` | identity-wide (auth) |
| `AZURE_CLIENT_ID` | App registration (client) id | `az ad app list --display-name gludd-observability --query "[0].appId" -o tsv` | the SP that holds the roles above |
| `AZURE_CLIENT_SECRET` | SP secret (option **c** only) | `az ad app credential reset --id "$APP_ID"` | n/a (authn material) |
| `AZURE_FEDERATED_TOKEN_FILE` | OIDC token path (option **b**) | CI/k8s injects it | n/a (authn material) |
| `workspace_id` (gludd config, not env) | Log Analytics workspace **GUID** | `az monitor log-analytics workspace show -g <rg> -n <ws> --query customerId -o tsv` | identifies the data scope |
| `subscriptions` (gludd config, not env) | Subscription id list for Resource Graph | `az account list --query "[].id" -o tsv` | must match the Reader assignments |

**Endpoints referenced:** `https://api.loganalytics.io` (Log Analytics query),
`https://management.azure.com` (ARM / Resource Graph / compute),
`https://graph.microsoft.com` (sign-in logs),
`https://login.microsoftonline.com` (token endpoint),
`http://169.254.169.254` (IMDS, managed identity only). The shipped connectors block
internal/metadata hosts via SSRF host validation, so the bearer must be minted out of
band and supplied through `token_env`.

---

## End-to-end smoke test

Run each facility's **Read-only verification** block above. All four should return
HTTP 200. If any returns:

- **401** — token audience wrong (you minted for the wrong `--resource` / `scope`).
- **403** — role/permission or scope wrong (Log Analytics Reader / Reader / admin
  consent / RG scope).
- **404** — wrong `workspace_id` GUID or subscription id.

Only after all four pass, set the matching `token_env` variables and start gludd.
