# Connector Join-Key Normalization — Design Document

**Issue context:** #73 (observe roles cross-source debugging), #74 (connector landing)
**Status:** Layer already partially implemented; this document audits what exists,
identifies gaps, and specifies the remaining implementation work for a follow-up
developer.

---

## 1. Problem statement

The ~40 connectors landed in commit 4314a6c each emit `NormalizedRecord` dicts
shaped `{ts, source, kind, level_or_status, message, value, labels, raw}`. Their
`labels` sub-dicts are heterogeneous: the same logical entity is called by
different names across vendors:

| Entity | Vendor label key variants found in connectors |
|---|---|
| Distributed trace ID | `trace_id` (Tempo, Zipkin, SigNoz), `trace.id` (Elasticsearch ECS), NRQL-projected `trace.id` (New Relic), `x-b3-traceid` (Zipkin B3 propagation) |
| Span ID | `span_id` (Tempo, Zipkin, SigNoz), `span.id` (Elasticsearch ECS), `spanId` (Tempo alt) |
| Host / machine | `host` (Datadog, Loki stream labels, Splunk), `hostname` (generic), `instance` (Prometheus), `computer` (Windows Event Log, Azure Monitor `Computer`), `node` (Kubernetes-aware Prometheus) |
| Service / app | `service` (Tempo, Zipkin), `service.name` (SigNoz, Elasticsearch ECS), `app` (Loki stream label), `job` (Prometheus), `container_name` (Docker Engine) |
| Container | `container_id` (Docker Engine), `container_name` (Docker Engine, Loki), `container` (Kubernetes Prometheus labels) |
| Kubernetes pod | `pod` (Loki, Prometheus), `pod_name` (Prometheus alt) |
| Kubernetes namespace | `namespace` (Loki, Prometheus), `kubernetes_namespace` (Kubernetes-aware shippers) |
| Cloud region | `region` (AWS-style), `location` (Azure), `availability_zone` / `zone` (GCP/AWS-alt) |
| Cloud account | `account` / `account_id` / `aws_account_id` (AWS), `subscription_id` (Azure), `project_id` (GCP) |
| Request correlation | `CorrelationId` (Azure Monitor KQL rows) |

Without a normalization layer these differences make cross-source incident
correlation impossible without connector-specific code at every call site.

---

## 2. What already exists

### 2.1 `src/general_ludd/connectors/normalize.py` — the core layer

This module is already implemented and covers most of the design described in
section 3. Key public API:

```python
normalize_join_keys(record: dict) -> dict
```
Returns `record` with a `join` sub-dict computed from its `labels`. The `join`
dict contains only the keys that have a source label (never `None` values):

| Canonical join key | Type | Alias inputs |
|---|---|---|
| `trace_id` | `str` | `trace_id`, `traceid`, `x-b3-traceid`, `x_b3_traceid` |
| `host` | `str` | `host`, `hostname`, `instance`, `computer`, `node` |
| `service` | `str` | `service`, `app`, `application`, `job`, `unit`, `container_name` |
| `k8s.namespace` | `str` (inside `k8s` dict) | `namespace`, `k8s_namespace`, `kubernetes_namespace` |
| `k8s.pod` | `str` (inside `k8s` dict) | `pod`, `k8s_pod`, `pod_name` |
| `k8s.container` | `str` (inside `k8s` dict) | `container`, `k8s_container`, `container_name` |
| `cloud.account` | `str` (inside `cloud` dict) | `account`, `account_id`, `aws_account`, `aws_account_id` |
| `cloud.project` | `str` (inside `cloud` dict) | `project`, `project_id`, `gcp_project` |
| `cloud.subscription` | `str` (inside `cloud` dict) | `subscription`, `subscription_id`, `azure_subscription` |
| `cloud.region` | `str` (inside `cloud` dict) | `region`, `location`, `availability_zone`, `zone` |
| `severity` | `str` | `level`, `level_or_status`, `status`, `severity`, `priority`; folded into `{debug,info,warn,error,critical}` |

