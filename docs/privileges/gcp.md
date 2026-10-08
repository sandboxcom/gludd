# GCP Least-Privilege Access Guide

Least-privilege IAM, credential delivery, and verification steps for **every GCP
facility gludd connects to or deploys onto**. Copy-paste the blocks per facility.

> **Scope & source of truth.** This guide is grounded in the actual connector code:
> `src/general_ludd/connectors/gcp_observability.py` (class `GcpObservabilitySource`)
> and the connector contract in `src/general_ludd/connectors/base.py`.
>
> | Facility | Status in code | Anchor |
> |---|---|---|
> | Cloud Logging (logs) | **Wired** — `mode='logs'` → `entries.list` | `gcp_observability.py` lines 7-8, 207-226 |
> | Cloud Monitoring (metrics) | **Wired** — `mode='metrics'` → `timeSeries.list` | `gcp_observability.py` lines 9-10, 260-285 |
> | Cloud Trace (traces) | **Planned** — `TraceSource` protocol marker only; no concrete GCP trace connector yet | `base.py` lines 147-149 (`TRACE_KIND = "traces"`) |
> | Compute deploy (GCE/GKE for model serving) | **Planned** — no infra/deploy code in repo today | (forward-looking) |
>
> Logging and Monitoring sections describe live behaviour. Trace and compute-deploy
> sections are forward-looking: grant their roles **only** when the corresponding
> connector / deploy path actually lands. Granting them early is wasted privilege.

---

## 0. How the connector authenticates (read this first)

`GcpObservabilitySource.__init__` resolves a **Bearer token** in this order
(`gcp_observability.py` lines 148-159):

1. An explicitly injected `token=` argument (tests / in-process callers), else
2. `os.environ.get(config['token_env'])`, where `token_env` **defaults to `GCP_TOKEN`**.
   If the named env var is unset, construction raises
   `ValueError: missing Bearer token: env var '<name>' is unset` — **fail-closed**.

The token is sent as `Authorization: Bearer <token>` (lines 164-168). It is an
**OAuth2 access token**, not a raw service-account key. So the access model is:

```json
[ IAM role on a principal ]  →  [ OAuth2 access token for that principal ]  →  GCP_TOKEN env  →  connector
```

The **role** is what grants least privilege. The **token** is just a short-lived
bearer of that role's permissions. Sections 1-4 grant the roles; section "Credential
delivery" turns a role-bearing principal into the `GCP_TOKEN` value.

Config keys read by the connector (all of `config`):

| Key | Default | Meaning |
|---|---|---|
| `project` | *(required)* | GCP project id; used in resource names & monitoring URL |
| `name` | `gcp:<project>` | Source name in normalized records |
| `timeout` | `15.0` | Per-request timeout (seconds) |
| `order_by` | `timestamp desc` | Default log ordering |
| `logging_endpoint` | `https://logging.googleapis.com/v2/entries:list` | Cloud Logging URL |
| `monitoring_base` | `https://monitoring.googleapis.com/v3` | Cloud Monitoring base URL |
| `token_env` | `GCP_TOKEN` | **Name of the env var** holding the Bearer token |

> Endpoints are SSRF-checked at construction against a literal host denylist
> (loopback, link-local, RFC-1918, `metadata.google.internal`) with **no DNS**
> (`_require_safe_endpoint`, lines 104-115). Public `*.googleapis.com` hosts pass.

---

## 1. Cloud Logging (logs)  —  **wired**

**What the connector does:** `POST https://logging.googleapis.com/v2/entries:list`
with body `{ "resourceNames": ["projects/<project>"], "orderBy": ..., "pageSize": ..., "filter": ... }`
(`gcp_observability.py` lines 207-226). This is the `logging.logEntries.list` operation.
It only **reads** entries — it never writes logs, sinks, or metrics.

### 1a. Minimal predefined role

| Role | Why |
|---|---|
| `roles/logging.viewer` | Grants `logging.logEntries.list` + `logging.logs.list` (read-only). Sufficient for `mode='logs'`. |

> Do **not** use `roles/logging.privateLogViewer`, `roles/logging.admin`, or
> `roles/logging.configWriter` — those add data-access-log reads, sink/exclusion
> writes, and admin. The connector needs none of them.

### 1b. Custom role (tighter than the predefined viewer)

`gcp-logging-reader.yaml`:

```yaml
title: "gludd Logging Reader"
description: "Read-only Cloud Logging entries for the gludd observability connector."
stage: GA
includedPermissions:
  - logging.logEntries.list
  - logging.logs.list
```

