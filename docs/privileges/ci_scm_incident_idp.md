# Least-Privilege Access Guide — CI / SCM / Incident / Identity

This guide describes the **minimal, read-only** credentials gludd needs for each
CI, SCM, incident, and identity facility it reads telemetry from. gludd's
connectors are **read-only by design**: every connector issues `GET` requests
only, reads its auth token from an environment variable (never inline, never
logged), and validates the configured `base_url` against a literal-host SSRF
blocklist before any network call.

> **Cross-reference:** Microsoft **Entra ID sign-in logs** are covered in
> [`azure.md`](./azure.md) — do not provision a separate Entra credential here;
> the `entra_signin` source reuses the Entra app registration / role documented
> there.

## Implementation status (read this first)

Only two of the connectors below are currently implemented in
`src/general_ludd/connectors/`:

| Facility | Module | Status | Config keys (verified from source) |
|---|---|---|---|
| GitHub Actions | `github_actions.py` | **Implemented** | `repo`, `base_url`, `token_env` |
| Jenkins | `jenkins.py` | **Implemented** | `base_url`, `job`, `user_env`, `token_env` |
| PagerDuty | `pagerduty.py` | **Planned** | — |
| Opsgenie | `opsgenie.py` | **Planned** | — |
| Grafana OnCall | `grafana_oncall.py` | **Planned** | — |
| Okta | `okta.py` | **Planned** | — |
| Cloudflare | `cloudflare.py` | **Planned** | — |
| Entra sign-in | `entra_signin.py` | **Planned** (see `azure.md`) | — |

For the **Implemented** rows, the config keys, env-var names, base URLs, API
paths, and auth header format below are taken directly from the connector
source. For the **Planned** rows, the least-privilege provider guidance is
correct and copy-pasteable, and the env-var names follow gludd's established
`*_env` / `token_env` naming convention so they will line up when those
connectors land. Treat the Planned env-var names as the **recommended
convention** to adopt, not yet a source-verified contract.

Connector contract (from `connectors/base.py`): each source exposes
`health() -> dict` and `query(spec) -> list[record]`, reads its token from
`os.environ[<*_env>]` at call time, and the `is_safe_endpoint()` SSRF guard
rejects loopback / RFC-1918 / link-local / `169.254.169.254` / metadata hosts.

---

## 1. GitHub Actions  *(implemented)*

Source: `connectors/github_actions.py` — `GitHubActionsSource`.

- Base URL default: `https://api.github.com` (override via `base_url`, e.g.
  `https://github.example.com/api/v3` for GHES).
- Endpoints called (GET only):
  - `/repos/{owner}/{repo}/actions/runs` — health + workflow-run listing
  - `/repos/{owner}/{repo}/actions/runs/{run_id}/jobs` — failure drill-down
- Auth header: `Authorization: Bearer <token>` plus
  `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`.
- Token source: `os.environ[token_env]`, where `token_env` defaults to
  `GITHUB_TOKEN`.

### Minimal read-only credential

**Prefer in this order: GitHub App > fine-grained PAT > classic PAT.**

1. **GitHub App (preferred).** Create an App with **repository permissions**:
   - **Actions: Read-only**
   - **Checks: Read-only**
   - **Metadata: Read-only** (mandatory, auto-selected)
   - *No* `Contents`, *no* write of any kind.
   Install it on only the target repo(s). gludd uses the installation access
   token. Apps give short-lived tokens, per-repo install scoping, and org-level
   auditability — best for least privilege.

2. **Fine-grained PAT.** Owner = the repo's org/user; **Repository access** =
   *Only select repositories* → the target repo(s); **Permissions**:
   - **Actions: Read-only**, **Checks: Read-only**, **Metadata: Read-only**.
   Set the shortest acceptable expiry.

3. **Classic PAT (last resort).** Cannot scope below `repo`; the narrowest is
   the `repo` scope (which grants write). Avoid unless on legacy GHES without
   fine-grained support. Use a dedicated bot account, not a human's.

### Where / how to apply

- **App:** GitHub → Settings → Developer settings → **GitHub Apps** → New GitHub
  App → set the three read permissions → Install on the repo.
  API: `POST /app/installations/{id}/access_tokens`.
- **Fine-grained PAT:** GitHub → Settings → Developer settings → **Personal
  access tokens → Fine-grained tokens** → Generate new token.
