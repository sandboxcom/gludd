# Observability & Pipeline Connectors — Canonical Source Reference

gludd's job across heterogeneous backends is **correlation**: take an anchor
(commit, time window, service, trace, alert) and fan out to many sources, then
join the results on shared keys. This document is the canonical inventory of the
connector layer, grounded in the code under `src/general_ludd/connectors/`.

Every connector is a *self-contained* observability **source**: it imports
nothing from a shared base class or a sibling connector, validates its endpoint
against a literal-host SSRF guard, reads secrets only from `*_env` environment
variable **names**, injects its HTTP transport for testability, and emits a
**normalized record** so the `Observability` façade can merge/sort/correlate
across backends.

> Inventory note: every entry below was extracted by reading the connector
> module directly (class name, `KIND`, endpoints, config keys, `*_env` secret
> names, normalization). Entries are grounded in real code; nothing here is
> inferred from naming alone.

---

## 1. Connector inventory (grouped by KIND)

`KIND` is a class attribute. The four contract kinds are `pipeline` / `logs` /
`metrics` / `traces` (`base.VALID_KINDS`); several connectors declare additional
domain kinds (`infra`, `incidents`, `gcp_observability`) for their own grouping.

### Pipelines (CI/CD) — `KIND = "pipeline"`

| Connector (class) | Module | Facility | Endpoint(s) | Secret env keys |
|---|---|---|---|---|
| `GitHubActionsSource` | `github_actions.py` | GitHub Actions workflow runs (+ failed-job log drill-down) | `GET {base_url}/repos/{repo}/actions/runs`; `.../runs/{id}/jobs` (default base `https://api.github.com`) | `token_env` (default `GITHUB_TOKEN`) → Bearer |
| `JenkinsSource` | `jenkins.py` | Jenkins build history (per-job or all-jobs JSON API) | `GET {base_url}[/job/{job}]/api/json?tree=builds[...]` | `user_env` (`JENKINS_USER`), `token_env` (`JENKINS_TOKEN`) → HTTP Basic |
| `AwsPipelineSource` | `aws_pipeline.py` | AWS CodePipeline executions, **kind-bridges** to CloudWatch Logs via `fetch_logs()` (emits `kind="logs"`) | boto3 `codepipeline.list_pipeline_executions`; `logs.filter_log_events` | none in config — standard AWS credential chain (injectable `client_factory`) |

### Logs — `KIND = "logs"`

| Connector (class) | Module | Facility | Endpoint(s) | Secret env keys |
|---|---|---|---|---|
| `ElasticsearchSource` | `elasticsearch.py` | Elasticsearch `_search` (logs; emits `kind="traces"` per-hit when `trace.id`/`span.id` present — APM data) | `POST {base_url}/{index}/_search`; health `GET {base_url}/_cluster/health` | `api_key_env` → `ApiKey`, or `token_env` → `Bearer` |
| `SplunkSource` | `splunk.py` | Splunk oneshot SPL search over REST | `POST {base_url}/services/search/jobs`; health `GET .../services/server/info` | `token_env` (required) → Bearer |
| `GraylogSource` | `graylog.py` | Graylog universal search (relative/absolute) | `GET {base_url}/api/search/universal/{relative,absolute}`; health `GET .../api/system/lbstatus` | `token_env` (required) → HTTP Basic (`<token>:token`) |
| `GrafanaLokiSource` | `grafana_loki.py` | Loki `query_range` (LogQL); detects level from stream labels | `GET {base_url}/loki/api/v1/query_range`; health `GET {base_url}/ready` | `token_env` → Bearer |
| `AzureMonitorSource` | `azure_monitor.py` | Azure Monitor / Log Analytics KQL (also surfaces numeric `Value` column into `value`) | `POST {base_url}/v1/workspaces/{workspace_id}/query` (default base `https://api.loganalytics.io`) | `token_env` (required) → Bearer |
| `GcpObservabilitySource`† | `gcp_observability.py` | GCP Cloud Logging (`mode="logs"`) — see Multi-mode note | `POST https://logging.googleapis.com/v2/entries:list` | `token_env` (default `GCP_TOKEN`) or injected `token` → Bearer |
| `DatadogSource`‡ | `datadog.py` | Datadog Logs Search v2 (`mode="logs"`) — see Multi-mode note | `POST {site}/api/v2/logs/events/search`; health `GET {site}/api/v1/validate` | `api_key_env` → `DD-API-KEY`, `app_key_env` → `DD-APPLICATION-KEY` |
| `SentrySource` | `sentry.py` | Sentry issues (and per-issue latest event via `fetch_event()`, surfacing `trace_id`/`commit`) | `GET {base_url}/api/0/projects/{org}/{project}/issues/`; event `.../issues/{id}/events/latest/` (default base `https://sentry.io`) | `token_env` (required) → Bearer |
| `KubernetesSource` | `kubernetes.py` | Kubernetes pod logs (`mode="logs"`) and cluster Events (`mode="events"`) via the REST API — no kubectl/shell | `GET {api_server}/api/v1/namespaces/{ns}/pods/{pod}/log`; `.../events`; health `/livez`→`/version` | `token_env` (default `K8S_TOKEN`) → Bearer (ServiceAccount) |
| `JsonlLogSource` | `local_files.py` | Local line-delimited JSON logs, path-confined to an allowed `root` | local filesystem (`os.path.realpath` confinement) | none (local files) |
| `SyslogGrepSource` | `local_files.py` | Local RFC3164-ish syslog text, scanned by a Python regex (never `grep`), path-confined | local filesystem | none (local files) |
| `JournaldSource` | `journald.py` | systemd journal via `journalctl -o json --no-pager` through an injected argv runner (validated filters, `shell=False`, fail-closed) | local `journalctl` subprocess (argv list) | none (local journal) |