### 1c. Create + bind (gcloud CLI)

```bash
# Variables
PROJECT_ID="your-project-id"
SA_NAME="gludd-observability"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# 1) Service account (shared by all read facilities — create once)
gcloud iam service-accounts create "${SA_NAME}" \
  --project="${PROJECT_ID}" \
  --display-name="gludd observability reader"

# 2a) OPTION A — predefined role
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/logging.viewer" \
  --condition=None

# 2b) OPTION B — custom role (tighter)
gcloud iam roles create gluddLoggingReader \
  --project="${PROJECT_ID}" \
  --file=gcp-logging-reader.yaml
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="projects/${PROJECT_ID}/roles/gluddLoggingReader" \
  --condition=None
```

> Scope to a **folder** instead of a project with
> `gcloud resource-manager folders add-iam-policy-binding <FOLDER_ID> ...` if gludd
> reads many projects under one folder. Prefer project scope when possible.

### 1d. Apply via Console

- **Custom role:** IAM & Admin → **Roles** → **Create Role** → add
  `logging.logEntries.list` and `logging.logs.list` → Create.
- **Grant:** IAM & Admin → **IAM** → **Grant Access** → principal =
  `gludd-observability@<project>.iam.gserviceaccount.com` → role =
  *gludd Logging Reader* (or **Logs Viewer** for the predefined one) → Save.
- **Service account:** IAM & Admin → **Service Accounts** → **Create Service Account**.

### 1e. Verify (read-only)

```bash
# Verifies logging.logEntries.list end-to-end with the SA's token.
gcloud logging read 'severity>=ERROR' \
  --project="${PROJECT_ID}" --limit=1 \
  --impersonate-service-account="${SA_EMAIL}"

# Or raw REST, exactly mirroring the connector's POST:
ACCESS_TOKEN="$(gcloud auth print-access-token --impersonate-service-account="${SA_EMAIL}")"
curl -s -X POST 'https://logging.googleapis.com/v2/entries:list' \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"resourceNames\":[\"projects/${PROJECT_ID}\"],\"pageSize\":1}"
```

Permission-by-permission check:

```bash
# logging.logs.list
gcloud logging logs list --project="${PROJECT_ID}" --limit=1 \
  --impersonate-service-account="${SA_EMAIL}"
```

---

## 2. Cloud Monitoring (metrics)  —  **wired**

**What the connector does:** `GET https://monitoring.googleapis.com/v3/projects/<project>/timeSeries`
with query params `filter`, `interval.startTime`, `interval.endTime`, `pageSize`
(`gcp_observability.py` lines 144-146, 260-285). This is `monitoring.timeSeries.list`.
The connector's `health()` (lines 177-191) also calls this endpoint with `pageSize=1`,
so the monitoring read permission is required for health checks too. Read-only.

### 2a. Minimal predefined role

| Role | Why |
|---|---|
| `roles/monitoring.viewer` | Grants `monitoring.timeSeries.list` + read of metric/monitored-resource descriptors. Read-only. |

> Do **not** use `roles/monitoring.editor` or `roles/monitoring.admin` (those add
> write of dashboards, alerts, uptime checks, and metric ingestion).

### 2b. Custom role

`gcp-monitoring-reader.yaml`:

```yaml
title: "gludd Monitoring Reader"
description: "Read-only Cloud Monitoring time series for the gludd connector."
stage: GA
includedPermissions:
  - monitoring.timeSeries.list
  - monitoring.metricDescriptors.list
  - monitoring.monitoredResourceDescriptors.list
```

### 2c. Create + bind (gcloud CLI)

```bash
# Reuses the SA from section 1c.
# OPTION A — predefined
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/monitoring.viewer" \
  --condition=None

# OPTION B — custom
gcloud iam roles create gluddMonitoringReader \
  --project="${PROJECT_ID}" \
  --file=gcp-monitoring-reader.yaml
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="projects/${PROJECT_ID}/roles/gluddMonitoringReader" \
  --condition=None
```

### 2d. Apply via Console

- **Custom role:** IAM & Admin → **Roles** → **Create Role** → add the three
  `monitoring.*` permissions above.
- **Grant:** IAM & Admin → **IAM** → **Grant Access** → same SA principal → role =
  *gludd Monitoring Reader* (or predefined **Monitoring Viewer**).

### 2e. Verify (read-only)

