# Least-Privilege Access Guide — Observability Backends gludd Reads From

> **Status:** Uncommitted working draft. DOC-ONLY.
> **Scope:** This guide grants the *minimum* token/role scope needed for gludd's
> connectors to **read** the telemetry they query — logs, metrics, traces, issues.
> gludd never writes, never deletes, never administers. Every credential below is
> **read-only by design**; if a console forces you to pick a broader role, that is
> a finding to escalate, not a default to accept.

## How gludd consumes these credentials (read this first)

gludd's connectors live in `src/general_ludd/connectors/`. Every connector follows
the same security contract (see `connectors/base.py` and each module's docstring):

- **Secrets are never hardcoded and never stored on the connector instance.** Each
  config key ending in `*_env` (e.g. `api_key_env`, `token_env`, `app_key_env`)
  holds the **name of an environment variable**, not the secret itself. gludd reads
  `os.environ[<that name>]` at *call time* and injects it into a request header.
  - Example: config `api_key_env: "DD_API_KEY"` → gludd reads `os.environ["DD_API_KEY"]`
    → sends header `DD-API-KEY: <value>`.
- **Read-only HTTP surface.** Connectors only call search/query/health endpoints
  (POST search bodies, GET query params). No mutating endpoints are referenced.
- **SSRF guard.** `is_safe_endpoint()` / `_validate_site()` reject loopback,
  RFC-1918 private, link-local, and cloud-metadata literal hosts. Plain `http` is
  permitted for internal-but-allowlisted backends; only `http`/`https` schemes pass.
- **`health()` never raises.** Each connector probes a lightweight,
  read-only validation/status endpoint to confirm the credential works.

**Implication for least privilege:** because gludd only ever calls the read paths
listed per-backend below, you can scope every token to exactly those paths/indices.

### Connectors actually present in gludd today

| Backend | Connector file | Reads |
|---|---|---|
| Datadog | `datadog.py` | logs + metrics |
| Grafana Loki | `grafana_loki.py` | logs (LogQL) |
| Splunk | `splunk.py` | logs (SPL) |
| Elasticsearch | `elasticsearch.py` | logs / traces (`_search`) |
| SigNoz | `signoz.py` | traces (query_range) |
| Sentry | `sentry.py` | issues + latest events |
| Graylog | `graylog.py` | logs (universal search) |
| Prometheus | `prometheus.py` | metrics (PromQL) |

### Connectors named in the request but **NOT present** in gludd

`tempo.py`, `victoriametrics.py`, `thanos.py`, `jaeger.py`, `zipkin.py`,
`parca.py`, `pyroscope.py`, `kafka_exporter.py` — **no connector module exists**
for these as of this writing. They are covered in the
[Backends without a gludd connector](#backends-without-a-gludd-connector-yet)
section with forward-looking least-privilege guidance, because operators commonly
front them with the same reverse-proxy + read-token pattern and gludd already has
the generic PromQL (`prometheus.py`) and trace shapes those backends speak.

---

## Datadog

**Connector:** `datadog.py` — reads **logs** (Logs Search API v2) and **metrics**
(Query Timeseries v1). Default `site` = `https://api.datadoghq.com` (override for
EU/US3/US5/AP1/gov sites, e.g. `https://api.datadoghq.eu`).

**Endpoints gludd calls (all read-only):**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v2/logs/events/search` | Log events search |
| GET | `/api/v1/query` | Metrics timeseries query |
| GET | `/api/v1/validate` | `health()` key validation |

**Auth:** two headers — `DD-API-KEY` (from `api_key_env`) and
`DD-APPLICATION-KEY` (from `app_key_env`).

### 1. Minimal scope (READ ONLY)

Datadog needs **both** an API key and an Application key. The **Application key**
carries the scopes — restrict it to:

- `logs_read_data` — read log events (required for `/api/v2/logs/events/search`).
- `timeseries_query` — query metrics timeseries (required for `/api/v1/query`).
- `metrics_read` — read metric metadata if your org enforces it.

Do **NOT** grant: `logs_write_*`, `metrics_write`, `dashboards_write`,
`user_access_manage`, `api_keys_write`, or any `*_manage` / admin scope.

### 2. Exact artifact to create (scoped Application key)

Create the key **scoped** (Datadog scoped App keys, GA):

```bash
# Create an Application key restricted to read scopes only.
# Requires an existing API key + a temporary scoped-enough App key to bootstrap.
curl -s -X POST "https://api.datadoghq.com/api/v2/application_keys" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_BOOTSTRAP_APP_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "type": "application_keys",
      "attributes": {
        "name": "gludd-readonly",
        "scopes": ["logs_read_data", "timeseries_query", "metrics_read"]
      }
    }
  }'