### Metrics — `KIND = "metrics"`

| Connector (class) | Module | Facility | Endpoint(s) | Secret env keys |
|---|---|---|---|---|
| `PrometheusSource` | `prometheus.py` | PromQL instant + range queries (vector/matrix/scalar result types) | `GET {base_url}/api/v1/query` and `/api/v1/query_range`; health uses `query=1` | `token_env` (optional) → Bearer |

(Datadog and GCP also serve metrics via `mode="metrics"` — see Multi-mode note.)

### Traces — `KIND = "traces"`

| Connector (class) | Module | Facility | Endpoint(s) | Secret env keys |
|---|---|---|---|---|
| `JaegerSource` | `jaeger.py` | Jaeger traces; one record per span (service resolved via `processes` map) | `GET {base_url}/api/traces?service=&lookback=&limit=`; health `GET /api/services` | `token_env` (optional) → Bearer; `allow_private` opt-in |
| `TempoSource` | `tempo.py` | Grafana Tempo search (tags or TraceQL); one record per trace summary, or per span with `fetch_spans` (`/api/traces/{id}`, OTLP batches) | `GET {base_url}/api/search`; `GET {base_url}/api/traces/{id}` | `token_env` (optional) → Bearer; `allow_private` opt-in |
| `ZipkinSource` | `zipkin.py` | Zipkin v2 spans; one record per span (duration in µs) | `GET {base_url}/api/v2/traces?serviceName=&lookback=&limit=` | `token_env` (optional) → Bearer; `allow_private` opt-in |
| `SigNozSource` | `signoz.py` | SigNoz `query_range` traces/spans (`SECONDARY_KINDS = ("metrics",)`) | `POST {base_url}/api/v3/query_range`; health `GET /api/v1/version` | `token_env` → `SIGNOZ-API-KEY` + Bearer |

### Infrastructure inventory — `KIND = "infra"`

| Connector (class) | Module | Facility | Endpoint(s) | Secret env keys |
|---|---|---|---|---|
| `AzureResourceGraphSource` | `azure_resource_graph.py` | Azure Resource Graph KQL — resource inventory across subscriptions (`provisioningState` → `level_or_status`) | `POST {base_url}/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01` (default base `https://management.azure.com`) | `token_env` (required) → Bearer |

### Incidents / alerting — `KIND = "incidents"`

