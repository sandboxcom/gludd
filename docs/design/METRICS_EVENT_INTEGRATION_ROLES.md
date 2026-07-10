# Metrics & Event System Integration Roles (2026-07-10)

Status: **design-complete, not yet implemented.** Style/format mirrors
`docs/design/PIPELINE_INTERACTION_ROLES.md` and
`docs/design/TIME_TIMERS_SCOPED_NOTIFICATIONS.md`. Line numbers are
current-tree at authoring time — re-confirm with a Read before implementing,
they drift.

**Scope note.** This is about a gludd-deployed agent talking to an
**operator's/target-system's** metrics and event infrastructure (Prometheus,
StatsD, OTLP, Graphite, InfluxDB, CloudWatch, Datadog, Kafka/NATS/Redis
Streams/EventBridge/generic webhooks) — discover it, query it, emit to it,
subscribe to it. It is **not** `docs/design/CI_PIPELINE_MEDIC_ROLE.md` (gludd
healing its own GHA pipeline) and it is not
`docs/design/PIPELINE_INTERACTION_ROLES.md` (driving an external CI/CD
system's trigger/cancel/approve verbs) — those two designs' ten `KIND
= "pipeline"` connectors are out of scope here; this design's connectors all
carry `KIND ∈ {"metrics", "logs", "traces", "events"}`. The three designs
share the same connector spine (`connectors/base.py`, `connectors/registry.py`)
and the same `gludd_dispatch.py`/`daemon_url`+`psk`+`timeout` Ansible-module
convention — reuse, don't refork, that spine.

---

## 1. SURVEY — what exists today

### 1.1 gludd's OWN self-instrumentation (distinct from what this design extends)

`src/general_ludd/observability/` is gludd instrumenting **itself**, not a
generic backend-integration layer:

- `metrics_exporter.py:38-170 MetricsExporter` — an in-process
  `prometheus_client` wrapper (`counter_inc`/`gauge_set`/`histogram_observe`,
  `:81-112`) with a cardinality guard (`_bound_labels`, `:54-79`, caps distinct
  label values per `(metric, label-key)` at `MAX_LABEL_VALUES_PER_KEY = 50`,
  folding overflow into `"__other__"`). `render_prometheus()` (`:114-116`) is
  wired at `daemon.py:2626-2627` (`GET /metrics` returns
  `PlainTextResponse(get_metrics_exporter().render_prometheus())`); `get_json`/
  `get_counters`/`get_gauges` back `daemon.py:2631-2632`'s JSON facet. This is
  **PULL-only, and it exposes gludd's OWN process metrics** — the daemon is
  the Prometheus *target*, never a client of someone else's Prometheus.
- `tracer.py:15-152 ExecutionSpan`/`ExecutionTrace` — gludd's own lightweight
  span/trace model for task-lifecycle observability (tokens, cost, phase,
  `project_id`-scoped per the XT trace-leak fix at `:88-91`). Pure in-process
  dataclasses, no wire format.
- `otel_bridge.py:26-121 OTelBridge` — exports `ExecutionTrace` spans
  **out** via `OTLPSpanExporter` (gRPC, `:46-68`), guarded by
  `_check_otel_available()` (`:11-23`, `importlib.util.find_spec` probe so the
  optional `opentelemetry.*` deps need not be installed to import this
  module). **Traces only** — no metrics, no logs OTLP export exists anywhere
  in the tree.
- `timing.py:66-337 DurationTracker`/`StallWatchdog` — pure-stdlib
  anomaly/stall detection with pluggable `on_anomaly`/`on_stall` callbacks
  (`:141-165`, `:196-318`); it decides *when* something is slow/hung and hands
  the verdict to whatever the caller wires (logging today) — a natural
  `metrics_emit` (§2.2) consumer, not itself a backend integration.
- `routers/facts.py:83-120 _metrics_facet` (backing `GET /api/metrics`,
  `:449-459`, and the `gludd_metrics` Ansible module,
  `collections/.../plugins/modules/gludd_metrics.py:91-129`) surfaces
  gludd's OWN agent/model/cost metrics (`MetricsCollector.get_full_report`,
  `BenchmarkRepository`) as Ansible facts. This is "how does a playbook read
  gludd's internal state," not "how does an agent read a target system's
  Prometheus" — a different, already-solved problem this design does not
  redo.

### 1.2 The connector layer — the real spine to extend (pull/query side)

`src/general_ludd/connectors/` ships **~85 self-contained modules**
(`make collection-modules`-adjacent count via `pkgutil.iter_modules`, see
`registry.py:340-343`). The contract (`base.py`):

- `base.py:145-163 Source` Protocol (`name`, `KIND`, `health()`, `query(spec)`)
  + four marker kinds `base.py:53-59` (`pipeline`/`logs`/`metrics`/`traces`).
- `base.py:65-139 NormalizedRecord`/`normalized_record()` — the shared
  8-key record shape every connector emits (`ts/source/kind/level_or_status/
  message/value/labels/raw`), with NaN/Inf sanitization (`:122-129`, the D-31
  fix).
- `base.py:188-213 SourceRegistry`, `base.py:218-398 Observability` — fan-out
  `find()` with per-source/global caps (D-30, `:39-42`) and byte budget, plus
  `associate()` correlation by label or time-window.
- `base.py:409-435 is_safe_endpoint()` — literal-host SSRF guard (no DNS),
  delegating to `security/ssrf.is_url_blocked`. Every metrics/logs/traces
  connector calls this (or the equivalent `is_url_blocked`/`host_is_blocked`)
  itself at construction.
- `base.py:528-578 run_healthcheck` / `classify_health_for_source` —
  `health()` is timeout-bound in a thread, never raises, and an unconfigured
  source (no `*_env` var set) is classified `"degraded"` rather than
  `"unhealthy"` (`_is_configured`, `:452-477`).