```bash
ACCESS_TOKEN="$(gcloud auth print-access-token --impersonate-service-account="${SA_EMAIL}")"

# monitoring.timeSeries.list — mirrors the connector's GET (and its health() call)
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START="$(date -u -v-10M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ)"
curl -s -G "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/timeSeries" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  --data-urlencode 'filter=metric.type="compute.googleapis.com/instance/cpu/utilization"' \
  --data-urlencode "interval.startTime=${START}" \
  --data-urlencode "interval.endTime=${NOW}" \
  --data-urlencode 'pageSize=1'

# monitoring.metricDescriptors.list
curl -s "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/metricDescriptors?pageSize=1" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

---

## 3. Cloud Trace (traces)  —  **planned (grant only when the connector lands)**

> **Status:** the repo defines a `TraceSource` protocol marker
> (`base.py` lines 147-149, `TRACE_KIND = "traces"`) but **no concrete GCP trace
> connector exists yet** — there is no `cloudtrace.googleapis.com` call in the code.
> A future trace connector following the `GcpObservabilitySource` pattern would call
> `cloudtrace.googleapis.com/v1/projects/<project>/traces` (`traces.list` / `traces.get`).
> Grant the role below **only after** that connector ships; until then it is dead privilege.

### 3a. Minimal predefined role

| Role | Why |
|---|---|
| `roles/cloudtrace.user` | Grants `cloudtrace.traces.get` + `cloudtrace.traces.list` (read) — the right least-privilege choice for a read-only trace connector. |

> `roles/cloudtrace.admin` adds write/delete — never needed for read.
> `roles/cloudtrace.agent` is for *writing* spans (apps emitting traces), not reading.

### 3b. Custom role

`gcp-trace-reader.yaml`:

```yaml
title: "gludd Trace Reader"
description: "Read-only Cloud Trace access for a future gludd trace connector."
stage: GA
includedPermissions:
  - cloudtrace.traces.get
  - cloudtrace.traces.list
```

### 3c. Create + bind (gcloud CLI)

```bash
# OPTION A — predefined
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/cloudtrace.user" \
  --condition=None

# OPTION B — custom
gcloud iam roles create gluddTraceReader \
  --project="${PROJECT_ID}" \
  --file=gcp-trace-reader.yaml
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="projects/${PROJECT_ID}/roles/gluddTraceReader" \
  --condition=None
```

### 3d. Apply via Console

- **Custom role:** IAM & Admin → **Roles** → **Create Role** → add
  `cloudtrace.traces.get`, `cloudtrace.traces.list`.
- **Grant:** IAM & Admin → **IAM** → **Grant Access** → same SA → role =
  *gludd Trace Reader* (or predefined **Cloud Trace User**).

### 3e. Verify (read-only)

```bash
ACCESS_TOKEN="$(gcloud auth print-access-token --impersonate-service-account="${SA_EMAIL}")"

# cloudtrace.traces.list
curl -s "https://cloudtrace.googleapis.com/v1/projects/${PROJECT_ID}/traces?pageSize=1" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# cloudtrace.traces.get (substitute a real TRACE_ID from the list above)
# curl -s "https://cloudtrace.googleapis.com/v1/projects/${PROJECT_ID}/traces/<TRACE_ID>" \
#   -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

---

## 4. Compute deploy — GCE / GKE for model serving  —  **planned**

> **Status:** there is **no deploy/infra code in the repo today** (no Terraform, no
> manifests, no `compute.googleapis.com` / `container.googleapis.com` client). This
> section is the least-privilege target for when gludd starts deploying its own model
> serving. The deploy principal **must be a separate service account** from the
> read-only observability SA in §1-3 — never merge read and deploy privileges.
>
> Pick **GKE _or_ GCE**, not both. Scope to a single project (or folder), never org.

### 4a. Minimal predefined role

| If deploying to… | Use | Grants (least privilege) |
|---|---|---|
| **GKE** (containerized serving) | `roles/container.developer` | Manage workloads/objects **inside** existing clusters (deploy Pods/Services). Does **not** let you create/delete clusters. |
| **GCE** (VM-based serving) | `roles/compute.instanceAdmin.v1` | Create/start/stop/delete **instances** in the project. Does **not** grant network/firewall admin or project IAM. |

> Avoid `roles/container.admin`, `roles/compute.admin`, `roles/editor`, `roles/owner`.
> If the deploy SA must also pull serving images: add `roles/artifactregistry.reader`
> (read-only) — nothing broader. If it provisions clusters (rare; prefer pre-created
> clusters), `roles/container.clusterAdmin` is the bounded choice, still project-scoped.

