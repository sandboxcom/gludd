# Least-privilege: tracing & profiling

Covers `jaeger`, `zipkin`, `tempo` (distributed tracing) and `parca`,
`pyroscope` (continuous profiling). All five are read-only HTTP connectors with
`KIND = "traces"`. Each reads an **optional** bearer token from the env var
*named* by `config["token_env"]`; if `token_env` is unset, requests go out
unauthenticated (valid for open internal backends). All five SSRF-guard
`base_url` and reject private/loopback hosts unless `allow_private=True`.

## Env-var table

| connector | `*_env` key | auth header sent | endpoints read |
|---|---|---|---|
| jaeger | `token_env` (optional) | `Authorization: Bearer <token>` | `GET {base_url}/api/traces`, `GET /api/services` |
| zipkin | `token_env` (optional) | `Authorization: Bearer <token>` | `GET {base_url}/api/v2/traces` |
| tempo | `token_env` (optional) | `Authorization: Bearer <token>` | `GET {base_url}/api/search`, `GET /api/traces/{id}` |
| parca | `token_env` (optional) | `Authorization: Bearer <token>` | `POST {base_url}/parca.query.v1alpha1.QueryService/QueryRange` |
| pyroscope | `token_env` (optional) | `Authorization: Bearer <token>` | `GET {base_url}/render?format=json` |

The token is read at call time (`os.environ.get(token_env)`); the header is only
attached when the var resolves to a non-empty value.

## Least-privilege principle

These connectors only ever issue read/query calls — never write, delete, or
admin operations. Provision a token (where the backend supports scoped tokens)
that can **read traces/profiles only**. None of these backends require write
scope for this connector to function.

### Jaeger / Zipkin / Tempo

Jaeger and Zipkin OSS have no built-in RBAC; tokens (if any) are enforced by a
fronting proxy (e.g. an OAuth2 proxy or Grafana). For Tempo behind Grafana
Cloud, mint a token scoped to **traces read** only.

Copy-pasteable env setup (token optional):

```bash
# Only if the backend is behind an auth proxy:
export JAEGER_TOKEN='<read-only-trace-token>'
export ZIPKIN_TOKEN='<read-only-trace-token>'
export TEMPO_TOKEN='<grafana-cloud-traces-read-token>'
```

Config wiring (env var *name*, not the secret):

```yaml
- module: jaeger
  config: { base_url: "https://jaeger.example.com", token_env: "JAEGER_TOKEN" }
- module: zipkin
  config: { base_url: "https://zipkin.example.com", token_env: "ZIPKIN_TOKEN" }
- module: tempo
  config: { base_url: "https://tempo.example.com", token_env: "TEMPO_TOKEN" }
```

### Parca / Pyroscope

Both are query-only against the profiling store. Parca's Connect `QueryRange`
and Pyroscope's `/render` are read paths. If the instance is behind an auth
proxy, mint a **read-only** token.

```bash
export PARCA_TOKEN='<read-only-token>'      # optional
export PYROSCOPE_TOKEN='<read-only-token>'  # optional
```

```yaml
- module: parca
  config: { base_url: "https://parca.example.com", token_env: "PARCA_TOKEN" }
- module: pyroscope
  config:
    base_url: "https://pyroscope.example.com"
    query: 'process_cpu:samples:count:cpu:nanoseconds{}'
    token_env: "PYROSCOPE_TOKEN"
```

## Read-only verification per facility

Each connector exposes `health()`, which performs a single bounded read and
**never raises** (returns `{"ok": bool, "detail": str}`):

| connector | `health()` probe |
|---|---|
| jaeger | `GET /api/services` |
| zipkin | `GET /api/v2/traces?limit=1` |
| tempo | `GET /api/search?limit=1` |
| parca | bounded `QueryRange` |
| pyroscope | bounded `/render?format=json` |

Manual read-only checks (no token shown; substitute your env var):

```bash
# Jaeger — list services (read-only)
curl -fsS -H "Authorization: Bearer $JAEGER_TOKEN" \
  "https://jaeger.example.com/api/services"

# Zipkin — one trace summary
curl -fsS -H "Authorization: Bearer $ZIPKIN_TOKEN" \
  "https://zipkin.example.com/api/v2/traces?limit=1"

# Tempo — search probe
curl -fsS -H "Authorization: Bearer $TEMPO_TOKEN" \
  "https://tempo.example.com/api/search?limit=1"

# Pyroscope — render probe (read)
curl -fsS -H "Authorization: Bearer $PYROSCOPE_TOKEN" \
  "https://pyroscope.example.com/render?query=process_cpu:samples:count:cpu:nanoseconds{}&format=json"

# Parca — QueryRange probe (read)
curl -fsS -X POST -H "Authorization: Bearer $PARCA_TOKEN" \
  -H "Content-Type: application/json" -d '{"query":"","limit":1}' \
  "https://parca.example.com/parca.query.v1alpha1.QueryService/QueryRange"
```

A `200`/JSON response confirms the token can read; a `403` means the token is
over- or under-scoped. None of these calls mutate state.