- `registry.py:94-476 ConnectorRegistry.from_config` — builds a
  `name -> live instance` map from an operator config list; `factory`/
  `class`/`module` selector (`_resolve_factory`, `:178-211`), hard-allowlisted
  against `_ALLOWED_CONNECTOR_MODULES` (`:340-343`, built once from
  `pkgutil.iter_modules`) BEFORE any import (`_check_module_allowlist`,
  `:400-443`) — this is what stops an operator config value like
  `"module": "os"` from being an RCE. `query(name, spec)` is deliberately
  URL-free (`:274-305`).
- `routers/observe.py:91-297` — `GET /api/observe/sources` (metadata only,
  never secrets), `GET /api/observe/health`, `POST /api/observe/query`
  (body `{source, spec}` — no `url` field, so request-time SSRF is
  structurally impossible), and `wire_observability(app, daemon_state,
  config)` (`:249-297`) — the single daemon hookup, called at
  `daemon.py:2939-2943` from `UserConfig.connectors`
  (`config/user_config.py:174`). PSK-gated, not in `_PUBLIC_PATHS`.

**Already-shipped backend families relevant to this design** (all
`KIND ∈ {metrics, logs, traces}`, all self-contained, all with injectable
transport + literal-host SSRF guard + `*_env` credential resolution):

| Family | Connectors | Verb shape |
|---|---|---|
| Prometheus / OpenMetrics | `prometheus.py` (PromQL instant+range via `/api/v1/query[_range]`, `:245-359`), `prom_scrape.py` (generic `/metrics` exposition scraper, any exporter) | query (pull) |
| Pushgateway | **none** — no write-side Prometheus connector exists | gap (§2.4) |
| StatsD/DogStatsD | `statsd_parse.py` — a **pure line-protocol parser**, `query(spec)` expects `spec['lines']` already collected; no UDP socket listener anywhere | parse-only, no receiver (gap) |
| Graphite / InfluxDB / VictoriaMetrics / OpenTSDB / Thanos | `graphite.py`, `influxdb.py`, `victoriametrics.py`, `opentsdb.py`, `thanos.py` | query (pull) |
| Kafka | `kafka_exporter.py` — scrapes **kafka_exporter's Prometheus endpoint** (consumer-lag gauges, `_WANTED_METRICS` allowlist `:55-65`), does **NOT** consume a Kafka topic's messages | query (pull, metrics-about-Kafka, not Kafka itself) |
| NATS | `nats.py` — scrapes the NATS **HTTP monitoring** endpoints (`/varz`/`/connz`/`/subsz`, `:283-318`), does **NOT** subscribe to NATS subjects/JetStream | query (pull, monitoring only) |
| Redis | `redis_stats.py` — runs `INFO`/`SLOWLOG GET` via an injected executor (`:268-291`), does **NOT** read a Redis Stream | query (pull, stats only) |
| MQTT | `mqtt.py` — **the one real subscribe-and-react connector**: a background `paho-mqtt` client thread pushes normalized messages into a bounded `deque`, `query(spec)` drains it with `kind`/`kinds`/`topic`/`since` filters (`:162-207`); lazy-connects on first `query()` (`:145-160`); `disconnect()` is called by `ConnectorRegistry.close()` teardown (`registry.py:259-271`) | **live subscribe** (the template — §2.4) |
| Datadog / CloudWatch / Azure Monitor / GCP observability | `datadog.py` (logs + metrics query, `DD-API-KEY`/`DD-APPLICATION-KEY` headers), `aws_observability.py` (boto3 SigV4, `mode ∈ {logs, metrics, traces, events}` → CloudWatch Logs/Metrics, X-Ray, CloudTrail, `:1-26`), `azure_monitor.py`, `gcp_observability.py` | query (pull) |
| APM / traces / profiling | `honeycomb.py`, `jaeger.py`, `zipkin.py`, `tempo.py`, `signoz.py`, `parca.py`, `pyroscope.py`, `elastic_apm.py`, `splunk_observability.py`, `dynatrace.py`, `appdynamics.py`, `newrelic.py` | query (pull) |
| Push landing zone | `webhook_buffer.py:36-147 WebhookBufferSource` — a bounded, thread-safe `deque` ring satisfying the same `Source` contract, so pushed records read through `/api/observe/query` exactly like a pulled connector (`push_one`/`push`, `:68-92`; `query` filters by `kind`/`kinds`/`since`, `:95-134`) | buffer (push landing) |

Config shape to copy: `config/examples/connectors_example.yml:1-63` — each
entry is `{name, kind, <selector>, ...settings, *_env}`.

### 1.3 Push/ingest — normalizers exist, the receiving ROUTE does not (key gap)

- `connectors/ingest.py:72-460 normalize(fmt, payload, headers)` — fully
  built, fail-soft (`:37-38`, never raises, bounded at `MAX_PAYLOAD_BYTES = 5
  MiB`, `:52`), covers `fmt="otlp"` (OTLP/JSON `resourceLogs`/`resourceSpans`
  → `logs`/`traces` records, `:165-257`), `fmt="webhook"` (sniffs
  `X-GitHub-Event`, an Alertmanager `alerts[]` shape, or falls back to a
  generic log record, `:272-343`), `fmt="syslog"` (RFC 5424/3164,
  `:389-459`).
- `connectors/ingest_formats.py:1-377` — pure parsers for **log-shipper**
  wire formats: `parse_fluent_forward` (Fluent Forward, JSON or guarded
  msgpack, `:113-190`), `parse_beats_lumberjack` (Elastic Beats v2 events,
  `:198-236`), `parse_gelf` (Graylog GELF, plain or chunked, `:277-376`) — all
  bounded (`MAX_PAYLOAD_BYTES`/`MAX_EVENTS`, `:57-61`) and fail-soft.