```

### 3. Where/how to apply

- **Console:** Organization Settings → **API Keys** (create the API key) and
  → **Application Keys** → *New Key* → set **Scopes** to the three read scopes
  above. Assign the key to a service account / low-privilege user, not an admin.
- **API/CLI:** the `POST /api/v2/application_keys` call above with `attributes.scopes`.

### 4. Keys / URLs / env vars

| Env var (you choose the name; put it in `api_key_env`/`app_key_env`) | Meaning | How to obtain | Scope it maps to |
|---|---|---|---|
| `DD_API_KEY` | Datadog API key → `DD-API-KEY` header | Org Settings → API Keys → New Key | Identifies the org; no per-scope grant |
| `DD_APP_KEY` | Datadog Application key → `DD-APPLICATION-KEY` header | Org Settings → Application Keys → New Key (scoped) | `logs_read_data`, `timeseries_query`, `metrics_read` |
| `site` (config) | API base URL | Pick your DD region | n/a |

### Read-only verification curl

```bash
# Should return {"valid": true} for a working read key:
curl -s "https://api.datadoghq.com/api/v1/validate" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}"

# Confirm logs_read_data works (read path gludd uses):
curl -s -X POST "https://api.datadoghq.com/api/v2/logs/events/search" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"filter":{"query":"*","from":"now-15m","to":"now"},"page":{"limit":1}}'
```

---

## Grafana Loki

**Connector:** `grafana_loki.py` — reads **logs** via LogQL range queries.
Config: `base_url` (required), `token_env` (optional bearer token).

**Endpoints gludd calls (read-only):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/loki/api/v1/query_range` | LogQL range query |
| GET | `/ready` | `health()` readiness probe |

**Auth:** `Authorization: Bearer <token>` (only sent when `token_env` is set and
the env var is non-empty). Loki itself is frequently unauthenticated behind a
reverse proxy — see verification note below.

### 1. Minimal scope (READ ONLY)

Two deployment shapes:

- **Grafana Cloud Loki / Grafana-managed datasource:** create a **Grafana
  service account** with **Viewer** role, then add a **service-account token**.
  Viewer grants *read* on datasources (Loki query) without edit/admin. Do NOT use
  Editor or Admin. If using Grafana Cloud Access Policies, scope to
  `logs:read` only.
- **Self-hosted Loki behind a proxy:** Loki's own API has no RBAC; enforce
  read-only at the **reverse proxy** — allow only `GET /loki/api/v1/query*`,
  `GET /loki/api/v1/labels`, `GET /ready`; deny `POST /loki/api/v1/push` and all
  admin/flush/ring endpoints. Issue gludd a bearer/basic credential the proxy maps
  to that read-only allowlist.

### 2. Exact artifact to create (Grafana service account)

- Grafana → **Administration → Users and access → Service accounts → Add service
  account** → name `gludd-loki-ro`, **Role: Viewer**.
- On the SA → **Add service account token** → copy the token (`glsa_...`).
- (Grafana Cloud) Access Policies → new policy with realm = your stack, scope
  `logs:read`; generate a token under it.

### 3. Where/how to apply

- Point `base_url` at the Loki query frontend (or the Grafana datasource-proxy URL).
- Store the `glsa_...` token in the env var named by `token_env`.
- Reverse-proxy operators: encode the read-only allowlist in nginx/Envoy and map
  the token there.

### 4. Keys / URLs / env vars

| Env var (name set in `token_env`) | Meaning | How to obtain | Scope it maps to |
|---|---|---|---|
| `LOKI_TOKEN` | Bearer token → `Authorization: Bearer` | Grafana SA token (Viewer) or proxy-issued read token | `logs:read` / Viewer |
| `base_url` (config) | Loki query endpoint | Your Loki/Grafana datasource URL | n/a |

### Read-only verification curl

```bash
# Readiness (health()):
curl -s "${LOKI_BASE_URL}/ready"

# Range query (the read path gludd uses); omit the header if Loki is open behind a proxy:
curl -s -G "${LOKI_BASE_URL}/loki/api/v1/query_range" \
  -H "Authorization: Bearer ${LOKI_TOKEN}" \
  --data-urlencode 'query={job="varlogs"}' \
  --data-urlencode 'limit=1'
```