The `host` value is lower-cased and has any `:port` suffix stripped (IPv6-aware).

```python
correlate(records: list[dict], by: str) -> dict[str, list[dict]]
```
Groups records by a single `join` key. Normalizes on the fly. Structured keys
(`k8s`, `cloud`) are rendered as stable `"a=1,b=2"` strings for grouping.
Records lacking the key are dropped.

```python
auth_family(name: str) -> str
bundle_credentials(configs: list[dict]) -> dict[str, list[str]]
```
Classify a connector name into 11 auth families (aws / azure / gcp / grafana /
datadog / elastic / github / gitlab / splunk / newrelic / pagerduty) and collect
credential env-var names per family without ever reading secret values.

**Hard invariants already enforced:** idempotent, total (never raises), pure
stdlib, never reads secret values.

### 2.2 `src/general_ludd/observe/facade.py` — GluddObserve

`GluddObserve` already composes `normalize_join_keys` and `correlate` into four
debugging primitives:

- `query_sources(kinds, spec, start, end)` — fan-out with per-source error
  isolation, time-bounded, time-ordered merge
- `correlate_incident(seed, kinds, by, window_s, spec)` — pulls records in a
  time window around an incident seed and groups by canonical join key
- `timeline(window, spec, start, end)` — merged time-ordered stream
- `topology(kinds, spec)` — service↔host adjacency map

The facade accepts any provider shape (dict / callable / registry-like) so it
already composes with `ConnectorRegistry` without a hard dependency.

### 2.3 `src/general_ludd/connectors/registry.py` — ConnectorRegistry

Wires ~50 connectors into a runtime name→source map. Exposes `by_kind()` /
`list_sources()` / `query(name, spec)` / `health_all()` / `errors()`. Already
used as `GluddObserve`'s preferred provider shape.

### 2.4 `src/general_ludd/connectors/base.py` — NormalizedRecord contract

Defines the 8-field `NormalizedRecord` TypedDict, the `Source` protocol, marker
subtypes, and the `Observability` facade (lower-level than `GluddObserve`).

---

## 3. Canonical join-key vocabulary (reference)

The full canonical vocabulary the normalization layer targets:

```text
join = {
    # Distributed tracing
    "trace_id":  str,      # globally unique trace identifier
    "span_id":   str,      # span within a trace (NOT yet in normalize.py — gap)

    # Infrastructure
    "host":      str,      # lower-cased, port-stripped host or IP
    "service":   str,      # logical service / application name

    # Kubernetes coordinates (sub-dict, only present keys)
    "k8s": {
        "namespace": str,
        "pod":       str,
        "container": str,
    },

    # Cloud coordinates (sub-dict, only present keys)
    "cloud": {
        "account":      str,   # AWS account ID or equivalent
        "project":      str,   # GCP project ID
        "subscription": str,   # Azure subscription ID
        "region":       str,   # region / zone / location
    },

    # Severity (folded to 5 levels)
    "severity": "debug" | "info" | "warn" | "error" | "critical",

    # NOT YET CANONICAL — gaps noted in section 4
    # "request_id": str,   # application-layer request ID (not yet aliased)
    # "span_id":    str,   # span ID (extracted by ES connector but not surfaced)
}
```

---

## 4. Gap analysis — what is missing

### G1. `span_id` alias not in `normalize_join_keys`

Tempo, Zipkin, and SigNoz all emit `span_id` in `labels`. The Elasticsearch
connector extracts `span.id` from ECS `_source` to decide record kind but does
NOT add it to `labels`, so it never reaches `normalize_join_keys`.

**Impact:** Cannot correlate spans across backends (needed for flamegraph-style
cross-backend trace assembly).