- **GHES:** same paths under your enterprise host; set `base_url` to
  `https://<host>/api/v3`.

### Keys / URLs / env vars

| Env var | Meaning | How to obtain | Scope / role it maps to |
|---|---|---|---|
| `GITHUB_TOKEN` (value of `token_env`) | Bearer token | App install token, or fine-grained PAT | Actions:read + Checks:read + Metadata:read |
| `base_url` (config) | API host | Default `https://api.github.com`; GHES `https://<host>/api/v3` | — |
| `repo` (config) | `owner/name` target | — | — |
| `token_env` (config) | Name of the env var to read | — | indirection only |

### Read-only verification curl

```bash
curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" \
     -H "Accept: application/vnd.github+json" \
     -H "X-GitHub-Api-Version: 2022-11-28" \
     "https://api.github.com/repos/OWNER/REPO/actions/runs?per_page=1"
# Expect HTTP 200 + a workflow_runs array. A 403/404 means the token lacks Actions:read.
```

---

## 2. Jenkins  *(implemented)*

Source: `connectors/jenkins.py` — `JenkinsSource`.

- Base URL: `base_url` (required, no default; http or https).
- Endpoints called (GET only):
  - `/api/json?tree=builds[number,result,timestamp,url,duration]` (no `job`), or
  - `/job/{job}/api/json?tree=...` (when `job` is set)
- Auth header: HTTP **Basic** — `Authorization: Basic base64(user:token)`,
  `Accept: application/json`.
- Credentials: `os.environ[user_env]` / `os.environ[token_env]`, defaulting to
  `JENKINS_USER` / `JENKINS_TOKEN`.

### Minimal read-only credential

Create a **dedicated service user** and grant only read permissions via the
**Matrix Authorization** or **Role-Based Strategy** plugin:

- **Overall/Read** (required to reach the API at all)
- **Job/Read** (list jobs and builds)
- **Build/Read** (read build results)
- Grant **nothing** else — no `Job/Build`, `Job/Configure`, `Run/Update`,
  `Overall/Administer`, etc.

Then create that user's **API token** (Basic-auth password): the user is the
`JENKINS_USER`, the API token is the `JENKINS_TOKEN`. Never use the account
password directly.

### Where / how to apply

- **Permissions:** Manage Jenkins → **Security** → Authorization → *Matrix-based
  security* (or *Role-Based Strategy* → Manage Roles → add a `gludd-readonly`
  role with the three read perms → Assign Roles).
- **API token:** log in as the service user → **People** → user → **Configure**
  → **API Token** → *Add new token* → copy once.
  API: `POST /me/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken`.

### Keys / URLs / env vars

| Env var | Meaning | How to obtain | Scope / role it maps to |
|---|---|---|---|
| `JENKINS_USER` (value of `user_env`) | Basic-auth username | The service account login | identity half of Basic auth |
| `JENKINS_TOKEN` (value of `token_env`) | Basic-auth secret | User → Configure → API Token | Overall/Read + Job/Read + Build/Read |
| `base_url` (config) | Jenkins root URL | e.g. `https://jenkins.example.com` | — |
| `job` (config, optional) | Single job to scope to | — | narrows reads to one job |

### Read-only verification curl

```bash
curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" \
     "https://jenkins.example.com/api/json?tree=builds[number,result,timestamp]"
# Expect HTTP 200 + JSON. A 403 means the user lacks Overall/Read or Job/Read.
```

---

## 3. PagerDuty  *(planned)*

- Base URL: `https://api.pagerduty.com`.
- Read endpoints gludd will use: `GET /incidents`, `GET /oncalls`,
  `GET /services`, `GET /abilities` (health probe).
- Auth header: `Authorization: Token token=<key>`,
  `Accept: application/vnd.pagerduty+json;version=2`.

### Minimal read-only credential

Prefer a **read-only REST API key** (account-level):

- PagerDuty → **Integrations → API Access Keys** → *Create New API Key* → tick
  **Read-only API Key** → Create. This key can only `GET`.

If you must scope to a single user instead, use a **User Token** for a user
whose base role is **Observer** (read-only) — but the read-only REST key is
preferred because it carries no user write paths at all.

### Where / how to apply

- **Console:** Integrations → API Access Keys (admin required to mint).
- **API:** all reads use the same `Authorization: Token token=...` header.

### Keys / URLs / env vars