---

## Splunk

**Connector:** `splunk.py` — reads **logs** via a oneshot SPL search.
Config: `base_url` (required), `token_env` (required).

**Endpoints gludd calls (read-only):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/services/server/info` | `health()` |
| POST | `/services/search/jobs` | Oneshot SPL search (`exec_mode=oneshot`) |

**Auth:** `Authorization: Bearer <token>` (Splunk authentication token).

> Note: `POST /services/search/jobs` is how Splunk *runs a search* — it is a read
> operation against indexed data, not a write to an index. Scope the role so it can
> only search the indexes gludd needs and can do nothing else.

### 1. Minimal scope (READ ONLY)

Create a dedicated Splunk **role** that grants only `search` plus read on the
specific indexes, and a least-privilege **user** mapped to it (or inheriting it):

- Capability: **`search`** only. Do **not** grant `admin_all_objects`,
  `edit_*`, `delete_by_keyword`, `indexes_edit`, `schedule_search`, or `rtsearch`
  unless real-time is required.
- `srchIndexesAllowed` = the exact index list gludd queries (e.g. `main;app_logs`).
- `srchIndexesDefault` = same list (so an unqualified SPL still works).
- Set `srchJobsQuota` / `srchDiskQuota` low to cap impact.

### 2. Exact artifact to create (`authorize.conf` stanza)

`$SPLUNK_HOME/etc/system/local/authorize.conf` (or an app's `local/`):

```ini
[role_gludd_readonly]
importRoles = user
srchIndexesAllowed = main;app_logs
srchIndexesDefault = main;app_logs
srchJobsQuota = 3
srchDiskQuota = 100
rtSrchJobsQuota = 0
# Grant ONLY the search capability — everything else stays off by default.
search = enabled
# Explicitly deny mutation-adjacent capabilities:
schedule_search = disabled
indexes_edit = disabled
admin_all_objects = disabled
delete_by_keyword = disabled
```

Then create the user and assign the role (Splunk Web: **Settings → Users →
New User**, role = `gludd_readonly`), and issue that user an **authentication
token** (**Settings → Tokens → New Token**, set an expiry).

### 3. Where/how to apply

- **Console:** Settings → Access Controls → **Roles** (create `gludd_readonly`),
  → **Users** (create user, assign role), → **Tokens** (mint token).
- **CLI/conf:** drop the `authorize.conf` stanza above and restart/reload auth.
- Set `base_url` to the Splunk management/search-head URL (typically `:8089`).

### 4. Keys / URLs / env vars

| Env var (name in `token_env`) | Meaning | How to obtain | Scope it maps to |
|---|---|---|---|
| `SPLUNK_TOKEN` | Bearer auth token → `Authorization: Bearer` | Settings → Tokens → New Token (as the `gludd_readonly` user) | role `search` + `srchIndexesAllowed` |
| `base_url` (config) | Splunk REST endpoint | e.g. `https://splunk.internal:8089` | n/a |

### Read-only verification curl

```bash
# health() endpoint:
curl -s "${SPLUNK_BASE_URL}/services/server/info?output_mode=json" \
  -H "Authorization: Bearer ${SPLUNK_TOKEN}"

# Oneshot search over an allowed index (read path gludd uses):
curl -s -X POST "${SPLUNK_BASE_URL}/services/search/jobs" \
  -H "Authorization: Bearer ${SPLUNK_TOKEN}" \
  -d output_mode=json -d exec_mode=oneshot \
  -d search='search index=main | head 1'
```

---

## Elasticsearch

**Connector:** `elasticsearch.py` — reads **logs / traces** via `_search`.
Config: `base_url` (required), `index` (required), `api_key_env` (ApiKey auth)
**or** `token_env` (Bearer auth). Default `time_field` = `@timestamp`.

**Endpoints gludd calls (read-only):**

| Method | Path | Purpose |
|---|---|---|
| POST | `/{index}/_search` | Query DSL / query-string search |
| GET | `/_cluster/health` | `health()` |

**Auth:** `Authorization: ApiKey <value>` (if `api_key_env` set) or
`Authorization: Bearer <value>` (if `token_env` set).

### 1. Minimal scope (READ ONLY)