- **Confirmed gap**: `make grep Q=api/ingest` returns exactly two hits — the
  docstrings of `ingest.py:4-7` and `webhook_buffer.py:4,8-9` describing the
  intended `POST /api/ingest` endpoint as if it exists. There is **no**
  FastAPI route anywhere in `src/general_ludd/` that actually reads a request
  body, calls `normalize()`, and pushes the result into a
  `WebhookBufferSource`. The normalizer and the buffer are both
  production-ready; nothing wires them to HTTP. This is the single largest
  missing piece for "receive a pushed OTLP/webhook/syslog payload" (§2.4
  closes it).

### 1.4 SUBSCRIBE / real pub-sub consumption — one template, several gaps

Per the family table (§1.2), `mqtt.py` is the **only** connector that
actually subscribes to a live stream and reacts (background thread +
bounded ring + lazy-connect + teardown via `ConnectorRegistry.close()`).
Confirmed absent (`make grep` for each):

- **Kafka**: no `confluent-kafka`/`kafka-python`/`aiokafka` import anywhere in
  `src/`; `kafka_exporter.py` only scrapes a sidecar's Prometheus metrics.
- **NATS**: no `nats-py`/`asyncio-nats-client` import; `nats.py` only scrapes
  the HTTP monitoring endpoints.
- **Redis Streams**: no `XREAD`/`XREADGROUP` call anywhere; `redis_stats.py`
  only runs `INFO`/`SLOWLOG GET`.
- **AWS EventBridge**: `make grep Q=eventbridge` returns exactly one hit —
  `normalize.py:402`, an auth-family classification token
  (`"aws": (..., "eventbridge")`). No connector of any kind exists. (EventBridge
  itself has no direct pull/poll API — see §2.4's `eventbridge_source.py`
  design, which is honestly an SQS consumer, not an EventBridge consumer.)
- **OTLP as a live receiver**: `otel_bridge.py` only *exports* traces; the
  only *ingest* path for OTLP is `ingest.py`'s JSON-mode logs/spans parser,
  itself unreachable per §1.3. No OTLP gRPC receiver, and no OTLP **metrics**
  parsing (`ingest.py` only handles `resourceLogs`/`resourceSpans`, never
  `resourceMetrics`) exists at all.

### 1.5 EMIT — gludd pushing its OWN metrics/events OUT — thin today

- `metrics_exporter.py` is **pull-only** (§1.1) — no Pushgateway client, no
  Prometheus remote-write client.
- `otel_bridge.py` exports **traces only** via OTLP gRPC — no metrics, no
  logs OTLP export.
- `events/hooks.py:100-232 HookSystem` — the one genuinely generic
  **outbound** event sink already built: `register_webhook(event_name, url,
  ...)` (`:130-159`) SSRF-guards the URL at registration
  (`_ensure_safe_webhook_url`, `:24-37`, delegating to the canonical
  `is_url_blocked`), clamps `retry_count` to 1-5 (D-34, `:141-143`), and
  `fire(event_name, payload)` (`:167+`) redacts secret-shaped keys
  (`_redact_payload`, `:53-79`, strips any key containing `api_key`/`token`/
  `secret`/`password`/`credential`/`authorization`) before POSTing. **This is
  already a working "emit a generic event to an arbitrary webhook sink"
  primitive — reuse it, do not rebuild it** (§2.4's `webhook_event_emitter.py`
  is a thin adapter, not a new implementation).
- No StatsD/DogStatsD UDP emit client, no Graphite plaintext-protocol write
  client, no InfluxDB line-protocol write client, no Prometheus Pushgateway
  POST client, no CloudWatch `PutMetricData` call, no Kafka producer, no NATS
  publish, no Redis Streams `XADD`, no EventBridge `PutEvents` — **none of
  these exist as an outbound capability anywhere in the tree**, even though
  several of the same backends already have a *read* connector (Graphite,
  InfluxDB, CloudWatch). §2.4 closes this with a new, parallel
  `observe_emit/` package.

### 1.6 Credentials — the pattern to reuse (deliberately NOT `secrets/`)

- Every connector resolves a credential from `os.environ` **at call time**
  via a `*_env` config key naming the env var (never the value) — e.g.
  `prometheus.py:187-188,199-205 token_env`, `datadog.py api_key_env`/
  `app_key_env`, `mqtt.py:89-90,243-247 username_env`/`password_env`.
- `connectors/normalize.py:401-435 auth_family()` classifies a
  source/connector name into an auth family (`aws`/`azure`/`gcp`/`grafana`/
  `datadog`/`elastic`/`github`/`gitlab`/`splunk`/`newrelic`/`pagerduty`);
  `:438-494 bundle_credentials()` collects the **env-var NAMES** (never
  values) declared per config so one resolved credential can fan out to
  every connector in that family.
- `src/general_ludd/secrets/` (`config.py`, `manager.py`, `project_secrets.py`,
  `env.py`, `payment_vault.py`, `cosign.py`, `gitsign.py`) is a **separate**
  subsystem for gludd's own operational secrets (OpenBao/Vault, payment
  vault, signing). Connectors deliberately do **not** route through it —
  the `*_env` convention lets an operator's existing env-injection mechanism
  (k8s `Secret`, systemd `EnvironmentFile`, Vault Agent sidecar) just work
  without gludd needing a secrets-manager integration per backend. **New
  emit/subscribe modules in this design MUST follow the same `*_env`
  convention**, not reach into `secrets/`.

### 1.7 Ansible module + role conventions to mirror

- `gludd_dispatch.py:120-139` / `gludd_metrics.py:91-107` — the
  `daemon_url`/`psk` (`no_log: true`)/`timeout` triplet, `GluddClient`
  HTTP wrapper, `ok_result`/`error_result` helpers. Every new module in this
  design follows this exact triplet — it talks to the gludd **daemon**, which
  owns the `ConnectorRegistry`/new `EmitterRegistry`; it never embeds a
  backend token itself.