| Env var | Meaning | How to obtain | Scope / role it maps to |
|---|---|---|---|
| `PAGERDUTY_TOKEN` (recommended `token_env`) | REST API key | Integrations → API Access Keys → Read-only | Read-only REST (GET-only) |
| `base_url` (config) | API host | `https://api.pagerduty.com` | — |

### Read-only verification curl

```bash
curl -sS -H "Authorization: Token token=$PAGERDUTY_TOKEN" \
     -H "Accept: application/vnd.pagerduty+json;version=2" \
     "https://api.pagerduty.com/incidents?limit=1"
# Expect HTTP 200. A read-only key returns data on GET and 403 on any write.
```

---

## 4. Opsgenie  *(planned)*

- Base URL: `https://api.opsgenie.com` (EU: `https://api.eu.opsgenie.com`).
- Read endpoints gludd will use: `GET /v2/alerts`, `GET /v2/alerts/{id}`.
- Auth header: `Authorization: GenieKey <key>`.

### Minimal read-only credential

Create an **API Integration** (not a user) and grant only read:

- Opsgenie → **Settings → Integrations → Add → API**.
- Under **Access**: enable **Read Access** only; **disable** Create & Update,
  Delete, and configuration-access toggles.
- Restrict the integration to the relevant team(s) if team-scoped.

This yields a `GenieKey` that can list/read alerts but not acknowledge, close,
create, or reconfigure anything.

### Where / how to apply

- **Console:** Settings → Integrations → API integration → uncheck all write
  toggles, keep Read Access.
- **API:** `Authorization: GenieKey <key>` on every request.

### Keys / URLs / env vars

| Env var | Meaning | How to obtain | Scope / role it maps to |
|---|---|---|---|
| `OPSGENIE_TOKEN` (recommended `token_env`) | API integration key | Settings → Integrations → API → Read Access | Read-only alert access |
| `base_url` (config) | API host | `https://api.opsgenie.com` (or EU) | — |

### Read-only verification curl

```bash
curl -sS -H "Authorization: GenieKey $OPSGENIE_TOKEN" \
     "https://api.opsgenie.com/v2/alerts?limit=1"
# Expect HTTP 200 + a data array. A 403 means the integration has no Read Access.
```

---

## 5. Grafana OnCall  *(planned)*

- Base URL: your Grafana OnCall API host, e.g.
  `https://oncall-prod-us-central-0.grafana.net/oncall` (Grafana Cloud) or your
  self-hosted OnCall URL.
- Read endpoints gludd will use: `GET /api/v1/alert_groups`,
  `GET /api/v1/schedules`.
- Auth header: `Authorization: <token>` (raw token, no `Bearer` prefix).

### Minimal read-only credential

Grafana OnCall API tokens inherit the **Grafana user's role**, so least
privilege is enforced at the user, not the token:

- Create/choose a Grafana user with the **Viewer** org role (read-only).
- As that user: Grafana OnCall → **Settings → API Keys → + Create** → copy the
  token. A Viewer-derived token can list alert groups and schedules but cannot
  acknowledge/resolve or edit them.

Do **not** mint the token as an Admin/Editor.

### Where / how to apply

- **Console:** Grafana OnCall → Settings → API Keys.
- **API:** `Authorization: <token>` header on each request.

### Keys / URLs / env vars

| Env var | Meaning | How to obtain | Scope / role it maps to |
|---|---|---|---|
| `GRAFANA_ONCALL_TOKEN` (recommended `token_env`) | OnCall API token | OnCall → Settings → API Keys (as a Viewer user) | Read-only (Viewer-derived) |
| `base_url` (config) | OnCall API host | from OnCall → Settings → API URL | — |

### Read-only verification curl

```bash
curl -sS -H "Authorization: $GRAFANA_ONCALL_TOKEN" \
     "$GRAFANA_ONCALL_BASE/api/v1/alert_groups?perpage=1"
# Expect HTTP 200 + results. A 403 means the underlying user is not at least Viewer.
```

---

## 6. Okta  *(planned)*

- Base URL: your org, e.g. `https://<org>.okta.com` (or `.oktapreview.com`).
- Read endpoint gludd will use: `GET /api/v1/logs` (System Log).
- Auth header: `Authorization: SSWS <api_token>`.

### Minimal read-only credential

Okta API tokens inherit the **admin role of the user that mints them**, so scope
the *user*, not the token:

1. Create a dedicated service user.
2. **Security → Administrators → Roles** → assign a **Custom Admin Role** whose
   only permission is **View System Log** (`okta.logs.read`), with a Resource Set
   limited to what gludd needs (the org / log resource). Avoid the built-in
   *Read-Only Administrator* — it is broader than `okta.logs.read`; use it only
   if a custom role is not available.
3. As that service user: **Security → API → Tokens → Create Token** → copy once.
   Pin the token's network zone if possible.

### Where / how to apply

- **Custom role:** Admin console → Security → Administrators → **Roles** → Create
  new role → grant only **View System Log** → Resource Set = the org.
- **Token:** Security → **API → Tokens → Create Token** (as the scoped user).
- **API:** `Authorization: SSWS <token>`.

### Keys / URLs / env vars

| Env var | Meaning | How to obtain | Scope / role it maps to |
|---|---|---|---|
| `OKTA_TOKEN` (recommended `token_env`) | SSWS API token | Security → API → Tokens (as scoped user) | Custom admin role: `okta.logs.read` |
| `base_url` (config) | Okta org URL | `https://<org>.okta.com` | — |

### Read-only verification curl

```bash
curl -sS -H "Authorization: SSWS $OKTA_TOKEN" \
     -H "Accept: application/json" \
     "https://<org>.okta.com/api/v1/logs?limit=1"
# Expect HTTP 200 + a log array. A 403 means the minting user lacks okta.logs.read.
```

---

## 7. Cloudflare  *(planned)*

- Base URL: `https://api.cloudflare.com/client/v4`.
- Read endpoints gludd will use (depending on scope):
  - `GET /accounts/{account_id}/audit_logs` — audit logs
  - `GET /accounts/{account_id}/logs/...` / `GET /zones/{zone_id}/analytics/...`
- Auth header: `Authorization: Bearer <api_token>` (a **scoped API Token**, NOT
  the Global API Key).

### Minimal read-only credential

Always use a **scoped API Token**, never the Global API Key (which is
account-wide read/write). Cloudflare → **My Profile → API Tokens → Create Token
→ Create Custom Token** and grant only the read permissions needed, scoped to
the specific account/zone:

- **Account → Audit Logs → Read** (for audit-log ingestion), and/or
- **Account → Logs → Read** / **Zone → Logs → Read**, and/or
- **Zone → Analytics → Read** / **Account Analytics → Read**.
- **Account/Zone Resources:** *Include → specific* account/zone only — never
  "All accounts".
- Set a TTL and, if practical, an IP-address filter.

### Where / how to apply

- **Console:** My Profile → API Tokens → Create Custom Token → add only the
  read permissions above → restrict resources → Continue → Create.
- **API:** `Authorization: Bearer <token>` on every request.

### Keys / URLs / env vars

| Env var | Meaning | How to obtain | Scope / role it maps to |
|---|---|---|---|
| `CLOUDFLARE_TOKEN` (recommended `token_env`) | Scoped API token | My Profile → API Tokens → Custom Token | Audit Logs Read / Logs Read / Analytics Read |
| `CLOUDFLARE_ACCOUNT_ID` (config/env) | Account scope | Dashboard → account overview | — |
| `base_url` (config) | API host | `https://api.cloudflare.com/client/v4` | — |

### Read-only verification curl

```bash
# Verify the token is valid and read-only-scoped:
curl -sS -H "Authorization: Bearer $CLOUDFLARE_TOKEN" \
     "https://api.cloudflare.com/client/v4/user/tokens/verify"
# Then exercise the actual read scope:
curl -sS -H "Authorization: Bearer $CLOUDFLARE_TOKEN" \
     "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/audit_logs?per_page=1"
# Expect HTTP 200 with "success": true. A 403/9109 means the token lacks the read permission.
```

---

## 8. Entra sign-in logs  *(planned — see azure.md)*

The `entra_signin` source reads Microsoft Entra ID **sign-in logs** via Microsoft
Graph (`GET https://graph.microsoft.com/v1.0/auditLogs/signIns`), which requires
the application permission **`AuditLog.Read.All`** (plus `Directory.Read.All`)
and, ideally, the **Reports Reader** / **Security Reader** directory role.

**Provisioning for Entra is documented in [`azure.md`](./azure.md).** Do not
create a separate app registration here — reuse the Entra credential and app
registration defined in that guide, and (when the connector lands) point its
`*_env` token variable at the same secret.