A security **role** granting only `read` + `view_index_metadata` on the target
index pattern. `read` covers `_search`; `view_index_metadata` lets the connector
resolve mappings/aliases. The connector also needs **cluster** privilege
`monitor` for `/_cluster/health`. No `write`, `create`, `delete`, `manage`, or
`all`.

### 2. Exact artifact to create (security role JSON)

```json
PUT /_security/role/gludd_readonly
{
  "cluster": ["monitor"],
  "indices": [
    {
      "names": ["app-logs-*", "traces-*"],
      "privileges": ["read", "view_index_metadata"]
    }
  ]
}
```

Then mint an **API key** restricted to that role (preferred over Bearer/user auth):

```json
POST /_security/api_key
{
  "name": "gludd-readonly",
  "role_descriptors": {
    "gludd_readonly": {
      "cluster": ["monitor"],
      "indices": [
        { "names": ["app-logs-*", "traces-*"],
          "privileges": ["read", "view_index_metadata"] }
      ]
    }
  }
}
```

The response `encoded` field is the value for the `ApiKey` header → put it in the
env var named by `api_key_env`.

### 3. Where/how to apply

- **Kibana:** Stack Management → Security → **Roles** (create `gludd_readonly`)
  → **API keys** (create restricted key). Or use the `_security` REST calls above.
- Set `index` to the exact pattern/index gludd queries; keep the role's `names`
  matching it (no `*`-everything).

### 4. Keys / URLs / env vars

| Env var | Meaning | How to obtain | Scope it maps to |
|---|---|---|---|
| `ES_API_KEY` (name in `api_key_env`) | `Authorization: ApiKey` value | `POST /_security/api_key` `encoded` field | role `read` + `view_index_metadata` + cluster `monitor` |
| `ES_TOKEN` (name in `token_env`) | `Authorization: Bearer` value (alt) | OAuth/SSO token for a read-only user | same role |
| `base_url`, `index` (config) | Cluster URL + target index | Your ES endpoint + index pattern | n/a |

### Read-only verification curl

```bash
# Cluster health (health()):
curl -s "${ES_BASE_URL}/_cluster/health" \
  -H "Authorization: ApiKey ${ES_API_KEY}"

# Search the target index (read path gludd uses):
curl -s -X POST "${ES_BASE_URL}/app-logs-2026/_search" \
  -H "Authorization: ApiKey ${ES_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"size":1,"query":{"match_all":{}}}'
```

---

## SigNoz

**Connector:** `signoz.py` — reads **traces** via query_range.
Config: `base_url` (required), `token_env`.

**Endpoints gludd calls (read-only):**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v3/query_range` | Trace/metric/log query range |
| GET | `/api/v1/version` | `health()` |

**Auth:** sends both `SIGNOZ-API-KEY: <token>` and `Authorization: Bearer <token>`
(the latter helps proxy setups). Both come from the single `token_env`.

### 1. Minimal scope (READ ONLY)

SigNoz API keys carry a role. Create the key with the **Viewer** role — read-only
access to query traces/metrics/logs. Do NOT use **Admin** or **Editor** (those can
manage dashboards, alerts, users, ingestion keys).

### 2. Exact artifact to create (SigNoz API key, Viewer)

- SigNoz UI → **Settings → API Keys → New Key** → **Role: Viewer** → set an
  expiry → copy the key.
- Self-hosted behind a proxy with no SigNoz RBAC: front `/api/v3/query_range` and
  `/api/v1/version` with a read-only allowlist at the proxy and map a Bearer token
  there (the connector already sends `Authorization: Bearer`).

### 3. Where/how to apply

- Store the key in the env var named by `token_env`.
- Set `base_url` to your SigNoz query-service URL.

### 4. Keys / URLs / env vars

| Env var (name in `token_env`) | Meaning | How to obtain | Scope it maps to |
|---|---|---|---|
| `SIGNOZ_TOKEN` | Sent as `SIGNOZ-API-KEY` and `Authorization: Bearer` | Settings → API Keys → New Key (Viewer) | Viewer (read traces/metrics/logs) |
| `base_url` (config) | SigNoz query URL | Your SigNoz endpoint | n/a |

### Read-only verification curl

```bash
# health() version probe:
curl -s "${SIGNOZ_BASE_URL}/api/v1/version" \
  -H "SIGNOZ-API-KEY: ${SIGNOZ_TOKEN}"