- `PIPELINE_INTERACTION_ROLES.md`'s cited `gludd_break_glass.py` check-mode
  **refusal** pattern for mutating verbs — reused here for `observability_
  bootstrap`'s connector-registration verb (§2.2).
- `ci_pipeline_verify/tasks/main.yml:35-49` — the `uri`+`register`+`until`+
  `retries`+`delay` polling idiom, reused verbatim by `metrics_query`'s
  wait-for-threshold mode (§3).
- `scripts/gen_mcp_tools.py` auto-generates MCP tool definitions from every
  `gludd_*.py` module's `DOCUMENTATION`/`argument_spec` — no separate MCP
  registration step for any module this design adds; `no_log: true` options
  surface as `x-no-log: true` automatically.
- `collection-roles` (109 existing roles) has no `metrics_*`/`event_*`/
  `observability_*` role today — closest neighbors are `report_metrics`
  (reads gludd's own `gludd_metrics`/`gludd_traces` facts, §1.1 — a
  consumer of gludd's *internal* state, not of an external backend) and
  `watchdog_check` (a different, narrower liveness check). No collision.

---

## 2. DESIGN

### 2.1 Role roster (`collections/ansible_collections/general_ludd/agent/roles/`)

| Role | One-line purpose |
|---|---|
| `metrics_discover` | Probe a target host/environment for which metrics/event backend(s) it already runs; return ranked candidates + a ready-to-register connector-config stub. |
| `metrics_query` | Run a query (PromQL instant/range, or any registered connector's generic `spec`) against ONE registered backend; register the result + an optional threshold verdict as facts. |
| `metrics_emit` | Emit a counter/gauge/histogram sample, or a generic structured event, to one or more registered emitters. |
| `event_subscribe` | Ensure a background subscriber connector (MQTT/Kafka/NATS/Redis Streams/webhook-ingest buffer) is live; drain buffered messages since a checkpoint for the calling playbook to react to. |
| `observability_bootstrap` | Turn a `metrics_discover` finding (or an operator-supplied stanza) into a LIVE registered connector or emitter, without restarting the daemon. |
| `observability_capability` | Thin capability-marker role (mirrors `timekeeper`/`budget_guard`) granting an agent type permission to call the four roles above. |

### 2.2 Per-role inputs/outputs

#### `metrics_discover`

- **Module args** (`gludd_observe_discover`, new): `target_host` (str,
  required), `candidate_ports` (list, default covers the well-known ports
  below), `probe_timeout` (float, default 5.0), `daemon_url`/`psk`/`timeout`
  triplet.
- **Flow**: calls `POST /api/observe/discover` (a thin, read-only wrapper
  around the new `connectors/discovery.py`, §2.4) — check-mode safe (pure
  read).
- **Returned facts**: `ansible_facts.gludd_discovery = {"candidates":
  [{"kind": "prometheus_scrape", "confidence": 0.9, "base_url": "...",
  "suggested_config": {...connector entry...}}, ...], "environment_hints":
  {"aws_region_present": true, "datadog_api_key_env_present": false, ...}}`.
  `environment_hints` names only **env-var presence**, never a value
  (mirrors §1.6's never-dereference posture).

#### `metrics_query`

- **Module args** (`gludd_metrics_query`, new): `source` (registered
  connector name, required), `spec` (dict — PromQL sugar: `promql`/`start`/
  `end`/`step`/`time` keys pass straight through to `PrometheusSource.query`;
  any other connector reads its own `spec` shape), `threshold_expr`
  (optional Jinja-evaluable comparison string, e.g. `"value > 5.0"`),
  `daemon_url`/`psk`/`timeout` triplet. Check-mode safe (read-only).
- **Flow**: `POST /api/observe/query` (existing, unchanged) with `{source,
  spec}`; if `threshold_expr` is set, evaluate it against the returned
  record(s) and add a `breached: bool` verdict.
- **Returned facts**: `ansible_facts.gludd_query_result = {"records": [...],
  "count": N, "breached": bool|null}`.
- **Wait-for-threshold mode**: `roles/metrics_query/tasks/wait_for_threshold.yml`
  reuses the `ci_pipeline_verify` polling idiom (§1.7) — `uri`-equivalent
  `general_ludd.agent.gludd_metrics_query` task wrapped in `until: [
  gludd_query_result.breached ]`, `retries`/`delay` from role defaults.

#### `metrics_emit`

- **Module args** (`gludd_metrics_emit`, new): `backends` (list of
  registered emitter names, or the literal `"all"`), `metric_type`
  (`counter|gauge|histogram|event`, required), `name` (metric/event name,
  required), `value` (float, required unless `metric_type == "event"`),
  `labels` (dict, default `{}`), `message` (str, event body — required when
  `metric_type == "event"`), `daemon_url`/`psk`/`timeout` triplet.
  Check-mode: **refuses** (mirrors `gludd_break_glass.py`'s restore
  refusal, §1.7) — emitting is always an external side effect.
- **Flow**: `POST /api/observe/emit` (new, §2.4) → `EmitterRegistry.emit(name,
  record)` fanned across the named backends; a per-backend failure is
  captured, never raised (mirrors `ConnectorRegistry.health_all`'s
  never-abort-the-sweep posture, `registry.py:238-256`).
- **Returned facts**: `ansible_facts.gludd_emit_result = {"per_backend":
  {"prod-statsd": {"ok": true}, "prod-cloudwatch": {"ok": false, "detail":
  "..."}}, "any_failed": bool}`.

#### `event_subscribe`

- **Module args** (`gludd_event_subscribe`, new): `subscriber` (registered
  connector name — an MQTT/Kafka/NATS/Redis-Streams/webhook-buffer source,
  required), `since` (checkpoint epoch seconds, optional — omit for "all
  buffered"), `kinds` (optional filter list), `daemon_url`/`psk`/`timeout`
  triplet. Check-mode safe (draining a buffer is a read, never a mutation of
  the upstream broker).
- **Flow**: subscribers ARE connectors (§1.4) — `query(spec)` already drains
  the background thread's ring buffer with exactly these filters (`mqtt.py:
  162-207`'s shape, generalized). This module is a thin `gludd_metrics_query`
  sibling that additionally tracks `since` as a returned checkpoint so a
  playbook loop never re-processes the same message twice.
- **Returned facts**: `ansible_facts.gludd_events = {"records": [...],
  "count": N, "next_since": <max ts seen, for the next call's since=>}`.
- **React step**: `roles/event_subscribe/tasks/react.yml` (optional, gated
  by a `react_todo_template` var) creates a todo per matching record via the
  **existing** todo-creation path (`routers/todos.py` `AddTodoRequest` — no
  new plumbing) — mirrors `TIME_TIMERS_SCOPED_NOTIFICATIONS.md`'s
  `payload_todo` pattern of composing "reacting to an event" with the
  existing todo system rather than inventing a parallel action mechanism.

#### `observability_bootstrap`

- **Module args** (`gludd_connector_config`, new): `entry` (a full connector
  or emitter config dict — typically a `metrics_discover` `suggested_config`
  copied verbatim, or an operator-authored stanza), `registry` (`connector|
  emitter`), `persist` (bool, default `false` — session-only vs. written back
  to the on-disk user config), `daemon_url`/`psk`/`timeout` triplet.
  Check-mode: **refuses**, exactly like `gludd_break_glass.py` — registering
  a new live egress target is always a mutation with real-world effect
  (a new outbound connection, a new credential read).
- **Flow**: `POST /api/observe/connectors` (new, admin, §2.4) appends `entry`
  to the in-memory config list and re-invokes `wire_observability(app,
  daemon_state, updated_config)` (or the new `wire_emitters` for
  `registry: emitter`) — `ConnectorRegistry`/`EmitterRegistry` construction
  is idempotent and `wire_observability` already tears down the OLD
  registry's background threads via `.close()` (`observe.py:280-283`) before
  building the new one, so re-registration never leaks an `MqttSource`
  subscriber thread. When `persist: true`, the entry is additionally
  appended to the on-disk `connectors:`/`emitters:` YAML list so it survives
  a daemon restart.
- **Returned facts**: `ansible_facts.gludd_connector_registered = {"name":
  ..., "kind": ..., "ok": bool, "errors": [...]}` — surfaces
  `ConnectorRegistry.errors()`/`EmitterRegistry.errors()` (existing
  best-effort-skip pattern, `registry.py:123-176`) rather than failing the
  whole call on one bad entry.

#### `observability_capability`

- No module calls — a pure capability-declaration role
  (`tasks/main.yml` is a single `ansible.builtin.debug` asserting the
  capability, mirroring how `timekeeper`/`budget_guard` are structured
  per `TIME_TIMERS_SCOPED_NOTIFICATIONS.md` §T-4). A playbook
  `include_role: timekeeper`-equivalents `include_role:
  observability_capability` to assert "this agent may call
  metrics_query/metrics_emit/event_subscribe/observability_bootstrap"
  before composing them, tying to the new `metrics:<backend>`/
  `events:<backend>` `Capability` (§2.5).

### 2.3 Auto-detection algorithm (`metrics_discover` / `connectors/discovery.py`)

A concrete, safe, timeout-bound probe order (every probe reuses
`is_url_blocked`/`host_is_blocked` from `security/ssrf.py` — discovery must
never become an SSRF oracle for the internal network it's told to scan):

1. **Prometheus exposition sniff** — `GET {base_url}/metrics` (mirrors
   `prom_scrape.py`'s own fetch); a 2xx body containing a `# TYPE ` line ⇒
   `kind="prometheus_scrape"`, confidence `0.9`.
2. **Prometheus server** — `GET {base_url}:9090/api/v1/query?query=1` (the
   same cheap liveness query `PrometheusSource.health()` uses,
   `prometheus.py:361-389`); a `{"status":"success"}` body ⇒
   `kind="prometheus_server"`, confidence `0.95`.
3. **OTLP HTTP collector** — `POST {base_url}:4318/v1/traces` with an empty
   body; a `400` (malformed-request, meaning something IS listening and
   speaking OTLP) rather than a connection failure or `404` ⇒
   `kind="otlp_http_collector"`, confidence `0.7`.
4. **NATS / MQTT / Redis monitoring** — reuse the EXISTING connectors'
   `health()` against the candidate port (`nats.py`'s `/varz`,
   `mqtt.py`-shaped broker-port TCP-connect, `redis_stats.py`'s `PING`) —
   discovery does not reimplement these checks, it calls them.
5. **Vendor-key environment hints** (name-presence only, never
   dereferenced, per §1.6): `DATADOG_API_KEY`, `NEW_RELIC_LICENSE_KEY`,
   `HONEYCOMB_API_KEY`, `AWS_REGION`/`AWS_EXECUTION_ENV` (CloudWatch
   candidate), `AZURE_SUBSCRIPTION_ID`, `GOOGLE_CLOUD_PROJECT`.
6. **StatsD is deliberately NOT active-probed.** UDP has no connection
   handshake to ack — sending a garbage packet to guess liveness is blind
   and can pollute a real StatsD daemon's counters. Report
   `kind="statsd_configured"` only when `STATSD_HOST`/`STATSD_PORT`-shaped
   env vars are present, never by sending traffic.

Each candidate carries a `suggested_config` — a connector-entry dict shaped
exactly like `config/examples/connectors_example.yml`'s entries, ready for
`observability_bootstrap` to register verbatim.

### 2.4 Net-new Python modules

| Module | Purpose |
|---|---|
| `src/general_ludd/connectors/discovery.py` | `BackendProbe`/`DiscoveryReport` implementing §2.3. Read-only, reuses the injectable-transport + SSRF-guard idiom of every existing connector; never mutates. |
| `src/general_ludd/connectors/kafka_consumer.py` | Real Kafka topic consumer — `confluent-kafka` (preferred) or `aiokafka`, lazy-guarded import (mirrors `mqtt.py:232-243`'s `paho.mqtt` guard). Same shape as `MqttSource`: background thread, bounded `deque`, `query(spec)` drains with `kind`/`topic`/`since` filters, `disconnect()` for registry teardown. `KIND="logs"` by default (per-record kind sniffable from a header, config-overridable). |
| `src/general_ludd/connectors/nats_subscribe.py` | Real NATS core/JetStream subject subscribe (`nats-py`, guarded import). Same `MqttSource`-shaped background-thread/ring-buffer/teardown contract. Distinct module from `nats.py` (monitoring-HTTP scrape) — sibling, not a rewrite. |
| `src/general_ludd/connectors/redis_streams.py` | `XREADGROUP` consumer loop in a background thread, same shape. Distinct module from `redis_stats.py` (`INFO`/`SLOWLOG`) — sibling, not a rewrite. |
| `src/general_ludd/connectors/eventbridge_source.py` | EventBridge has **no direct pull/poll API** — an EventBridge rule must target something pollable. This connector is honestly an **SQS consumer** for a queue an EventBridge rule targets (boto3, guarded import, mirrors `aws_observability.py`'s ambient-credential-chain posture, no SSRF guard needed). Document this explicitly in the module docstring so no one expects a phantom EventBridge-native pull. |
| `src/general_ludd/observe_emit/base.py` (new package) | `Emitter` Protocol (`name`, `KIND`, `health()`, `emit(records: list[EmitRecord]) -> EmitResult`, never raises) — the emit-side analogue of `connectors/base.py`'s `Source` Protocol. |
| `src/general_ludd/observe_emit/registry.py` | `EmitterRegistry.from_config` — copies `connectors/registry.py:94-476`'s allowlist-then-validate-then-construct sequence verbatim, scoped to `general_ludd.observe_emit.*` (a **separate** frozenset from `_ALLOWED_CONNECTOR_MODULES` so a config entry can never smuggle a connector module in as an emitter or vice versa). |
| `src/general_ludd/observe_emit/statsd_emitter.py` | UDP StatsD/DogStatsD line-protocol emit (fire-and-forget — UDP has no ack, so `health()` can only report `"socket open"`, never `"backend reachable"`; document this limitation inline, mirroring §2.3's active-probe caveat). |
| `src/general_ludd/observe_emit/graphite_emitter.py` | Plaintext Graphite protocol (`metric value timestamp\n`) over TCP. |
| `src/general_ludd/observe_emit/influxdb_emitter.py` | InfluxDB line-protocol HTTP write (`POST /api/v2/write` v2 or `/write` v1), `token_env`/`org`/`bucket` config, mirrors `influxdb.py`'s (read-side) SSRF/transport posture. |
| `src/general_ludd/observe_emit/pushgateway_emitter.py` | Prometheus Pushgateway — POST text-exposition format to `/metrics/job/<job>[/instance/<instance>]`. |
| `src/general_ludd/observe_emit/cloudwatch_emitter.py` | `boto3` `cloudwatch.put_metric_data`, mirrors `aws_observability.py`'s guarded-import + ambient-credential-chain posture (no SSRF guard — boto3 resolves its own signed endpoint). |
| `src/general_ludd/observe_emit/otlp_metrics_emitter.py` | Sibling to `otel_bridge.py`, but for **metrics**: `opentelemetry.sdk.metrics` + `OTLPMetricExporter`, reusing the exact `_check_otel_available()`-shaped guarded-import idiom (`otel_bridge.py:11-23`) so the optional OTel deps stay optional. |
| `src/general_ludd/observe_emit/kafka_producer_emitter.py` | Mirrors `kafka_consumer.py`'s guarded import; produces instead of consumes. |
| `src/general_ludd/observe_emit/webhook_event_emitter.py` | **Thin adapter, not a new implementation** — delegates to the EXISTING `events/hooks.py HookSystem.fire`/`register_webhook` (§1.5). Do not reimplement SSRF guarding or secret redaction; adapt the `Emitter` Protocol shape onto the already-hardened primitive. |
| `src/general_ludd/routers/observe_emit.py` (new router, sibling to `routers/observe.py`) | `POST /api/observe/discover` (wraps `discovery.py`, read-only); `POST /api/observe/emit` (wraps `EmitterRegistry.emit`); `POST /api/observe/connectors` (admin, mutating — append + rewire, §2.2's `observability_bootstrap`); and — closing §1.3's gap — `POST /api/ingest` (finally wires `connectors.ingest.normalize()` + `connectors.ingest_formats.*` to a `WebhookBufferSource` instance the `ConnectorRegistry` holds under a well-known name, e.g. `"ingest-buffer"`, created automatically by `wire_observability` when absent from operator config). |
| `config/user_config.py:174` (edit) | Add a sibling `emitters: list[dict[str, Any]] = []` field next to the existing `connectors` field, wired the same way at `daemon.py:2941-2943`'s call site (a new `wire_emitters(app, daemon_state, _emitter_cfg)` call added alongside `wire_observability`). |

### 2.5 Capability gating

- New `Capability` resources `"metrics:<backend>"` (`actions`: `query`,
  `emit`) and `"events:<backend>"` (`actions`: `subscribe`, `emit`) — same
  shape as `security/permissions.py:61-78`'s `Capability(resource, actions,
  constraints)` and the `"pipeline:<provider>"` precedent already
  established by `PIPELINE_INTERACTION_ROLES.md` §1.5/§5. Checked at
  `routers/observe_emit.py`'s new endpoints BEFORE any `ConnectorRegistry`/
  `EmitterRegistry` call.
- `observability_bootstrap`'s mutating `POST /api/observe/connectors`
  additionally requires a registry-level `allowed_kinds`/`allowed_verbs`
  allowlist on the admin config — the same belt-and-suspenders double-gate
  `PIPELINE_INTERACTION_ROLES.md` §2.4 specifies for
  `PipelineProviderRegistry`, so a capability-layer bug alone cannot let an
  agent register an arbitrary module.
- **Shared dependency, not re-fixed here**: Wave C finding C-SEC-1 (`denied`
  capabilities not yet enforced at the intersection/STS layer,
  `docs/design/WAVE_C_DESIGNS_2026-07-10.md:16-42`) applies to
  `metrics:*`/`events:*` denials exactly as it does to `pipeline:*` — note
  the dependency, don't silently assume a deny carve-out works until that
  lands.
- Never log a resolved credential, `Authorization` header, or PSK in any
  error/exception string — mirrors `base.py:452-477`'s `_is_configured`
  posture and `registry.py:251-253`'s explicit refusal to leak `str(exc)`
  into a health manifest.

---

## 3. Decision-making example: query a metric, branch on a threshold

```yaml
# roles/metrics_query/tasks/wait_for_threshold.yml (excerpt)
# Poll a registered Prometheus source's error-rate PromQL until it breaches
# 5%, then escalate — mirrors ci_pipeline_verify/tasks/main.yml:35-49's
# uri+until+retries+delay idiom, substituted with gludd_metrics_query.

- name: Poll error rate until threshold breach or timeout
  general_ludd.agent.gludd_metrics_query:
    daemon_url: "{{ daemon_url }}"
    psk: "{{ psk }}"
    source: "{{ metrics_source }}"          # e.g. "prod-prometheus"
    spec:
      promql: >-
        sum(rate(http_requests_total{status=~"5.."}[5m]))
        / sum(rate(http_requests_total[5m]))
    threshold_expr: "value > 0.05"
  register: _query_poll
  until:
    - _query_poll.gludd_query_result.breached is not none
  retries: "{{ (metrics_poll_timeout_seconds | int) // (metrics_poll_interval | int) }}"
  delay: "{{ metrics_poll_interval }}"
  ignore_errors: true

- name: Escalate to a human when the error-rate budget is blown
  general_ludd.agent.gludd_human_todo:
    state: present
    title: "Error-rate budget breached on {{ metrics_source }}"
    body: "{{ _query_poll.gludd_query_result.records }}"
    category: "blocker"
  when: _query_poll.gludd_query_result.breached | default(false)

- name: Otherwise, hand off to drive_pipeline for an automated rollback
  ansible.builtin.include_role:
    name: drive_pipeline          # docs/design/PIPELINE_INTERACTION_ROLES.md
  vars:
    pipeline_provider: "{{ rollback_pipeline_provider }}"
    pipeline_name: "rollback-prod"
  when:
    - _query_poll.gludd_query_result.breached | default(false)
    - rollback_pipeline_provider is defined
```

This is the concrete "query → branch on threshold → act" loop the brief
asks for: `metrics_query` supplies the observation, the branch is a plain
Ansible `when:`, and the action reuses **existing** primitives
(`gludd_human_todo`, `drive_pipeline`) rather than inventing a new escalation
mechanism — same "compose, don't duplicate" posture as
`TIME_TIMERS_SCOPED_NOTIFICATIONS.md`'s `payload_todo`.

---

## 4. Config schema

**Wiring path (mirrors the connectors' — verified this survey).**
`config/general-ludd.yml` → `UserConfig.connectors: list[dict]`
(`config/user_config.py:174`) → `daemon.py:2941-2943` →
`wire_observability()` (`routers/observe.py:249-297`) →
`ConnectorRegistry.from_config(...)`. This design adds a **sibling**
`UserConfig.emitters` field and a `wire_emitters()` builder from the same
daemon-startup site — connectors (read) and emitters (write) are never
confused at config or registry level, matching
`PIPELINE_INTERACTION_ROLES.md` §6's `pipeline_drive` precedent.

```yaml
# config/examples/connectors_example.yml — new entries this design adds
connectors:
  # --- real subscribe (background thread + ring buffer) ------------------
  - name: prod-kafka-orders
    kind: logs
    module: kafka_consumer
    bootstrap_servers: "kafka.internal.example.com:9092"
    topics: ["orders.events"]
    group_id: "gludd-observer"
    allow_private: true               # internal broker, mirrors kubernetes.py's opt-in

  - name: prod-redis-stream
    kind: events
    module: redis_streams
    url_env: REDIS_URL
    stream: "ops:events"
    group: "gludd-observer"

  - name: prod-eventbridge-queue
    kind: events
    module: eventbridge_source
    queue_url: "https://sqs.us-east-1.amazonaws.com/123456789012/eb-target"
    region: "us-east-1"
    # No *_env: ambient boto3 credential chain, same posture as aws_observability.py

  # --- push landing zone (auto-created if absent) -------------------------
  - name: ingest-buffer
    kind: logs
    module: webhook_buffer
    maxlen: 5000

emitters:
  - name: prod-statsd
    kind: metrics
    module: statsd_emitter
    host: "statsd.internal.example.com"
    port: 8125
    allow_private: true

  - name: prod-pushgateway
    kind: metrics
    module: pushgateway_emitter
    base_url: "http://pushgateway.internal.example.com:9091"
    job: "gludd-agent"
    allow_private: true

  - name: prod-cloudwatch
    kind: metrics
    module: cloudwatch_emitter
    namespace: "GluddAgent"
    region: "us-east-1"

  - name: prod-otlp-metrics
    kind: metrics
    module: otlp_metrics_emitter
    endpoint: "otel-collector.internal.example.com:4317"
    allow_private: true

  - name: prod-webhook-events
    kind: events
    module: webhook_event_emitter
    url: "https://hooks.internal.example.com/gludd"
```

---

## 5. Test plan

**Precedent test files to mirror (verified this survey):**

- Connector unit tests inject a mock transport recording `(url, headers)`
  and returning canned `(status, body)` by URL substring — zero real
  network: `tests/unit/test_metrics_exporter.py`, `tests/unit/
  test_connector_webhook_buffer.py`. The new `kafka_consumer.py`/
  `nats_subscribe.py`/`redis_streams.py` tests copy `mqtt.py`'s test shape
  (`tests/unit/test_connector_mqtt*.py`-equivalent — inject a fake
  client/subscriber, assert the background thread pushes into the ring,
  assert `query()`'s `kind`/`since`/topic-equivalent filters).
- SSRF/no-redirect coverage: `tests/security/test_connector_ssrf_no_redirect.py`
  — the new connectors/emitters with an HTTP(S) `base_url` join the existing
  parametrized groups there.
- Registry RCE-prevention precedent: `tests/unit/test_connector_registry.py`,
  `tests/unit/test_connector_registry_import_guard.py` — `EmitterRegistry`
  gets a mirrored `tests/unit/test_emitter_registry_import_guard.py` proving
  a `module` outside `general_ludd.observe_emit.*` is rejected.
- Ansible-module tests: `tests/unit/test_gludd_git_module.py:46+`-style
  `_FakeAnsibleModule` stand-in (records `argument_spec`/`check_mode`,
  captures `exit_json`/`fail_json`); `tests/unit/test_gludd_embed_module.py`'s
  check-mode-refusal shape is the template for `gludd_metrics_emit`/
  `gludd_connector_config`'s mutating-verb tests.
- Full-wiring round-trip: `tests/integration/test_obs_connector_e2e.py` —
  the new `tests/integration/test_observe_emit_e2e.py` mirrors it for
  `/api/observe/emit`/`/api/observe/discover`/`/api/observe/connectors`/
  `/api/ingest`.

**New tests:**

1. `connectors/discovery.py` — each probe class (`prometheus_scrape`,
   `prometheus_server`, `otlp_http_collector`) against a mocked transport
   returning the expected signature; confirms StatsD is NEVER active-probed
   (assert no UDP socket send occurs when only env hints are checked);
   confirms every probe URL is rejected when it points at
   `169.254.169.254`/`metadata.google.internal`/an RFC-1918 address without
   `allow_private`.
2. `POST /api/ingest` — OTLP/webhook/syslog payloads land in the
   `"ingest-buffer"` `WebhookBufferSource` and are readable via `/api/observe/
   query`; an oversized payload (`> MAX_PAYLOAD_BYTES`) is rejected with
   `[]`/`400`, never buffered; a malformed payload degrades to `[]`, never a
   500.
3. `kafka_consumer.py`/`nats_subscribe.py`/`redis_streams.py` — a mocked
   consumer/subscriber delivering N messages results in exactly N buffered
   records, retrievable via `query({"since": ts})` with correct
   checkpoint semantics; `disconnect()`/`close()` stops the background
   thread (assert via a thread-count check, mirroring any existing
   `mqtt.py` teardown test).
4. `EmitterRegistry.from_config` — rejects a `module` outside
   `general_ludd.observe_emit.*`; a malformed entry lands in `errors()`
   without aborting the build (mirrors `ConnectorRegistry`'s
   `test_connector_registry.py` shape).
5. `statsd_emitter.py`/`graphite_emitter.py`/`influxdb_emitter.py`/
   `pushgateway_emitter.py`/`cloudwatch_emitter.py`/`otlp_metrics_emitter.py`
   — each against an injected transport/socket double, asserting the exact
   wire payload (StatsD line format, Graphite `metric value ts\n`, InfluxDB
   line protocol, Pushgateway text exposition, CloudWatch `put_metric_data`
   call args, OTLP metric point) — never a real network call.
6. `webhook_event_emitter.py` — asserts it calls `HookSystem.fire`/
   `register_webhook` rather than reimplementing SSRF/redaction (a spy on
   `HookSystem` methods, asserting zero direct `httpx` calls from this
   module).
7. Capability gate: a role whose `PermissionSpec` lacks a
   `metrics:<backend>`/`events:<backend>` capability (or the right
   `actions`) is refused BEFORE any `ConnectorRegistry`/`EmitterRegistry`
   call — mirrors `tests/unit/test_dispatch_permission_gate.py`'s
   fail-closed assertions.
8. `gludd_metrics_query`/`gludd_metrics_emit`/`gludd_observe_discover`/
   `gludd_event_subscribe`/`gludd_connector_config` — `_FakeAnsibleModule`
   unit tests per module (argument validation, check-mode behavior —
   read verbs safe, mutating verbs refuse); `gludd_connector_config`'s
   refusal test additionally asserts `wire_observability`/`wire_emitters`
   is never called under `check_mode`.
9. `roles/metrics_query/molecule/wait_for_threshold` (and one scenario per
   other role, mirroring existing `molecule/roles/*` scaffolding) —
   end-to-end: discover → register → query → threshold branch → (mocked)
   escalation, `make ansible-lint-playbooks` clean.
10. Regression: `test_molecule_coverage.py`'s role-inventory test gains the
    six new role names (mirrors how it already inventories `report_metrics`
    et al., `tests/integration/test_molecule_coverage.py:180`).