| Connector (class) | Module | Facility | Endpoint(s) | Secret env keys |
|---|---|---|---|---|
| `PagerDutySource` | `pagerduty.py` | PagerDuty incidents (filter by status window) | `GET {base_url}/incidents?since=&until=&statuses[]=` (default base `https://api.pagerduty.com`) | `token_env` (default `PAGERDUTY_TOKEN`) → `Authorization: Token token=<v>` |
| `OpsgenieSource` | `opsgenie.py` | Opsgenie alerts (Opsgenie query language) | `GET {base_url}/v2/alerts?query=&limit=` (default base `https://api.opsgenie.com`) | `token_env` (default `OPSGENIE_API_KEY`) → `Authorization: GenieKey <v>` |
| `GrafanaOnCallSource` | `grafana_oncall.py` | Grafana OnCall alert groups (`base_url` required, always SSRF-guarded) | `GET {base_url}/api/v1/alert_groups?perpage=&state=` | `token_env` (default `GRAFANA_ONCALL_TOKEN`) → raw token header |

### Multi-mode cloud suites — `KIND` per module

| Connector (class) | Module | KIND | Modes |
|---|---|---|---|
| `GcpObservabilitySource`† | `gcp_observability.py` | `gcp_observability` | `mode="logs"` → Cloud Logging `entries:list`; `mode="metrics"` → Cloud Monitoring `GET https://monitoring.googleapis.com/v3/projects/{project}/timeSeries` |
| `DatadogSource`‡ | `datadog.py` | `logs` | `mode="logs"` → Logs Search v2 (POST); `mode="metrics"` → `GET {site}/api/v1/query` (Query Timeseries v1) |

† `GcpObservabilitySource` declares `KIND = "gcp_observability"`; individual
records are tagged `kind="logs"` or `kind="metrics"` by the active mode.
‡ `DatadogSource` declares `KIND = "logs"`; metric records are emitted as
`kind="metrics"` when `mode="metrics"`.

**Cross-kind bridging:** several sources emit a different per-record `kind` than
their class `KIND` — `AwsPipelineSource.fetch_logs()` emits `logs`,
`ElasticsearchSource` emits `traces` for APM hits, GCP/Datadog switch on mode,
Kubernetes serves both pod logs and Events. The façade keys off the per-record
`kind`, so these mixed sources participate correctly in kind-restricted fan-out.

---

## 2. The connector contract (`connectors/base.py`)

### 2.1 `Source` Protocol + marker subtypes
`Source` is a `@runtime_checkable` structural Protocol — connectors satisfy it by
*duck typing*, not inheritance, which keeps the façade decoupled from any
concrete client. Required surface:

- `name: str` — the registered, human-readable source name.
- `KIND: str` — one of `base.VALID_KINDS` (or a domain kind like `infra`).
- `health() -> dict[str, Any]` — a status dict. **MUST NOT raise**: every
  connector reports failure inside the dict (`{"ok": False, ...}` /
  `{"healthy": False, ...}`).
- `query(spec) -> list[dict]` — a list of **normalized records** matching `spec`.

Four marker Protocols extend `Source` for static intent: `PipelineSource`,
`LogSource`, `MetricSource`, `TraceSource`. Kind constants:
`PIPELINE_KIND="pipeline"`, `LOG_KIND="logs"`, `METRIC_KIND="metrics"`,
`TRACE_KIND="traces"`; `VALID_KINDS` is the frozenset of those four.

### 2.2 Normalized record (`NormalizedRecord` TypedDict + `normalized_record()`)
Every backend row is funnelled through one shape so the façade can merge across
heterogeneous sources. The eight keys:

| Key | Meaning |
|---|---|
| `ts` | epoch seconds (`float`) or `None`; `None`-ts records sort **after** timed ones |
| `source` | the registered name of the producing source |
| `kind` | the per-record kind (`pipeline`/`logs`/`metrics`/`traces`/…) |
| `level_or_status` | log level or pipeline/job status; the façade tags its own failures `"error"` |
| `message` | human-readable line |
| `value` | numeric payload for metric records, else `None` |
| `labels` | free-form `str→Any` tags — correlation keys (`trace_id`, `commit`, …) live here |
| `raw` | the untouched backend payload, for drill-down |

`normalized_record(...)` builds one with well-formed defaults (empty `labels`
dict, `level_or_status="info"`).

### 2.3 Config-driven + secret-by-env-name
Connectors are constructed from a `config` dict. Credentials are **never** inline:
config carries `*_env` keys naming the environment variable that holds the
secret; the value is read from `os.environ` at call time and sent in the auth
header (Bearer / ApiKey / Basic / vendor header), never logged, never stored on
the instance where avoidable.