# Trace query_range (read path gludd uses) — adjust the body to your schema:
curl -s -X POST "${SIGNOZ_BASE_URL}/api/v3/query_range" \
  -H "SIGNOZ-API-KEY: ${SIGNOZ_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"start":0,"end":0,"compositeQuery":{"queryType":"builder","panelType":"list","builderQueries":{}}}'
```

---

## Sentry

**Connector:** `sentry.py` — reads **issues** and the **latest event** per issue.
Config: `token_env` (required), `org` (required), `project` (required),
`base_url` (default `https://sentry.io`).

**Endpoints gludd calls (read-only):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/0/` | `health()` API root |
| GET | `/api/0/projects/{org}/{project}/issues/` | List issues |
| GET | `/api/0/issues/{issue_id}/events/latest/` | Latest event for an issue |

**Auth:** `Authorization: Bearer <token>`.

### 1. Minimal scope (READ ONLY)

An **Organization Auth Token** (or Internal Integration token) with only:

- `project:read`
- `event:read`

That covers listing issues and reading the latest event. Do **NOT** grant
`project:write`, `project:admin`, `event:write`, `member:*`, `org:write`, or
`team:write`.

### 2. Exact artifact to create (org auth token)

- Sentry → **Settings → Auth Tokens** (org-level) → **Create New Token** → enable
  scopes **`project:read`** and **`event:read`** only → copy `sntrys_...`.
- Or **Settings → Custom Integrations → Internal Integration** → set permissions
  Project = *Read*, Issue & Event = *Read*; use its token.

### 3. Where/how to apply

- Store the token in the env var named by `token_env`.
- Set `org` and `project` to the target slugs; `base_url` only for self-hosted
  Sentry (`https://sentry.example.com`).

### 4. Keys / URLs / env vars

| Env var (name in `token_env`) | Meaning | How to obtain | Scope it maps to |
|---|---|---|---|
| `SENTRY_TOKEN` | `Authorization: Bearer` value | Settings → Auth Tokens → New (read scopes) | `project:read`, `event:read` |
| `org`, `project` (config) | Org + project slugs | Your Sentry org/project | n/a |
| `base_url` (config) | API root (self-hosted only) | Your Sentry URL | n/a |

### Read-only verification curl

```bash
# health() API root:
curl -s "https://sentry.io/api/0/" \
  -H "Authorization: Bearer ${SENTRY_TOKEN}"

# List issues (read path gludd uses):
curl -s "https://sentry.io/api/0/projects/${SENTRY_ORG}/${SENTRY_PROJECT}/issues/?query=is:unresolved&limit=1" \
  -H "Authorization: Bearer ${SENTRY_TOKEN}"
```

---

## Graylog

**Connector:** `graylog.py` — reads **logs** via universal search.
Config: `base_url` (required), `token_env` (required).

**Endpoints gludd calls (read-only):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/system/lbstatus` | `health()` |
| GET | `/api/search/universal/relative` | Relative-window search |
| GET | `/api/search/universal/absolute` | Absolute-window search (when `from`/`to` set) |

**Auth:** HTTP **Basic** — `Authorization: Basic base64("<token>:token")`
(username = the API token, password = the literal string `token`). This is
Graylog's standard token-as-username scheme.

### 1. Minimal scope (READ ONLY)

Create a Graylog **read-only user** (or a custom role) that can only run searches
on the needed streams:

- Built-in **Reader** permission set (`*:read` equivalents for search/streams),
  scoped to the specific **streams** gludd queries — not the global "Admin" role.
- Grant the role only the stream(s) needed; deny dashboard/stream/user
  management.
- Generate the **API token** *as that read-only user* (tokens inherit the user's
  permissions).

### 2. Exact artifact to create (read-only user + token)

- Graylog → **System → Authentication → Users → Create User** →
  assign the **Reader** role (and stream-read permissions for the target streams).
- As that user (or via admin on their behalf): **profile → Edit tokens →
  Create token** named `gludd-ro` → copy it. This token is the Basic-auth username.

### 3. Where/how to apply

- Store the token in the env var named by `token_env`.
- Set `base_url` to the Graylog REST API root (e.g. `https://graylog.internal:9000`).

### 4. Keys / URLs / env vars

| Env var (name in `token_env`) | Meaning | How to obtain | Scope it maps to |
|---|---|---|---|
| `GRAYLOG_TOKEN` | Basic-auth username (password=`token`) | User profile → Edit tokens → Create token (read-only user) | Reader role + stream read |
| `base_url` (config) | Graylog REST URL | Your Graylog endpoint | n/a |