**Fix:** Add `_SPAN_ALIASES = ("span_id", "spanid", "span.id", "x-b3-spanid")`
and wire it into `_derive_join` alongside `trace_id`.

### G2. `request_id` / `correlation_id` has no canonical alias

Azure Monitor emits `CorrelationId` in KQL rows. New Relic NRQL projections may
include `request.id`. Honeycomb rows may carry any user-defined attribute.
None of these have a canonical `request_id` join key.

**Impact:** Cannot correlate an HTTP request across a load balancer log (Splunk),
an app trace (Honeycomb), and an Azure function log without manual key selection.

**Fix:** Add `_REQUEST_ALIASES = ("request_id", "requestid", "correlationid",
"correlation_id", "x-request-id", "x_request_id")` and emit canonical
`request_id`.

### G3. `trace.id` (dotted ECS key) not in `_TRACE_ALIASES`

Elasticsearch ECS records land `trace.id` as a label key (with a literal dot).
The current `_TRACE_ALIASES` list does not include `"trace.id"`, so ES trace
records do not get a canonical `trace_id` join key.

**Fix:** Add `"trace.id"` to `_TRACE_ALIASES`.

### G4. `service.name` (dotted ECS / OTel key) not in `_SERVICE_ALIASES`

SigNoz emits `"service.name"` as its label key. Elasticsearch ECS also uses
`service.name`. Neither is currently aliased.

**Fix:** Add `"service.name"` to `_SERVICE_ALIASES`.

### G5. Datadog `tags` list not decomposed

Datadog metrics and logs emit `tags` as a `list[str]` of `"key:value"` strings
(e.g. `["env:prod", "host:web-01", "service:checkout"]`). The normalization
layer's `_coerce_label_map` treats list values as opaque and never splits them,
so Datadog records produce no `host` or `service` join key.

**Impact:** Datadog data cannot be correlated with any other backend.

**Fix:** In `_coerce_label_map`, detect a `tags` key whose value is a list,
iterate the list, split each element on `:` (first colon only), and inject the
resulting key/value pairs into the label map. Handle duplicate keys by
last-writer-wins (same policy as the rest of the map).

### G6. `machine` (Windows Event Log) not in `_HOST_ALIASES`

`windows_event_log.py` emits `machine` as the label key for the machine name.
`_HOST_ALIASES` does not include `"machine"`.

**Fix:** Add `"machine"` to `_HOST_ALIASES`.

### G7. No auth-family awareness in the alias tables

Some vendors use family-specific label conventions that collide across families.
For example, Prometheus `job` means service-name, but Splunk does not emit `job`.
The current alias tables apply globally with no per-family precedence. In
practice this is safe today because aliases are tried in order and the first
non-empty match wins — but it means a record with both `app` (Loki) and `job`
(unrelated Prometheus label) will prefer `app`. Document this in the module
docstring as a known limitation; no code change needed yet.

### G8. No `span_id` surfacing from Elasticsearch

