# Least-privilege: metrics stores

Covers `prometheus`, `victoriametrics`, `thanos`, and `opentsdb`. All have
`KIND = "metrics"` and issue **read-only query** calls only. All SSRF-guard
`base_url` (reject loopback/private/metadata literal hosts) and allow plain
`http`.

## Env-var table

| connector | `*_env` key(s) | auth scheme | endpoints |
|---|---|---|---|
| prometheus | `token_env` (optional) | `Authorization: Bearer <token>` | `GET {base_url}/api/v1/query`, `/api/v1/query_range` |
| victoriametrics | `username_env`, `password_env` (optional) | `Authorization: Basic <b64(user:pass)>` | `GET {base_url}/api/v1/query`, `/api/v1/query_range` |
| thanos | `token_env` (optional) | `Authorization: Bearer <token>` | `GET {base_url}/api/v1/query[_range]` (+`dedup`, `partial_response`) |
| opentsdb | `username_env`, `password_env` (optional) | `Authorization: Basic <b64(user:pass)>` | `POST {base_url}/api/query`, `GET /api/version` |

## prometheus

Runs instant (`/api/v1/query`) or range (`/api/v1/query_range`) PromQL. Auth is
an optional bearer token. Open-source Prometheus has no native RBAC; tokens are
enforced by a fronting proxy (e.g. Thanos/Cortex/Grafana gateway).

- **Least privilege:** none for an open Prometheus; otherwise a **query-only**
  token at the proxy. The connector never hits admin/TSDB-write APIs.

```bash
export PROMETHEUS_TOKEN='<optional-query-token>'
```
```yaml
- module: prometheus
  config: { base_url: "http://prometheus:9090", token_env: "PROMETHEUS_TOKEN" }
```
Verify: `health()` issues `GET /api/v1/query?query=1`.
```bash
curl -fsS -H "Authorization: Bearer $PROMETHEUS_TOKEN" \
  "http://prometheus:9090/api/v1/query?query=1"
```

## victoriametrics

Prometheus-compatible read API. Auth is **optional HTTP Basic** built from
`username_env`/`password_env` (only attached when both resolve).

- **Least privilege:** a read-only vmauth/vmselect user. If using vmauth, give
  the user a route limited to `/api/v1/query` and `/api/v1/query_range`.

```bash
export VM_USER='gludd_ro'
export VM_PASSWORD='CHANGE_ME'
```
```yaml
- module: victoriametrics
  config:
    base_url: "http://victoriametrics:8428"
    username_env: "VM_USER"
    password_env: "VM_PASSWORD"
```
Verify: `health()` issues `GET /api/v1/query?query=vector(1)`.
```bash
curl -fsS -u "$VM_USER:$VM_PASSWORD" \
  "http://victoriametrics:8428/api/v1/query?query=vector(1)"
```

## thanos

Thanos Querier, Prometheus read API plus the Thanos-specific `dedup` and
`partial_response` query params (config defaults overridable per-spec). Auth is
an optional bearer token.

- **Least privilege:** a query-only token at the Querier's fronting proxy.

```bash
export THANOS_TOKEN='<optional-query-token>'
```
```yaml
- module: thanos
  config:
    base_url: "http://thanos-query:10902"
    token_env: "THANOS_TOKEN"
    dedup: true
    partial_response: false
```
Verify: `health()` issues `GET /api/v1/query?query=vector(1)` (with the dedup/
partial_response params applied).

## opentsdb

`POST /api/query` with a JSON body (`{start, end, queries:[{metric, aggregator,
tags}]}`); `health()` probes `GET /api/version`. Auth is **optional HTTP Basic**
from `username_env`/`password_env`.

- **Least privilege:** OpenTSDB has no fine-grained RBAC; place it behind a proxy
  that permits only `/api/query` and `/api/version` (both read paths) for the
  monitoring user.

```bash
export OPENTSDB_USER='gludd_ro'
export OPENTSDB_PASSWORD='CHANGE_ME'
```
```yaml
- module: opentsdb
  config:
    base_url: "http://opentsdb:4242"
    username_env: "OPENTSDB_USER"
    password_env: "OPENTSDB_PASSWORD"
    aggregator: "sum"
```
Verify:
```bash
curl -fsS -u "$OPENTSDB_USER:$OPENTSDB_PASSWORD" \
  "http://opentsdb:4242/api/version"
```

## Read-only guarantee

Every connector here calls only query/version endpoints. `query()` returns
`[]` (victoriametrics, thanos, opentsdb) or a single normalized error record
(prometheus) on failure, and `health()` never raises.