A GKE workload deploying via Workload Identity also needs `roles/container.developer`
on the deploy principal and the **GKE node SA** kept minimal (`roles/logging.logWriter`,
`roles/monitoring.metricWriter`, `roles/artifactregistry.reader` only).

### 4b. Custom role (GCE instance-deploy, tighter than instanceAdmin.v1)

`gcp-gce-deployer.yaml`:

```yaml
title: "gludd GCE Serving Deployer"
description: "Minimal GCE instance lifecycle for gludd model-serving deploy."
stage: GA
includedPermissions:
  - compute.instances.create
  - compute.instances.delete
  - compute.instances.get
  - compute.instances.list
  - compute.instances.start
  - compute.instances.stop
  - compute.instances.setMetadata
  - compute.disks.create
  - compute.zones.get
  # iam.serviceAccounts.actAs is required to attach a runtime SA to the instance;
  # grant it narrowly on the *runtime* SA resource, not project-wide (see 4c).
```

`gcp-gke-deployer.yaml` (GKE workload deploy without cluster admin):

```yaml
title: "gludd GKE Serving Deployer"
description: "Deploy workloads into existing GKE clusters; no cluster lifecycle."
stage: GA
includedPermissions:
  - container.clusters.get
  - container.clusters.list
  - container.pods.create
  - container.pods.get
  - container.pods.list
  - container.deployments.create
  - container.deployments.get
  - container.deployments.update
  - container.services.create
  - container.services.get
```

### 4c. Create + bind (gcloud CLI)

```bash
DEPLOY_SA_NAME="gludd-serving-deployer"
DEPLOY_SA_EMAIL="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create "${DEPLOY_SA_NAME}" \
  --project="${PROJECT_ID}" \
  --display-name="gludd model-serving deployer"

# --- GKE path (predefined) ---
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOY_SA_EMAIL}" \
  --role="roles/container.developer" \
  --condition=None

# --- GCE path (predefined) ---
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOY_SA_EMAIL}" \
  --role="roles/compute.instanceAdmin.v1" \
  --condition=None

# --- GCE custom role variant ---
gcloud iam roles create gluddGceDeployer \
  --project="${PROJECT_ID}" --file=gcp-gce-deployer.yaml
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOY_SA_EMAIL}" \
  --role="projects/${PROJECT_ID}/roles/gluddGceDeployer" \
  --condition=None

# actAs scoped to the runtime SA only (NOT project-wide) — needed to attach an SA to a VM
RUNTIME_SA_EMAIL="gludd-serving-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOY_SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser"
```

### 4d. Apply via Console

- **Service account:** IAM & Admin → **Service Accounts** → **Create** →
  `gludd-serving-deployer`.
- **Custom role:** IAM & Admin → **Roles** → **Create Role** → paste the
  GCE/GKE permission list above.
- **Grant:** IAM & Admin → **IAM** → **Grant Access** → principal = deploy SA →
  role = **Kubernetes Engine Developer** *or* **Compute Instance Admin (v1)** /
  your custom role. Scope via the resource selector (project/folder).
- **actAs:** open the *runtime* SA → **Permissions** tab → **Grant Access** →
  deploy SA as **Service Account User**.

### 4e. Verify (read-only — does not create anything)

```bash
ACCESS_TOKEN="$(gcloud auth print-access-token --impersonate-service-account="${DEPLOY_SA_EMAIL}")"

# GKE: container.clusters.list (read; proves the deploy SA can see clusters)
gcloud container clusters list --project="${PROJECT_ID}" \
  --impersonate-service-account="${DEPLOY_SA_EMAIL}"

# GCE: compute.instances.list (read; proves instance visibility)
gcloud compute instances list --project="${PROJECT_ID}" \
  --impersonate-service-account="${DEPLOY_SA_EMAIL}"

# Dry-run create permission WITHOUT creating, via testIamPermissions:
curl -s -X POST \
  "https://compute.googleapis.com/compute/v1/projects/${PROJECT_ID}/testIamPermissions" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"permissions":["compute.instances.create","compute.instances.delete"]}'
# Returned "permissions" array echoes only those the SA actually holds.
```

---

## Credential delivery — ranked (best → worst)

The connector ultimately needs an **OAuth2 access token** in the env var named by
`token_env` (default `GCP_TOKEN`). How you mint that token determines your blast
radius. Ranked most → least secure:

### (a) Workload Identity Federation — **preferred, no keys**