`elasticsearch.py` digs `span.id` from `_source` to determine the record `kind`
but does NOT add it to `labels`. It must be added to `labels` under the key
`span.id` (matching the ECS name, which gap G1's fix will then alias).

---

## 5. Module placement and API

The normalization layer lives at:

```text
src/general_ludd/connectors/normalize.py      # already exists — extend in place
src/general_ludd/connectors/__init__.py        # re-export normalize_join_keys, correlate
src/general_ludd/observe/facade.py             # GluddObserve — no changes needed
```

No new module is required. All gap fixes are additive changes to `normalize.py`
and a one-line fix in `elasticsearch.py`.

### Module dependency graph

```text
connectors/base.py          (NormalizedRecord, Source protocol)
      ↑
connectors/normalize.py     (normalize_join_keys, correlate, auth_family)
      ↑
connectors/registry.py      (ConnectorRegistry — wires ~50 connectors)
      ↑
observe/facade.py           (GluddObserve — composes registry + normalize)
      ↑
roles / operator code       (drives GluddObserve for incident debugging)
```

`normalize.py` is intentionally pure and imports nothing from siblings — this
constraint must be preserved.

### Public API surface (complete, after gap fixes)

```python
# normalize.py
normalize_join_keys(record: dict[str, Any]) -> dict[str, Any]
    # Returns record + {"join": {trace_id?, span_id?, request_id?, host?,
    #   service?, k8s?: {namespace?, pod?, container?},
    #   cloud?: {account?, project?, subscription?, region?}, severity?}}

correlate(records: list[dict], by: str) -> dict[str, list[dict]]
    # Groups by one join key; normalizes on the fly; drops records missing key.

auth_family(name: str) -> str
    # Classifies connector name into 11 family slugs.

bundle_credentials(configs: list[dict]) -> dict[str, list[str]]
    # Collects *_env var names per family; never reads secret values.

AUTH_FAMILY_PREFIXES: dict[str, tuple[str, ...]]
CANONICAL_SEVERITIES: tuple[str, ...]
```

### How an observe-role uses the layer

A typical cross-source incident investigation from an observe role:

```python
from general_ludd.connectors.registry import ConnectorRegistry
from general_ludd.observe.facade import GluddObserve

# 1. Build registry from operator config
registry = ConnectorRegistry.from_config(operator_configs)

# 2. Build the facade (registry satisfies by_kind / list_sources protocol)
observe = GluddObserve(registry)

# 3. Correlate an incident across all connector kinds using trace_id
seed = {
    "ts": 1718200000.0,
    "source": "pagerduty-prod",
    "kind": "incidents",
    "level_or_status": "error",
    "message": "High error rate on checkout service",
    "labels": {"trace_id": "abc123def456", "service": "checkout"},
    "value": None,
    "raw": {...},
}

groups = observe.correlate_incident(
    seed,
    kinds=["logs", "traces", "metrics", "events", "incidents"],
    by="trace_id",      # or "host", "service", "k8s", "request_id"
    window_s=300.0,     # ±5 minutes around seed timestamp
    spec={"env": "prod"},
)
# groups = {"abc123def456": [seed, loki_log, tempo_span, prometheus_metric, ...]}

# 4. Optionally build a topology map to see what else ran on the same host
topo = observe.topology(
    kinds=["logs", "metrics"],
    spec={"env": "prod"},
)
# topo = {"services": {"checkout": {"web-01", "web-02"}},
#          "hosts": {"web-01": {"checkout", "frontend"}}}

# 5. Access per-source errors without aborting
for err in observe.errors:
    print(f"Source {err['source']} failed: {err['message']}")
```

Per-family auth bundling (for an operator rotating credentials):

```python
from general_ludd.connectors.normalize import bundle_credentials

cred_map = bundle_credentials(operator_configs)
# {"aws": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
#  "datadog": ["DATADOG_API_KEY", "DATADOG_APP_KEY"],
#  "grafana": ["GRAFANA_TOKEN"], ...}
# No secret values — only the env-var names.
```

---

## 6. Per-connector/per-family mapping tables

These tables document what label keys each connector emits and which canonical
join key each maps to after normalization. Used by a follow-up implementer to
write fixture-driven unit tests.

### Tracing connectors

| Connector | Label key | Canonical join key | Notes |
|---|---|---|---|
| `tempo.py` | `trace_id` | `join.trace_id` | Explicit in both summary + span mode |
| `tempo.py` | `span_id` | `join.span_id` | After G1 fix |
| `tempo.py` | `service` | `join.service` | From `service.name` resource attr |
| `zipkin.py` | `trace_id` | `join.trace_id` | |
| `zipkin.py` | `span_id` | `join.span_id` | After G1 fix |
| `zipkin.py` | `service` | `join.service` | From `localEndpoint.serviceName` |
| `signoz.py` | `trace_id` | `join.trace_id` | Via alias |
| `signoz.py` | `span_id` | `join.span_id` | After G1 fix; key is `span_id` |
| `signoz.py` | `service.name` | `join.service` | After G4 fix |
| `elasticsearch.py` | `trace.id` | `join.trace_id` | After G3 fix (dotted key) |
| `elasticsearch.py` | `span.id` | `join.span_id` | After G1+G8 fix |
| `elasticsearch.py` | `service.name` | `join.service` | After G4 fix |
| `honeycomb.py` | `trace.trace_id` (dynamic) | `join.trace_id` | Only if NRQL/dataset projects it |

### Logging connectors

| Connector | Label key | Canonical join key | Notes |
|---|---|---|---|
| `grafana_loki.py` | `host` | `join.host` | Stream label, if present |
| `grafana_loki.py` | `pod` / `pod_name` | `join.k8s.pod` | Stream label, if present |
| `grafana_loki.py` | `namespace` | `join.k8s.namespace` | Stream label |
| `grafana_loki.py` | `container` / `container_name` | `join.k8s.container` | |
| `grafana_loki.py` | `app` | `join.service` | Via `_SERVICE_ALIASES` |
| `datadog.py` (logs) | `host` | `join.host` | Direct key |
| `datadog.py` (logs) | `service` | `join.service` | Direct key |
| `datadog.py` (metrics) | `tags` list | `join.host`, `join.service`, etc. | After G5 fix — split `"key:value"` |
| `splunk.py` | `host` | `join.host` | Direct key |
| `azure_monitor.py` | `Computer` | `join.host` | Via `computer` alias (already in `_HOST_ALIASES`) |
| `azure_monitor.py` | `CorrelationId` | `join.request_id` | After G2 fix |
| `azure_monitor.py` | `SubscriptionId` | `join.cloud.subscription` | Via `subscription_id` alias |
| `windows_event_log.py` | `machine` | `join.host` | After G6 fix |
| `docker_engine.py` | `container_id` | (no canonical key — deliberate) | ID not a join key; use `container_name` |
| `docker_engine.py` | `container_name` | `join.service` | Via `_SERVICE_ALIASES` |

### Metrics connectors

| Connector | Label key | Canonical join key | Notes |
|---|---|---|---|
| `prometheus.py` | `instance` | `join.host` | Via alias |
| `prometheus.py` | `job` | `join.service` | Via alias |
| `prometheus.py` | `namespace` | `join.k8s.namespace` | Kubernetes Prometheus |
| `prometheus.py` | `pod` | `join.k8s.pod` | Kubernetes Prometheus |
| `prometheus.py` | `container` | `join.k8s.container` | Kubernetes Prometheus |
| `newrelic.py` | Any NRQL-projected string column | dynamic | `trace.id` → `join.trace_id` after G3; `host.name` → `join.host` if projected |
| `appdynamics.py` | `metricPath` / `metricName` | (no join key) | Metric metadata only; no entity IDs |
| `dynatrace.py` | `dimensionMap.*` (dynamic) | (no join key unless dims include host) | Dimension map keys are metric-specific |

### Event / incident connectors

| Connector | Label key | Canonical join key | Notes |
|---|---|---|---|
| `pagerduty.py` | `service.summary` | (no join key) | PD service name, not a corr. key |
| `okta.py` | `client_ip` | (no join key — IP ≠ `host`) | Okta events not correlatable by host |

---

## 7. Implementation steps

These are concrete, ordered tasks for a follow-up implementer. Each step is
independently committable and testable.

### Step 1 — Fix `span_id` aliasing (gap G1)

File: `src/general_ludd/connectors/normalize.py`

Add after the `_TRACE_ALIASES` line:
```python
_SPAN_ALIASES = ("span_id", "spanid", "span.id", "x-b3-spanid", "x_b3_spanid")
```

In `_derive_join`, after the `trace_id` block:
```python
span_id = _as_str(_first_present(label_map, _SPAN_ALIASES))
if span_id is not None:
    join["span_id"] = span_id
```

Add `"span_id"` to `__all__`-adjacent documentation. Idempotency and total
properties are preserved (same pattern as `trace_id`).

### Step 2 — Fix `request_id` aliasing (gap G2)

File: `src/general_ludd/connectors/normalize.py`

Add:
```python
_REQUEST_ALIASES = (
    "request_id", "requestid", "correlationid", "correlation_id",
    "x-request-id", "x_request_id", "x-correlation-id",
)
```

Wire into `_derive_join` the same way as `trace_id`. This covers Azure Monitor
`CorrelationId` (which `_coerce_label_map` lower-cases to `correlationid`).

### Step 3 — Fix dotted ECS keys (gaps G3 + G4)

File: `src/general_ludd/connectors/normalize.py`

Add `"trace.id"` to `_TRACE_ALIASES` and `"service.name"` to `_SERVICE_ALIASES`.
The label map is already lower-cased so these match ES/OTel dotted keys directly.

### Step 4 — Fix Datadog `tags` list decomposition (gap G5)

File: `src/general_ludd/connectors/normalize.py`

In `_coerce_label_map`, after the main loop, add:
```python
# Decompose Datadog-style "key:value" tag lists into the label map.
tags = out.get("tags")
if isinstance(tags, list):
    for tag in tags:
        try:
            tag_str = str(tag).strip()
        except Exception:
            continue
        if ":" in tag_str:
            k, _, v = tag_str.partition(":")
            k = k.strip().lower()
            if k and k not in out:   # do not overwrite a direct label key
                out[k] = v.strip()
```

### Step 5 — Fix `machine` and `span.id` aliases (gaps G6 + G8)

- Add `"machine"` to `_HOST_ALIASES` in `normalize.py`.
- In `elasticsearch.py`, when `span_id` is extracted from `_source`, add it to
  the record's `labels` dict under the key `"span.id"` so the alias in G1+G3
  can pick it up.

### Step 6 — Expose `span_id` and `request_id` in `__all__` and docstring

Update the module-level docstring of `normalize.py` to list the new join keys.
Update `connectors/__init__.py` to re-export `normalize_join_keys` and `correlate`
(they are already accessible via `connectors.normalize`; decide if top-level
re-export is wanted).

### Step 7 — Wire auth-family into GluddObserve topology (optional enhancement)

`GluddObserve.topology()` currently returns service↔host adjacency. Extend it
(or add `topology_by_family()`) to group by `auth_family(source)` so operators
can see which auth bundle covers which topology nodes. No normalization change
needed — call `auth_family(rec["source"])` inside the topology loop.

---

## 8. Test plan

All tests live under `tests/unit/` (fast, no network) and `tests/integration/`
(requires connector credentials in env vars, opt-in).

### Unit tests — `tests/unit/test_normalize_join_keys.py`

| Test | What it asserts |
|---|---|
| `test_trace_id_aliases` | `trace_id`, `traceid`, `x-b3-traceid`, `trace.id` (after G3) each produce `join.trace_id` |
| `test_span_id_aliases` | `span_id`, `span.id`, `x-b3-spanid` each produce `join.span_id` (after G1) |
| `test_request_id_aliases` | `request_id`, `correlationid`, `x-request-id` each produce `join.request_id` (after G2) |
| `test_host_aliases` | `host`, `hostname`, `instance`, `computer`, `node`, `machine` (after G6) each produce `join.host` |
| `test_service_aliases` | `service`, `app`, `job`, `unit`, `container_name`, `service.name` (after G4) each produce `join.service` |
| `test_host_port_strip` | `"web-01:8080"` → `"web-01"`, `"[::1]:9090"` → `"[::1]"`, bare IPv6 preserved |
| `test_k8s_sub_dict` | `namespace`, `pod`, `container` land in `join.k8s` |
| `test_cloud_sub_dict` | `account_id`, `project_id`, `subscription_id`, `region` land in `join.cloud` |
| `test_severity_mapping` | syslog integers, common spellings all fold to 5 levels |
| `test_datadog_tags_list` | `{"tags": ["host:web-01","service:checkout","env:prod"]}` → `join.host="web-01"`, `join.service="checkout"` (after G5) |
| `test_idempotent` | `normalize_join_keys(normalize_join_keys(rec)) == normalize_join_keys(rec)` |
| `test_total_never_raises` | None, `""`, `{"labels": None}`, `{"labels": "bad"}`, non-dict record all return a dict |
| `test_correlate_by_trace_id` | Mixed-vendor records with same `trace_id` end up in the same group |
| `test_correlate_drops_missing` | Records with no matching join key are dropped from result |
| `test_correlate_k8s_group_key` | k8s sub-dict renders as `"container=...,namespace=...,pod=..."` |

### Unit tests — `tests/unit/test_auth_family.py`

| Test | What it asserts |
|---|---|
| `test_aws_variants` | `"aws_cloudwatch"`, `"cloudwatch"`, `"xray"`, `"prod-cloudwatch"` → `"aws"` |
| `test_azure_variants` | `"azure_monitor"`, `"loganalytics"`, `"appinsights"` → `"azure"` |
| `test_gcp_variants` | `"gcp_logging"`, `"stackdriver"`, `"gke"` → `"gcp"` |
| `test_grafana_bundle` | `"loki"`, `"tempo"`, `"mimir"`, `"prometheus"` → `"grafana"` |
| `test_unknown` | Empty string, `None`, unrecognized name → `"unknown"` |
| `test_bundle_credentials_secret_safe` | Result contains only env-var name strings, never values; `os.environ` is NOT read |

### Unit tests — `tests/unit/test_connector_label_fixtures.py`

Parametrized fixture tests: for each connector, construct a synthetic
`NormalizedRecord` matching the label keys that connector actually emits (per the
inventory in section 6), run `normalize_join_keys`, and assert the expected
canonical join keys are present. One fixture per connector. This test catches any
regression if a connector changes its label key names.

Fixture cases to cover at minimum:
- `tempo` summary mode, `tempo` span mode
- `zipkin` span
- `signoz` trace
- `elasticsearch` ECS trace record
- `grafana_loki` kubernetes-annotated stream
- `datadog` logs record, `datadog` metrics record (with tags list)
- `prometheus` kubernetes-labeled series
- `azure_monitor` KQL row with `CorrelationId` and `Computer`
- `windows_event_log` record with `machine`
- `splunk` record with `host`
- `docker_engine` container logs record

### Integration tests — `tests/integration/test_observe_correlate.py`

Opt-in (skipped unless `INTEGRATION=1`). Exercise `GluddObserve.correlate_incident`
against a live connector pair (e.g., Loki + Tempo if both are configured). Assert:
- At least one group exists keyed by `trace_id`
- Every record in the group has `join.trace_id` matching the group key
- `observe.errors` is inspectable even when some sources fail
- The result is time-ordered within each group

---

## 9. Summary

The normalization layer (`normalize.py`) and the debugging facade (`GluddObserve`
in `observe/facade.py`) are already implemented and functional. The core
canonical vocabulary (`trace_id`, `host`, `service`, `k8s`, `cloud`, `severity`)
is in place. Six concrete gaps (G1–G6, G8) prevent full cross-vendor coverage:
`span_id` aliasing, `request_id`/`CorrelationId`, ECS dotted keys (`trace.id`,
`service.name`), Datadog tag-list decomposition, and `machine` alias. All fixes
are small, additive, and confined to `normalize.py` (plus one label-surfacing
fix in `elasticsearch.py`). No new modules are needed. The test plan covers
idempotency, total-function invariants, per-connector fixture checks, and a
live integration smoke test.