### 2.4 SSRF guard — `is_safe_endpoint(url)` (and per-connector variants)
`base.is_safe_endpoint()` is a **literal-host** guard: it accepts only
`http`/`https`, and rejects loopback, RFC-1918 private, link-local (incl. the
`169.254.169.254` metadata IP), reserved, multicast, unspecified, IPv6
unique-local, and named-metadata hosts (`metadata.google.internal`, …). It
**never resolves DNS** — a hostname that is not a literal IP and not a known-bad
name passes (network egress policy is the connector layer's concern, not this
guard's). Each connector ships its own equivalent literal-host check validated at
construction time (so a bad `base_url`/`site`/`api_server` fails before any I/O).
Notable variations:

- **`allow_private` opt-in** — `JaegerSource`, `TempoSource`, `ZipkinSource`,
  `KubernetesSource`, and the incident connectors accept private/RFC-1918 hosts
  only when `allow_private=True`; loopback / link-local / metadata stay blocked
  even then.
- **`PrometheusSource` / `DatadogSource`** additionally reject `not ip.is_global`.
- **PagerDuty/Opsgenie/GrafanaOnCall** best-effort `socket.getaddrinfo` resolve
  on override hosts and also block internal name suffixes
  (`.local`/`.internal`/`.lan`/`.corp`/…).
- **`local_files`** uses path confinement (`os.path.realpath` must stay inside
  the allowed `root`) instead of a URL guard; **`journald`** validates argv
  filters (rejects leading-dash and shell metacharacters, fail-closed).

### 2.5 Registry + façade
- `SourceRegistry` — a runtime `name → Source` map (`register` is
  last-write-wins; `get`, `by_kind`, `all`).
- `Observability(registry)` — pure orchestration:
  - `find(spec, kinds=None)` fans `spec` across matching sources, merges, and
    sorts by `ts`. **Resilient:** a source whose `query()` raises is captured as
    an `"error"`-level record attributed to that source; the fan-out continues.
  - `associate(records, by="trace_id", window_s=60)` correlates: group by a label
    value (`trace_id`/`commit`/any label) or, with `by="time_window"`, greedily
    cluster records within `window_s` of the cluster's first record.

---

## 3. Cross-source correlation layer (`connectors/normalize.py`)

Connector `labels` are heterogeneous (CloudWatch says `instance`, Loki says
`host`, k8s says `node`). This module folds them into a **canonical join
vocabulary** and classifies connectors into **auth families**. It is pure stdlib,
imports no connector module, is **idempotent** and **total** (never raises).

### 3.1 Canonical join keys — `normalize_join_keys(record)`
Adds a `join` sub-dict computed from the record's `labels` (a missing key is
omitted, never `None`):

| Join key | Source label aliases (case-insensitive) |
|---|---|
| `trace_id` | `trace_id`, `traceid`, `x-b3-traceid`, `x_b3_traceid` |
| `host` | `host`, `hostname`, `instance`, `computer`, `node` (lower-cased, `:port` stripped, IPv6-aware) |
| `service` | `service`, `app`, `application`, `job`, `unit`, `container_name` |
| `k8s` | `{namespace, pod, container}` from their alias sets (only present keys) |
| `cloud` | `{account, project, subscription, region}` from their alias sets |
| `severity` | folded from `level`/`level_or_status`/`status`/`severity`/`priority` |

`correlate(records, by)` groups records by one canonical join key (normalizing on
the fly); scalar keys group by string value, `k8s`/`cloud` group by a stable
`"a=1,b=2"` rendering. Records lacking `by` are dropped.

### 3.2 Severity canonicalization
`CANONICAL_SEVERITIES = (debug, info, warn, error, critical)`. `_SEVERITY_MAP`
folds heterogeneous tokens — backend spellings (`succeeded`, `unhealthy`,
`fatal`, `notice`, …) and **syslog numeric priorities 0–7** — into those five.

### 3.3 Auth families — `auth_family(name)` / `bundle_credentials(configs)`
`AUTH_FAMILY_PREFIXES` maps a connector/source name to a family by
prefix-or-substring token match (case-insensitive, declaration order):