Federate an external identity (GitHub Actions OIDC, AWS, OIDC IdP) to impersonate the
GCP service account. **No long-lived key ever exists.** The CI/runtime exchanges its
OIDC token for a short-lived GCP access token and exports it:

```bash
# In CI (after google-github-actions/auth or `gcloud auth login` via WIF):
export GCP_TOKEN="$(gcloud auth print-access-token)"   # short-lived, auto-expiring
# gludd reads GCP_TOKEN (config['token_env']).
```

Setup: create a **Workload Identity Pool + Provider**, then
`gcloud iam service-accounts add-iam-policy-binding ${SA_EMAIL}
--role="roles/iam.workloadIdentityUser" --member="principalSet://..."`.

### (b) GKE Workload Identity — **preferred when gludd runs in GKE**

Bind the Kubernetes service account to the Google SA; pods get tokens from the
metadata server automatically.

```bash
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:${PROJECT_ID}.svc.id.goog[<k8s-namespace>/<k8s-sa>]"
kubectl annotate serviceaccount <k8s-sa> -n <k8s-namespace> \
  iam.gke.io/gcp-service-account="${SA_EMAIL}"
```

In the pod, mint the env token from the metadata server (no key file):

```bash
export GCP_TOKEN="$(gcloud auth print-access-token)"
```

> The connector's SSRF guard blocks `metadata.google.internal` for **backend
> endpoints**, but the gcloud/metadata token mint happens *outside* the connector,
> so Workload Identity is fully compatible.

### (c) Service-account JSON key — **least preferred (long-lived secret)**

Only if neither federation option is possible. A downloaded JSON key is a long-lived
credential — store in a secret manager, rotate, and prefer the options above.

```bash
gcloud iam service-accounts keys create /secure/gludd-sa.json \
  --iam-account="${SA_EMAIL}"
export GOOGLE_APPLICATION_CREDENTIALS=/secure/gludd-sa.json
# Mint the short-lived access token the connector expects:
export GCP_TOKEN="$(gcloud auth application-default print-access-token)"
```

> The connector reads a **token** (`GCP_TOKEN`), not the key file directly. Even in
> this mode you derive a short-lived `GCP_TOKEN` from the key — never put the JSON
> blob in `GCP_TOKEN`. `GOOGLE_APPLICATION_CREDENTIALS` only feeds the `gcloud`/ADC
> step that prints the token.

---

## Keys / URLs / Env vars table

| Env var | Meaning | How to obtain | Role it maps to |
|---|---|---|---|
| `GCP_TOKEN` (value of `config['token_env']`, default name `GCP_TOKEN`) | OAuth2 **access token** the connector sends as `Authorization: Bearer …`. Short-lived. | `gcloud auth print-access-token` (WIF / GKE WI), or `gcloud auth application-default print-access-token` (key mode), or `--impersonate-service-account` | Whatever roles are bound to the principal that minted it: `roles/logging.viewer` + `roles/monitoring.viewer` (+ `roles/cloudtrace.user` when trace lands) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a downloaded SA **JSON key file** (key-mode only; **not** read by the connector directly). | `gcloud iam service-accounts keys create` | The SA's bound roles; used only to mint `GCP_TOKEN` via ADC |
| `config['project']` | GCP project id (config, not env). | Your project id. | Resource selector; roles must be granted on this project (or its folder) |

### Endpoints the connector contacts (from `gcp_observability.py`)

| URL | Facility | Operation | Default const |
|---|---|---|---|
| `https://logging.googleapis.com/v2/entries:list` | Cloud Logging | `logging.logEntries.list` (POST) | `DEFAULT_LOGGING_ENDPOINT` (line 39) |
| `https://monitoring.googleapis.com/v3/projects/{project}/timeSeries` | Cloud Monitoring | `monitoring.timeSeries.list` (GET) | `DEFAULT_MONITORING_BASE` (line 40) + `/projects/{project}/timeSeries` |
| `https://cloudtrace.googleapis.com/v1/projects/{project}/traces` | Cloud Trace | `cloudtrace.traces.list/get` | *(planned — no const in code yet)* |

---

## Minimal grant summary (copy block)

For the **currently-wired** read facilities (logs + metrics), one service account:

```bash
PROJECT_ID="your-project-id"
SA_EMAIL="gludd-observability@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/logging.viewer" --condition=None
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/monitoring.viewer" --condition=None
# add roles/cloudtrace.user ONLY when the trace connector ships.
```

That is the complete least-privilege footprint gludd needs **today**. Trace and
compute-deploy roles are documented above for when those code paths land — do not
grant them pre-emptively.