### Read-only verification curl

```bash
# health() load-balancer status (token as username, literal 'token' as password):
curl -s -u "${GRAYLOG_TOKEN}:token" \
  "${GRAYLOG_BASE_URL}/api/system/lbstatus"

# Relative universal search (read path gludd uses):
curl -s -u "${GRAYLOG_TOKEN}:token" \
  -H "Accept: application/json" \
  "${GRAYLOG_BASE_URL}/api/search/universal/relative?query=*&range=300&limit=1"
```

---

## Prometheus

**Connector:** `prometheus.py` — reads **metrics** via PromQL instant/range
queries. Config: `base_url` (required), `token_env` (optional bearer token).

**Endpoints gludd calls (read-only):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/query` | Instant PromQL query (`health()` also probes here) |
| GET | `/api/v1/query_range` | Range PromQL query |

**Auth:** `Authorization: Bearer <token>` only when `token_env` is set and the
env var is non-empty. Open-source Prometheus has **no built-in auth/RBAC**, so
least privilege is enforced at the network edge.

### 1. Minimal scope (READ ONLY)

- **Network access** to the Prometheus HTTP API is the real grant. Restrict gludd's
  source IP/namespace to the Prometheus endpoint via firewall/NetworkPolicy.
- Put a **reverse proxy** (nginx / Envoy / oauth2-proxy) in front and allow only
  the read query paths: `GET /api/v1/query`, `GET /api/v1/query_range`,
  `GET /api/v1/labels`, `GET /api/v1/series`. **Deny** `/api/v1/admin/*` (TSDB
  delete/clean), `/-/reload`, `/-/quit`. Run Prometheus with
  `--web.enable-admin-api=false` and without `--web.enable-lifecycle`.
- Issue gludd a **read-only Bearer/Basic credential** the proxy maps to that
  allowlist.

### 2. Exact artifact to create (reverse-proxy read-only allowlist)

```nginx
# nginx: read-only Prometheus front for gludd
location ~ ^/api/v1/(query|query_range|labels|series|label/.*/values)$ {
    auth_request /authz;            # validates the Bearer token gludd sends
    proxy_pass http://prometheus:9090;
}
location / { return 403; }          # everything else denied (admin, reload, quit)
```

### 3. Where/how to apply

- Point `base_url` at the proxy URL (not the raw Prometheus port).
- Store the proxy-issued token in the env var named by `token_env` (omit
  `token_env` entirely if the endpoint is unauthenticated on a locked-down network).

### 4. Keys / URLs / env vars

| Env var (name in `token_env`) | Meaning | How to obtain | Scope it maps to |
|---|---|---|---|
| `PROM_TOKEN` | `Authorization: Bearer` value (optional) | Reverse-proxy / oauth2-proxy read token | Query-paths-only allowlist |
| `base_url` (config) | Prometheus (proxy) URL | Your proxied endpoint | n/a |

### Read-only verification curl

```bash
# Instant query (also what health() exercises). Omit the header if unauthenticated:
curl -s -G "${PROM_BASE_URL}/api/v1/query" \
  -H "Authorization: Bearer ${PROM_TOKEN}" \
  --data-urlencode 'query=up'

# Range query (read path gludd uses):
curl -s -G "${PROM_BASE_URL}/api/v1/query_range" \
  -H "Authorization: Bearer ${PROM_TOKEN}" \
  --data-urlencode 'query=up' \
  --data-urlencode 'start=1700000000' --data-urlencode 'end=1700000300' \
  --data-urlencode 'step=60'
