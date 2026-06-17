# Least-privilege: incidents & identity-provider audit logs

Covers `pagerduty`, `opsgenie`, `grafana_oncall` (incident sources,
`KIND = "incidents"`) and `okta`, `cloudflare`, `entra_signin` (IdP/audit
sources, `KIND = "events"`). All six are read-only HTTP connectors that read a
secret **only** from the env var named by `config["token_env"]`, never inline
the secret, and never write it to any record/label/log/error. All six SSRF-guard
their endpoint host.

## Env-var table

| connector | `*_env` key | default name | auth header | endpoint |
|---|---|---|---|---|
| pagerduty | `token_env` | `PAGERDUTY_TOKEN` | `Authorization: Token token=<token>` | `GET https://api.pagerduty.com/incidents` |
| opsgenie | `token_env` | `OPSGENIE_API_KEY` | `Authorization: GenieKey <key>` | `GET https://api.opsgenie.com/v2/alerts` |
| grafana_oncall | `token_env` | `GRAFANA_ONCALL_TOKEN` | `Authorization: <raw token>` | `GET {base_url}/api/v1/alert_groups` |
| okta | `token_env` | *(required, no default)* | `Authorization: SSWS <token>` | `GET {org_url}/api/v1/logs` |
| cloudflare | `token_env` | *(required, no default)* | `Authorization: Bearer <token>` | `GET https://api.cloudflare.com/client/v4/accounts/{id}/audit_logs` |
| entra_signin | `token_env` | *(required, no default)* | `Authorization: Bearer <token>` | `GET https://graph.microsoft.com/v1.0/auditLogs/signIns` |

`pagerduty`/`opsgenie` only SSRF-guard `base_url` when it is overridden from the
public default. `grafana_oncall` **requires** `base_url` and always guards it.
`okta` requires `org_url`; `cloudflare` requires `account_id` (or a full
`base_url`); all reject private/loopback hosts unless `allow_private=True`.

## pagerduty

Reads `GET /incidents` (filtered by `statuses[]`, `since`, `until`, `limit`).

- **Least privilege:** a PagerDuty **read-only API token** (General Access /
  read-only REST API key). No write/manage scope is exercised.
```bash
export PAGERDUTY_TOKEN='<read-only-rest-api-token>'
```
```yaml
- module: pagerduty
  config:
    token_env: "PAGERDUTY_TOKEN"
    statuses: ["triggered", "acknowledged"]
```
Verify (read-only): `health()` GETs `/incidents?limit=1`.
```bash
curl -fsS -H "Authorization: Token token=$PAGERDUTY_TOKEN" \
  -H "Accept: application/json" "https://api.pagerduty.com/incidents?limit=1"
```

## opsgenie

Reads `GET /v2/alerts` (filtered by `query`, `limit`).

- **Least privilege:** an Opsgenie **API integration / key with Read access
  only** to alerts.
```bash
export OPSGENIE_API_KEY='<read-only-api-key>'
```
```yaml
- module: opsgenie
  config: { token_env: "OPSGENIE_API_KEY", query: "status: open" }
```
Verify: `health()` GETs `/v2/alerts?limit=1` with `Authorization: GenieKey …`.

## grafana_oncall

Reads `GET {base_url}/api/v1/alert_groups`. `base_url` is required and always
SSRF-guarded (self-hosted on-prem needs `allow_private=True`). The token is sent
as a **raw** `Authorization` header (no scheme prefix).

- **Least privilege:** a Grafana OnCall API token scoped to the OnCall plugin;
  the connector only reads alert groups.
```bash
export GRAFANA_ONCALL_TOKEN='<oncall-api-token>'
```
```yaml
- module: grafana_oncall
  config:
    base_url: "https://oncall.example.com"
    token_env: "GRAFANA_ONCALL_TOKEN"
    # allow_private: true   # only for on-prem private hosts
```
Verify: `health()` GETs `/api/v1/alert_groups?perpage=1`.

## okta

Reads `GET {org_url}/api/v1/logs` (System Log), paginating via the `Link: …;
rel="next"` header up to `max_pages`. `token_env` is **required** (no default).

- **Least privilege:** an Okta API token created by a user holding only the
  **read-only admin** role needed for System Log read (e.g. *Read-Only
  Administrator*). The connector only reads `/api/v1/logs`.
```bash
export OKTA_TOKEN='<okta-api-token-from-read-only-admin>'
```
```yaml
- module: okta
  config:
    org_url: "https://example.okta.com"
    token_env: "OKTA_TOKEN"
    max_pages: 10
```
Verify: `health()` GETs `/api/v1/logs?limit=1` with `Authorization: SSWS …`.

## cloudflare

Reads `GET .../accounts/{account_id}/audit_logs` (paginated via
`result_info.total_pages`). `token_env` is **required**; `account_id` is required
unless a full `base_url` is supplied.

- **Least privilege:** a Cloudflare API **token** (not the Global API Key) scoped
  to **Account → Audit Logs → Read** for the target account only.
```bash
export CLOUDFLARE_TOKEN='<scoped-audit-logs-read-token>'
```
```yaml
- module: cloudflare
  config:
    account_id: "<account-id>"
    token_env: "CLOUDFLARE_TOKEN"
    per_page: 100
```
Verify: `health()` GETs the audit-log endpoint with `per_page=1&page=1` and
`Authorization: Bearer …`.

## entra_signin

Reads `GET https://graph.microsoft.com/v1.0/auditLogs/signIns`, paginating via
`@odata.nextLink`. The connector does **not** implement OAuth — the Graph bearer
token is minted **externally** (client-credentials flow) and supplied via the env
var named by `token_env` (required).

- **Least privilege:** a Microsoft Entra app registration granted the
  **`AuditLog.Read.All`** application permission (admin-consented), nothing more.
  The externally-run token service mints a short-lived Graph access token into
  the env var.
```bash
export ENTRA_GRAPH_TOKEN='<externally-minted-graph-access-token>'
```
```yaml
- module: entra_signin
  config:
    token_env: "ENTRA_GRAPH_TOKEN"
    max_pages: 10
```
Verify: `health()` GETs `signIns?$top=1` with `Authorization: Bearer …`.

## Read-only & secret-safety guarantees

- Every connector issues only `GET` reads; `health()` never raises and
  `pagerduty`/`opsgenie`/`grafana_oncall` scrub error detail to the exception
  **type name** so a request URL/credential can never leak.
- The secret is read at call time from `os.environ[token_env]`; a missing var
  yields a clean `health()` failure (`"environment variable … is not set"`),
  never a stack trace exposing config.