| Family | Recognizing tokens |
|---|---|
| `aws` | `aws`, `cloudwatch`, `x-ray`/`xray`/`x_ray`, `dynamodb`, `eventbridge` |
| `azure` | `azure`, `entra`, `graph`, `appinsights`/`app_insights`, `loganalytics`/`log_analytics` |
| `gcp` | `gcp`, `google`, `stackdriver`, `bigquery`, `pubsub`, `gke` |
| `grafana` | `grafana`, `loki`, `tempo`, `mimir`, `oncall`, `prometheus` |
| `datadog` | `datadog`, `ddog`, `dd_` |
| `elastic` | `elastic`, `elasticsearch`, `kibana`, `logstash`, `opensearch` |
| `github` | `github`, `gh_`, `actions` |
| `gitlab` | `gitlab`, `glab` |
| `splunk` | `splunk`, `hec` |
| `newrelic` | `newrelic`, `new_relic`, `nr_` |
| `pagerduty` | `pagerduty`, `pd_` |

`bundle_credentials(configs)` returns `{family: [ENV_VAR_NAME, …]}` — collecting
only the **names** of `*_env` config entries (de-duplicated, first-appearance
order), so one resolved family credential can fan out to every connector in that
family. **Secret-safe invariant:** it reads env-var *names* only and never
dereferences a secret value. A config's family comes from an explicit
`family`/`auth_family` key, else is inferred from its `source`/`name`/`kind`/
`connector` field.

---

## 4. Push-side ingest parsers (`connectors/ingest_formats.py`)

The receiver endpoint accepts bytes pushed by log shippers and must normalize
them **without trusting the sender**. Each parser is pure (no I/O, no global
state), self-contained (emits plain dicts), **fail-soft** (malformed input →
`[]`, never raises), and **payload-bounded** (`MAX_PAYLOAD_BYTES = 8 MiB`,
`MAX_EVENTS = 100_000`). Records carry the canonical key set with `kind="log"`.

| Parser | Wire format | Notes |
|---|---|---|
| `parse_fluent_forward(payload)` | Fluent Forward, JSON `[tag, [[time, record], …]]` | msgpack/binary mode decoded **only** if `msgpack` is importable (guarded), else fails soft; non-`message` fields become labels (plus `tag`) |
| `parse_beats_lumberjack(frames)` | Elastic Beats / Lumberjack v2 window (caller supplies decoded JSON events) | surfaces `host` and `beat`/`agent.type` into labels; reads `@timestamp`/`log.level`/`event.original` |
| `parse_gelf(payload)` | Graylog GELF (JSON, and chunked GELF magic `0x1e 0x0f`) | chunked input reassembled only when **all** chunks are present in one payload (else fail soft); `level` mapped via syslog 0–7 names; `_field` keys de-underscored into labels |

---

## 5. How to add a connector

1. **Create a standalone module** in `src/general_ludd/connectors/`. Import
   nothing from a sibling connector or a base class — connectors are
   independently vendorable/testable.
2. **Implement the duck-typed `Source` surface:** a `name: str` instance
   attribute, a `KIND: str` class attribute (use a `base.VALID_KINDS` value
   unless you genuinely need a new domain kind), `health()` that **never raises**,
   and `query(spec)` that returns normalized-record dicts.
3. **Construct from a `config` dict.** Read secrets via a `*_env` key
   (`os.environ[config["token_env"]]`) at call time — never inline a secret.
4. **Guard the endpoint.** Validate `base_url`/`site`/`api_server` at
   construction with a literal-host SSRF check (reuse `base.is_safe_endpoint`
   semantics: http(s) only, reject loopback/private/link-local/metadata, no DNS).
   Offer `allow_private` only if internal targets are a legitimate use case.
5. **Inject the HTTP transport** (a callable / small Protocol) so tests run with
   canned responses and zero network. Time-bound every request; never `shell=True`.
6. **Emit normalized records** — funnel each backend row through the eight-key
   shape (`normalized_record()` or an inline dict). Put correlation keys
   (`trace_id`, `commit`, `service`, host/k8s/cloud coordinates) into `labels`
   using names that match `normalize.py`'s alias sets, so the correlation layer
   joins your records with everyone else's for free.
7. **Mind auth-family bundling.** If your connector belongs to an existing auth
   family, name it so `auth_family()` classifies it (or add a token to
   `AUTH_FAMILY_PREFIXES`), and declare its credential under a `*_env` key so
   `bundle_credentials()` collects it.
8. **Register** the instance into a `SourceRegistry`; the `Observability` façade
   will fan queries to it by kind automatically.