```

---

## Backends without a gludd connector (yet)

> **No connector module exists** in `src/general_ludd/connectors/` for the
> following. The guidance here is **forward-looking**: it follows the same
> `base_url` + optional `token_env` Bearer/Basic pattern gludd already uses for
> Prometheus, so when these land they can be fronted identically. **Do not
> provision these credentials for gludd today** — there is nothing to consume them.

For **all** of the below, the common least-privilege recipe is:

1. **Network access** to the read API (firewall / NetworkPolicy scoping gludd's
   source to the endpoint) is the primary grant — none of these ship per-query
   RBAC out of the box.
2. **Reverse-proxy auth** (nginx / Envoy / oauth2-proxy) fronting the endpoint,
   allowlisting only the read query paths and denying admin/ingest/delete paths.
3. A **read-only Bearer or Basic token** the proxy maps to that allowlist, stored
   in a future `token_env` and sent as `Authorization: Bearer`/`Basic`.

| Backend | Read paths to allowlist | Admin/write paths to DENY | Read-only verification curl |
|---|---|---|---|
| **Grafana Tempo** | `GET /api/traces/{id}`, `GET /api/search`, `GET /api/search/tags` | flush/compaction/admin endpoints | `curl -s "$TEMPO/api/search?limit=1" -H "Authorization: Bearer $TOKEN"` |
| **VictoriaMetrics** | `GET /api/v1/query`, `GET /api/v1/query_range`, `GET /api/v1/labels`, `GET /api/v1/export` (read) | `/api/v1/import*`, `/api/v1/write`, `/api/v1/admin/tsdb/*`, `/internal/*` | `curl -s -G "$VM/api/v1/query" --data-urlencode 'query=up' -H "Authorization: Bearer $TOKEN"` |
| **Thanos** (Querier) | `GET /api/v1/query`, `GET /api/v1/query_range`, `GET /api/v1/labels`, `GET /api/v1/series` | Receive `/api/v1/receive`, Store admin, compactor, `/-/reload` | `curl -s -G "$THANOS/api/v1/query" --data-urlencode 'query=up' -H "Authorization: Bearer $TOKEN"` |
| **Jaeger** | `GET /api/traces`, `GET /api/traces/{id}`, `GET /api/services`, `GET /api/operations` | collector ingest endpoints; admin | `curl -s "$JAEGER/api/services" -H "Authorization: Bearer $TOKEN"` |
| **Zipkin** | `GET /api/v2/traces`, `GET /api/v2/trace/{id}`, `GET /api/v2/services`, `GET /api/v2/spans` | `POST /api/v2/spans` (ingest) | `curl -s "$ZIPKIN/api/v2/services" -H "Authorization: Bearer $TOKEN"` |
| **Parca** | gRPC/Connect `query`, `queryRange`, `labels`, `values` (read RPCs) | write/ingest, debug/admin RPCs | `curl -s "$PARCA/api/v1alpha1/query" -H "Authorization: Bearer $TOKEN"` (adjust to Connect-RPC) |
| **Pyroscope** | `GET /pyroscope/render`, `GET /render`, `GET /label-values`, query/select profiles (read) | `POST /ingest`, admin/flush | `curl -s "$PYROSCOPE/pyroscope/render?query=app&from=now-1h" -H "Authorization: Bearer $TOKEN"` |
| **kafka_exporter** | `GET /metrics` (Prometheus exposition, read-only by nature) | none (exporter only exposes metrics; lock down at network/proxy) | `curl -s "$KAFKA_EXPORTER/metrics" -H "Authorization: Bearer $TOKEN" \| head` |

**Datasource-fronted alternative:** Tempo, VictoriaMetrics, Thanos, Jaeger,
Zipkin, Pyroscope, and Parca are all commonly accessed through **Grafana
datasources**. If you front them with Grafana, reuse the **Grafana service account
+ Viewer role** pattern from the [Grafana Loki](#grafana-loki) section instead of a
bespoke proxy — one read-only `glsa_...` token then covers every Grafana-managed
datasource.

---

## Cross-cutting checklist (apply to every backend)

- [ ] Token/role is **read-only** — verified by the per-backend verification curl
      succeeding **and** an attempted write returning 403/401.
- [ ] Credential stored **only** in the environment variable named by the
      connector's `*_env` config key — never in config files, never committed.
- [ ] Token has an **expiry / rotation** schedule where the backend supports it
      (Datadog scoped keys, Splunk tokens, Sentry auth tokens, Grafana SA tokens,
      SigNoz keys all support expiry).
- [ ] Index/stream/project/datasource scope is **enumerated explicitly** — no
      `*`-everything grants (Splunk `srchIndexesAllowed`, ES role `names`, Graylog
      streams, Sentry `project`).
- [ ] Self-hosted/unauthenticated backends (Prometheus, Loki, SigNoz, and all
      "no connector yet" backends) sit behind a **reverse proxy with a read-path
      allowlist**; admin/ingest/delete paths return 403.
- [ ] `base_url`/`site` resolves to an **allowlisted external host** — gludd's
      SSRF guard already blocks loopback/RFC-1918/link-local/metadata literals.
