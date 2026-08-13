# Design: `gludd_observe` façade + 6 cross-source debugging roles (#73)

Status: DESIGN-ONLY (uncommitted). No code/tests written by this doc.
Date: 2026-06-16

This document specifies (1) a `gludd_observe` module/endpoint that composes
debugging workflows over the existing connector façade, and (2) six Ansible
debugging *roles*, each a workflow over that façade. Everything below is grounded
in the connector contract and role/router conventions that exist in the tree
today; where a referenced building block does **not** yet exist it is called out
explicitly as a *new* artifact to build.

---

## 0. Ground truth: what exists today vs. what is aspirational

> **CORRECTION (2026-06-16).** An earlier draft of this section claimed only two
> connectors exist (`datadog`, `prometheus`) and that `connectors/normalize.py`
> does not exist. That was read from a **stale git worktree**
> (`.claude/worktrees/agent-a338c97607e0da93c`) that predates the connector
> build-out. The **canonical tree** (`/Users/shawnwilson/gludd/`,
> `src/general_ludd/connectors/`) actually contains **~34 connector modules** and
> a real **`connectors/normalize.py`** with `normalize_join_keys` / `correlate` /
> an auth-family layer, plus `connectors/ingest_formats.py` push-side parsers. The
> authoritative inventory is `docs/OBSERVABILITY_SOURCES.md`. The corrected facts
> are below; the rest of this design is unchanged in substance because it was
> built on the `base.py` contract / `Source` Protocol / capability-policy / role
> conventions, all of which the canonical tree confirms verbatim. The net effect
> of the correction: **`normalize.py` and most connectors already exist — reuse
> them; do not re-create them.**

Canonical facts (from `docs/OBSERVABILITY_SOURCES.md`, grounded in code):

- **Connector contract** — `src/general_ludd/connectors/base.py`. Defines:
  - `VALID_KINDS = frozenset({"pipeline","logs","metrics","traces"})` plus the
    constants `PIPELINE_KIND`, `LOG_KIND`, `METRIC_KIND`, `TRACE_KIND`.
  - `NormalizedRecord` TypedDict with the 8 keys `ts, source, kind,
    level_or_status, message, value, labels, raw` and the
    `normalized_record(*, source, kind, message="", ts=None,
    level_or_status="info", value=None, labels=None, raw=None)` builder.
  - `Source` Protocol (`name: str`, `KIND: str`, `health() -> dict`,
    `query(spec: dict) -> list[dict]`) + marker subtypes `PipelineSource`,
    `LogSource`, `MetricSource`, `TraceSource`.
  - `SourceRegistry` (`register`, `get(name)`, `by_kind(kind)`, `all()`).
  - `Observability` façade — `find(spec, kinds=None)` (resilient fan-out: a
    failing `query()` becomes an `"error"` record, never an aborted find) and
    `associate(records, by="trace_id", window_s=60.0)` (group by a label value,
    or `by="time_window"` to cluster by ts).
  - `is_safe_endpoint(url)` — literal-host SSRF guard (no DNS), rejects
    non-http(s), loopback, RFC-1918, link-local/`169.254.169.254`, ULA IPv6,
    named metadata hosts.
- **Concrete connectors that exist in the canonical tree (~24 classes across 23
  modules)** — full table in `docs/OBSERVABILITY_SOURCES.md`. By KIND:
  - **`pipeline`**: `GitHubActionsSource`, `JenkinsSource`, `AwsPipelineSource`
    (bridges to CloudWatch logs via `fetch_logs()`).
  - **`logs`**: `ElasticsearchSource` (emits `traces` for APM hits), `SplunkSource`,
    `GraylogSource`, `GrafanaLokiSource`, `AzureMonitorSource`,
    `GcpObservabilitySource` (mode=logs), `DatadogSource` (mode=logs),
    `SentrySource`, `KubernetesSource` (mode=logs|events, REST not kubectl),
    `JsonlLogSource` + `SyslogGrepSource` (`local_files.py`, path-confined),
    `JournaldSource` (argv `journalctl`, `shell=False`).
  - **`metrics`**: `PrometheusSource` (+ Datadog/GCP via mode).
  - **`traces`**: `JaegerSource`, `TempoSource`, `ZipkinSource`, `SigNozSource`.
  - **`infra`**: `AzureResourceGraphSource`.
  - **`incidents`**: `PagerDutySource`, `OpsgenieSource`, `GrafanaOnCallSource`.
  - **Multi-mode cloud**: `GcpObservabilitySource` (`KIND="gcp_observability"`),
    `DatadogSource` (`KIND="logs"`, metric records when `mode="metrics"`).
  - Datadog/Prometheus specifics (verified in my stale worktree, identical
    contract): Datadog `spec={"mode":"logs"|"metrics","query","from","to","limit"}`,
    injectable `http_request(method,url,*,params,json,headers,timeout)`,
    `api_key_env`/`app_key_env`. Prometheus `spec={"promql","start","end","step"|"time"}`,
    injectable `http_get(url,params,headers)`, `token_env`→Bearer.
- **The ticket's "~40 connectors" is accurate** — the canonical tree has **34
  connector modules** (per `docs/privileges/README.md`, read from source):
  tracing/profiling (5), messaging (3, e.g. rabbitmq/nats), metrics_stores (4,
  e.g. clickhouse), host_os (10, e.g. snmp/redfish), databases (6: postgres,
  mysql, redis, mongodb, clickhouse, cassandra), incidents_idp (6: pagerduty,
  opsgenie, grafana_oncall, **okta** [`SSWS` auth], …), plus the logs/metrics/
  traces sources listed above. **`okta` IS present** — so security_signal (2f)
  can use it today. Genuinely-absent security sources to confirm/add are narrower:
  **cloudflare audit, k8s-audit, entra sign-ins, aws-cloudtrail, gitlab CI**.
  Cross-reference the authoritative index in `docs/privileges/README.md` (34-row
  module/class/KIND/credential/endpoint table) before building any "new" connector
  to avoid duplicating one that exists under a different name.
- **`src/general_ludd/connectors/normalize.py` EXISTS** (canonical tree) with
  exactly the symbols the ticket names — `normalize_join_keys(record)` (folds
  vendor label aliases into a canonical `join` sub-dict: `trace_id`, `host`,
  `service`, `k8s`, `cloud`, `severity`), `correlate(records, by)` (groups on a
  canonical join key, normalizing on the fly), `CANONICAL_SEVERITIES`
  (debug/info/warn/error/critical), and an **auth-family layer**
  (`AUTH_FAMILY_PREFIXES`, `auth_family(name)`, `bundle_credentials(configs)` —
  secret-safe, returns env-var *names* only). **This supersedes Section 1.3's
  "extract a new normalize.py" proposal: do NOT create it — import and extend the
  existing one** (see the revised note in 1.3). My `JOIN_KEYS`/`AUTH_FAMILIES`
  sketches below should defer to the canonical `normalize.py` vocabulary.
- **`connectors/ingest_formats.py` EXISTS** — push-side parsers
  (`parse_fluent_forward`, `parse_beats_lumberjack`, `parse_gelf`), fail-soft,
  payload-bounded (`MAX_PAYLOAD_BYTES=8 MiB`, `MAX_EVENTS=100_000`). Not needed by
  the pull-side `gludd_observe` façade, but relevant if a role ever consumes
  pushed telemetry.
- **`model_deploy_check` does NOT exist** (confirmed absent). The
  saturation/capacity role (Section 2e) specifies it as a *new* helper to add —
  this remains accurate.
- **Role conventions** — `collections/ansible_collections/general_ludd/agent/`:
  - One role exists today: `roles/agent_task/` (`tasks/main.yml`,
    `defaults/main.yml`). It demonstrates the binding conventions: a
    `block/rescue/always` lifecycle, `assert` gate on required vars,
    `set_fact` for derived vars, `no_log: "{{ psk | length > 0 }}"`, a
    `capability_role` default that the per-role default-DENY policy keys on,
    and a `enable_*` destructive-op guard defaulting to `false`.
- **Capability policy (authorization spine)** —
  `collections/ansible_collections/general_ludd/agent/plugins/module_utils/capability_policy.py`.
  `CapabilityPolicy` dataclass, default-DENY in every dimension: `db_ops`,
  `network_hosts`, `facts_prefixes`, `secret_prefixes`, `fs_write`/`fs_write_roots`,
  `collections_self_modify`. `for_role(name, config=None)` resolves
  config-override → built-in table → empty baseline. Check methods raise
  `CapabilityError` (fail-closed): `check_db_op`, `check_network_host`
  (exact or subdomain-suffix match), `check_secret_access`, `check_facts_access`,
  `check_fs_write`. `extract_host(url)` returns a bare host for the network check.
- **Daemon-API client used by modules** —
  `.../module_utils/gludd.py`: `GluddClient(base_url, psk, timeout)` (stdlib
  `urllib` only), `get/post/patch`, `Authorization: Bearer <psk>` + `X-PSK`
  headers, `ok_result`/`error_result` helpers, returns include `_status`/`_error`.
- **Router conventions** — `src/general_ludd/routers/__init__.py` →
  `register_all(app, daemon_state)` calls each module's `register(app,
  daemon_state)`. `routers/todos.py` shows the house style: handlers declared
  inside `register()`, Pydantic `BaseModel` request bodies with field
  constraints/`pattern`, `HTTPException` for errors, least-privilege gating via
  `_active_project_ids(app)` returning `None` (unconstrained) vs. a constrained
  set. Worker PSK middleware authenticates on `Authorization: Bearer <psk>`.

Everything in Sections 1–3 either uses these as-is or is flagged as a new
artifact to add.

---

## 1. The `gludd_observe` module + HTTP endpoint

### 1.1 Placement & shape

New module: **`src/general_ludd/observe/__init__.py`** + **`src/general_ludd/observe/facade.py`**
(separate top-level package so it can import the `connectors` contract without a
circular dependency, mirroring how `routers/*` import from elsewhere lazily).

`gludd_observe` is a *thin composition layer* over `connectors.Observability`. It
adds three things the raw façade lacks:

1. **KIND-addressed loading** of connectors from operator config (today an
   operator must hand-build a `SourceRegistry`; `gludd_observe` builds it from a
   declarative source list).
2. **Time-bounded, multi-KIND query orchestration** with a single time window
   propagated to each backend's native spec dialect.
3. **Higher-level correlation primitives** (`correlate_incident`, `timeline`,
   `topology`) built on `Observability.associate()` + a new join-key vocabulary.

```python
# src/general_ludd/observe/facade.py  (NEW)
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from general_ludd.connectors.base import (
    Observability, SourceRegistry, Source, VALID_KINDS, is_safe_endpoint,
)

# A connector constructor registry: KIND-family vendor name -> factory.
# Factories take (config, transport) and return a Source. Only datadog &
# prometheus are wired today; the table grows as connectors land.
ConnectorFactory = Callable[[Mapping[str, Any], Any], Source]

@dataclass(frozen=True)
class TimeWindow:
    """A half-open [start, end) window in epoch seconds."""
    start: float
    end: float
    def clamp(self, max_span_s: float) -> "TimeWindow": ...   # guardrail (1.5)

class ObserveFacade:
    def __init__(self, registry: SourceRegistry) -> None:
        self._obs = Observability(registry)
        self._registry = registry

    # ---- construction -------------------------------------------------
    @classmethod
    def from_config(
        cls,
        sources_config: Sequence[Mapping[str, Any]],
        *,
        factories: Mapping[str, ConnectorFactory],
        transport_for: Callable[[Mapping[str, Any]], Any],
    ) -> "ObserveFacade":
        """Build a registry from declarative source configs.

        Each entry: {"vendor": "datadog", "kind": "logs", "name": "...",
                     "endpoint": "https://...", ...vendor-specific...}.
        Fail-closed: an entry whose endpoint fails is_safe_endpoint() is
        SKIPPED with a recorded warning (never registered) so a misconfigured
        SSRF-prone source cannot enter the registry. Unknown vendors are
        skipped (no factory). Returns a facade over whatever validated.
        """
```

### 1.2 Façade function signatures (the public API)

```python
    def query_sources(
        self,
        *,
        window: TimeWindow,
        kinds: Sequence[str] | None = None,     # subset of VALID_KINDS
        filters: Mapping[str, Any] | None = None,  # e.g. {"service": "...", "query": "..."}
        sources: Sequence[str] | None = None,   # restrict to named sources
        max_records: int = 5000,                # hard cap (1.5)
    ) -> list[dict[str, Any]]:
        """Time-bounded fan-out across matching sources.

        Translates `window`+`filters` into each backend's native spec dialect
        via `_build_spec(kind, vendor, window, filters)` (a per-vendor adapter,
        e.g. Prometheus wants start/end/step; Datadog wants from/to/query),
        then delegates to Observability.find(spec, kinds). Returns ts-sorted
        normalized records, truncated to max_records. Resilient: one failing
        source becomes an error record (inherited from Observability.find)."""

    def correlate_incident(
        self,
        *,
        anchor: Mapping[str, Any],               # an incident/event record (the seed)
        window: TimeWindow,
        join_keys: Sequence[str] = ("trace_id", "request_id", "commit"),
        pull_kinds: Sequence[str] = ("logs", "traces", "metrics"),
        time_fallback_s: float = 60.0,
    ) -> dict[str, Any]:
        """Given a seed record (e.g. a PagerDuty/incident event), pull related
        telemetry in `window` and group it.

        Strategy: extract candidate join values from anchor.labels for each key
        in join_keys (via normalize.extract_join_values). For each present key,
        query_sources(...) then Observability.associate(records, by=key). If NO
        structured key is present, fall back to associate(by='time_window',
        window_s=time_fallback_s) anchored on the incident ts. Returns
        {"anchor": anchor, "groups": [...], "join_key_used": str,
         "record_count": int}."""

    def timeline(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        bucket_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Order records by ts (None-ts last, matching Observability._sort_by_ts)
        into a flat or bucketed timeline. Each entry:
        {"ts": float|None, "source": str, "kind": str,
         "level_or_status": str, "message": str, "value": float|None,
         "labels": {...}}. With bucket_s set, returns per-bucket aggregates
        {"bucket_start": float, "count": int, "error_count": int,
         "by_kind": {...}, "records": [...]}."""

    def topology(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        node_label: str = "service",
        edge_label: str = "trace_id",
    ) -> dict[str, Any]:
        """Derive a service-dependency graph from correlated telemetry.

        Nodes := distinct labels[node_label] values. Edges := pairs of nodes
        that co-occur within the same edge_label (e.g. trace_id) group, ordered
        by ts (caller -> callee inferred from span order in labels when present).
        Returns {"nodes": [{"id","kind","error_count","sample_count"}],
                 "edges": [{"src","dst","weight","p95_latency_ms"|None}]}.
        Pure function over already-fetched records — no I/O, easy to unit test."""
```

`query_sources` is the only method that touches the network; `timeline`,
`topology`, and the grouping half of `correlate_incident` are pure functions over
records, which keeps the bulk of the logic unit-testable with hand-built
`NormalizedRecord` fixtures (the same pattern `base.py` is designed for).

### 1.3 New `connectors/normalize.py` (join-key + auth-family layer)

Extract a small, dependency-free module so the correlation vocabulary is
first-class and testable. It re-exports the record builder and adds explicit
join-key helpers the façade depends on:

```python
# src/general_ludd/connectors/normalize.py  (NEW — thin extraction)
from general_ludd.connectors.base import (
    NormalizedRecord, normalized_record, Observability,
)

# The canonical correlation keys recognised across backends. Connectors should
# populate whichever apply into record["labels"].
JOIN_KEYS: tuple[str, ...] = (
    "trace_id", "span_id", "request_id", "correlation_id",
    "commit", "deploy_id", "release", "incident_id",
    "service", "host", "pod", "namespace", "cluster",
)

def normalize_join_keys(labels: dict) -> dict:
    """Canonicalise vendor-specific label aliases onto JOIN_KEYS.

    e.g. Datadog 'dd.trace_id' -> 'trace_id'; OTel 'traceID' -> 'trace_id';
    k8s 'pod_name' -> 'pod'. Pure dict->dict, idempotent. Non-aliased keys
    pass through untouched so 'raw' label fidelity is preserved alongside."""

def extract_join_values(record: dict, keys) -> dict:
    """Return {key: value} for each key in `keys` present in record.labels
    (after normalize_join_keys). Missing keys omitted."""

def correlate(records, by="trace_id", window_s=60.0) -> list[dict]:
    """Thin alias over Observability.associate, but first runs
    normalize_join_keys on each record's labels so alias drift never breaks a
    join. Returns the same [{"key":..., "records":[...]}] group shape."""

# Auth-family abstraction: connectors share a small set of auth shapes.
AUTH_FAMILIES = {
    "api_app_key": ("api_key_env", "app_key_env"),   # datadog
    "bearer_env":  ("token_env",),                    # prometheus/loki/tempo/grafana
    "oauth_client": ("client_id_env", "client_secret_env", "token_url"),  # okta/entra
    "static_header": ("header_name", "secret_env"),   # cloudflare/pagerduty
    "kubeconfig":  ("kubeconfig_path", "context"),    # k8s events/infra
}
```

`normalize_join_keys`/`correlate` are exactly the symbols the ticket names; this
section is where they live. The façade in 1.2 calls `normalize.correlate` rather
than `Observability.associate` directly, so alias drift across ~40 vendors is
handled in one place.

### 1.4 Connector KIND map (how ~40 backends address the façade)

Connectors keep self-registering under the `Source` Protocol; `gludd_observe`
addresses them by `KIND`. Proposed KIND assignment for the connector families the
ticket enumerates (each is a connector module to add; datadog/prometheus exist):

| KIND        | Vendors (connectors to add; * = exists)                              | Auth family        |
|-------------|----------------------------------------------------------------------|--------------------|
| `logs`      | datadog*, loki, cloudwatch-logs, elasticsearch, splunk, gcp-logging  | api_app_key/bearer |
| `metrics`   | prometheus*, datadog*(mode), cloudwatch-metrics, grafana, victoria   | bearer_env         |
| `traces`    | tempo, jaeger, datadog-apm, otel-collector, zipkin                   | bearer_env         |
| `events`    | k8s-events, github-actions, gitlab-ci, argocd, opsgenie, statuspage  | kubeconfig/bearer  |
| `incidents` | pagerduty, opsgenie, incident.io, servicenow                         | static_header      |
| `pipeline`  | github, gitlab, jenkins, circleci, argocd, spinnaker                 | bearer_env         |
| `infra`     | k8s-api, aws-asg, gcp-mig, terraform-state, node-exporter            | kubeconfig/bearer  |
| `security`  | okta, entra, cloudflare-audit, k8s-audit, vault-audit, aws-cloudtrail| oauth/static       |

**KIND handling — corrected (2026-06-16).** `events`, `incidents`, `infra`,
`security` go beyond the four `VALID_KINDS`. The canonical tree **already
establishes the pattern**: connectors declare **free-form domain `KIND` strings**
outside the frozenset (`infra`, `incidents`, `gcp_observability`) and
`SourceRegistry.by_kind` keys on that string directly, so **no `VALID_KINDS`
edit is required** for the façade to address them. My earlier "extend the
frozenset" recommendation is therefore unnecessary — follow the in-tree
convention: connectors just set `KIND="incidents"` / `"infra"` / `"security"`,
and several already do (PagerDuty/Opsgenie/GrafanaOnCall =`incidents`,
AzureResourceGraph =`infra`). `events` is served today as a per-record `kind`
from `KubernetesSource(mode="events")` rather than a class KIND; the `security`
KIND is **new** (no connector declares it yet — okta/cloudflare/k8s-audit are the
connectors to add). Marker Protocols (`IncidentSource`, etc.) are optional sugar,
not required, since `Source` is duck-typed.

### 1.5 HTTP endpoint(s) under `routers/`

New router: **`src/general_ludd/routers/observe.py`**, registered by adding
`from general_ludd.routers.observe import register as register_observe` +
`register_observe(app, daemon_state)` to `routers/__init__.register_all` (the
exact pattern every existing router follows).

Endpoints (admin-scoped, PSK-gated by the existing daemon middleware that checks
`Authorization: Bearer <psk>`):

```
POST /admin/observe/query
  Request (Pydantic, field-constrained like routers/todos.py):
    { "kinds":   [str]   # subset of valid KINDs; pattern ^(logs|metrics|traces|events|incidents|pipeline|infra|security)$
      "window":  {"start": float, "end": float},   # epoch seconds, end>start
      "filters": {str: <scalar|str>},              # service/query/promql/etc.
      "sources": [str] | null,                     # optional source-name allowlist
      "max_records": int   # 1..5000, default 1000
    }
  Response:
    { "records": [NormalizedRecord...], "truncated": bool, "source_count": int,
      "errors": [{"source": str, "message": str}] }   # error records split out

POST /admin/observe/correlate
  Request:
    { "anchor": {NormalizedRecord-ish},             # the seed event/incident
      "window": {"start": float, "end": float},
      "join_keys": [str] | null,                    # default JOIN_KEYS subset
      "pull_kinds": [str] | null }
  Response:
    { "join_key_used": str, "groups": [{"key": str, "records": [...]}],
      "record_count": int }

POST /admin/observe/timeline
  Request: { "records": [...], "bucket_s": float | null }
  Response: { "timeline": [...] }                    # pure transform, no I/O

POST /admin/observe/topology
  Request: { "records": [...], "node_label": str, "edge_label": str }
  Response: { "nodes": [...], "edges": [...] }        # pure transform, no I/O

GET  /admin/observe/sources
  Response: { "sources": [{"name": str, "kind": str, "health": {...}}] }
  # Calls each registered source.health() (which never raises by contract).
```

**Least-privilege & SSRF posture (mirrors existing patterns):**

- **No raw URLs accepted from the request body.** A client never names a host to
  fetch. It selects from sources *already registered by an operator*, by `kinds`
  and/or `sources` name allowlist. The only host-bearing config — connector
  endpoints — is validated at registry-build time, so an SSRF-prone target can
  never be registered, let alone queried.
- **SSRF authority is the connector's own construction-time guard, NOT
  `is_safe_endpoint` (security finding F1).** The connector audit
  (`docs/audit/connector_security_audit.md`, F1) found that `base.is_safe_endpoint`
  is exported but **unused**, and is actually *weaker* than the per-connector
  guards (no single-label-host block, thinner metadata list). Each connector
  already validates its own `base_url`/`site`/`api_server` at construction
  (datadog `_validate_site`, prometheus `_validate_base_url`, plus the
  `allow_private` opt-in family). Therefore `ObserveFacade.from_config` must rely
  on **each connector's constructor raising** on a bad endpoint (catch and skip
  that source), and may use `is_safe_endpoint` only as a coarse *pre*-filter — it
  must never be the sole or final gate, and must never *relax* what a connector
  enforces. (Ideally F1 is fixed first so the shared guard matches the strongest
  per-connector guard; until then, defer to the connector.)
- **Scrub `raw` from error records before returning them over HTTP (security
  finding F3).** The façade's resilience path puts the live exception in
  `raw` and `f"query failed: {exc}"` in `message` (`base.py:225-227`), which can
  carry a token/URL embedded in a transport exception (audit F3). The
  `/admin/observe/*` responses MUST drop `raw` for `level_or_status=="error"`
  records and return a redacted `message` (strip URLs/credentials) in the
  `errors` array — never echo `raw=exc` to an API client or the UI.
- **Window guardrail:** `TimeWindow.clamp(max_span_s)` caps the query span
  (default e.g. 24h) so a request cannot fan a multi-day query across 34 backends
  (DoS / cost guard). `max_records` caps result size. Both enforced server-side,
  ignoring client-supplied larger values.
- **PSK auth:** these are admin-scoped paths; the worker/daemon middleware already
  requires `Authorization: Bearer <psk>` on non-public paths (see
  `worker/app.py` and `module_utils/gludd.py` header construction). No new auth
  code — the path prefix inherits it.
- **Fail-closed:** unknown KIND → 422 (Pydantic pattern). `end <= start` → 422.
  Empty registry → 200 with `records: []` and `source_count: 0` (not an error).

**Reconciliation with `docs/design/connector_wiring_plan.md` (read it first).** A
parallel design specs the *plumbing layer*: `connectors/loader.py` (builds the
`SourceRegistry` at daemon startup, signature-tolerant via `inspect.signature`
over divergent transport kwargs), `connectors/transport.py`, a `connectors:`
`UserConfig` field, a `_connectors_facet` on `/api/facts`, and an `/api/observe`
router (sources/health/query). **That is the layer this design's `ObserveFacade`
should sit on**, not duplicate. Concretely: (a) use the wiring plan's `loader.py`
as the implementation of `ObserveFacade.from_config` (the façade wraps the
registry the loader builds); (b) **align the router**: prefer the wiring plan's
`/api/observe` base path + its registration mechanism, and add this design's
higher-level `correlate`/`timeline`/`topology` operations onto that same router
rather than a separate `/admin/observe/*` one — pick ONE path prefix at build
time and apply the F1/F3 hardening above to it. The two docs are complementary:
*this* doc owns the workflow/correlation/roles layer; the wiring plan owns the
load/transport/registration plumbing.

---

## 2. The 6 debugging ROLES

All six are Ansible roles under
`collections/ansible_collections/general_ludd/agent/roles/<role>/`, each with
`tasks/main.yml` + `defaults/main.yml`, following the `agent_task` conventions
(assert gate → block/rescue/always → `no_log` on PSK → `capability_role` default
→ artifact JSON out). Each role:

- talks to the daemon via the existing `GluddClient` pattern (a **new**
  `gludd_observe` Ansible module wrapping the `/admin/observe/*` endpoints — see
  Section 3),
- declares a `capability_role` that the default-DENY `capability_policy.py` keys
  on (new entries to add — Section 3),
- writes a result artifact `{{ artifact_dir }}/<role>_result.json`,
- never mutates anything by default (read-only analysis); any write-back
  (e.g. annotating an incident, creating a follow-up todo) is behind an
  `enable_*` guard defaulting to `false`, exactly like `agent_task`'s
  `enable_git_push`.

For each role: **inputs/vars · connector KINDs pulled · correlation/join strategy
· outputs · decision logic.**

### 2a. `incident_triage` — incident → correlated logs/traces/metrics window

- **Inputs/vars:** `incident_id` (required), `service` (optional hint),
  `window_before_s: 900`, `window_after_s: 300`, `daemon_url`, `psk`,
  `capability_role: "incident_triage"`, `artifact_dir`.
- **Connector KINDs pulled:** `incidents` (fetch the seed) →
  `logs` + `traces` + `metrics`.
- **Join strategy:** fetch the incident via `/admin/observe/query`
  (`kinds=["incidents"]`, filter on `incident_id`). Use the incident's `ts` to
  build `TimeWindow(ts - window_before_s, ts + window_after_s)`. Call
  `/admin/observe/correlate` with that anchor and
  `join_keys=["trace_id","request_id","service","incident_id"]`,
  `pull_kinds=["logs","traces","metrics"]`. The façade picks the first present
  structured key; falls back to `time_window` if none.
- **Outputs:** `incident_triage_result.json` =
  `{incident_id, join_key_used, window, groups:[...], top_errors:[...],
  affected_services:[...], timeline:[...]}` (timeline via `/observe/timeline`).
- **Decision logic:** rank groups by error density (`level_or_status=="error"`
  count). Flag the service with the highest error rate as the *suspected origin*.
  If a single `trace_id` group spans ≥3 services and contains the first error,
  emit `probable_root_service` = the service of the earliest error span. If no
  structured correlation key was available (time_window fallback), mark
  `confidence: "low"` in the artifact.

### 2b. `latency_regression` — traces + metrics deltas

- **Inputs/vars:** `service` (required), `route` (optional),
  `baseline_window` `{start,end}` and `candidate_window` `{start,end}` (required;
  e.g. last-week vs. now), `percentile: 95`, `regression_threshold_pct: 20`,
  `capability_role: "latency_regression"`.
- **Connector KINDs pulled:** `traces` (span durations) + `metrics`
  (latency histograms / RED metrics).
- **Join strategy:** two `query_sources` calls — one per window — each
  `kinds=["traces","metrics"]`, filtered on `service`/`route`. Group each
  window's traces by `service` (and `route` label). Compute pXX latency per
  service from `value` (metrics) and span duration (traces, from `labels` or
  `raw`). Join the two windows **by service+route key** (not by trace_id —
  cross-window correlation is by topology node, not by request).
- **Outputs:** `latency_regression_result.json` =
  `{service, percentile, deltas:[{service, route, baseline_p95, candidate_p95,
  delta_pct, regressed: bool}], worst_offender:{...}}`.
- **Decision logic:** a node is `regressed` when
  `(candidate_pXX - baseline_pXX)/baseline_pXX*100 >= regression_threshold_pct`.
  Use `topology(records, edge_label="trace_id")` on the candidate window to
  attribute the regression to a downstream edge: if a regressed node's p95
  increase is dominated by one outgoing edge's p95, name that edge's callee as
  the `likely_cause`. Emit `verdict: "regression"|"noise"|"improvement"`.

### 2c. `error_spike_root_cause` — logs + events + deploys

- **Inputs/vars:** `service` (optional; default all), `window` `{start,end}`
  (required), `baseline_window` `{start,end}` (optional, for spike detection),
  `error_query` (optional log filter), `capability_role:
  "error_spike_root_cause"`.
- **Connector KINDs pulled:** `logs` (error lines) + `events` (k8s events,
  config changes) + `pipeline` (deploy/CD events).
- **Join strategy:** `query_sources(kinds=["logs","events","pipeline"], window)`.
  Build a bucketed `timeline(bucket_s=60)`; detect the spike bucket (error count
  ≥ N× the baseline mean). Within ±2 buckets of the spike onset, correlate by
  `commit`/`deploy_id`/`release` (`correlate` over the JOIN_KEYS deploy family)
  to tie error lines to the deploy/event that immediately preceded them.
- **Outputs:** `error_spike_root_cause_result.json` =
  `{spike_detected: bool, spike_onset_ts, error_rate_multiplier,
  candidate_changes:[{kind:"pipeline"|"events", deploy_id|commit, ts,
  lead_time_to_spike_s}], top_error_signatures:[...]}`.
- **Decision logic:** the candidate change with the smallest non-negative
  `lead_time_to_spike_s` (change ts just before spike onset) is ranked
  `most_likely_cause`. Cluster error messages into signatures (normalize digits/
  UUIDs out of `message`) and report the top signature that *appeared* at the
  spike (absent in baseline) as the `new_error_signature`.

### 2d. `deploy_correlator` — CI/CD events ↔ error/latency change

- **Inputs/vars:** `service` (required), `lookback_s: 86400`,
  `post_deploy_window_s: 1800`, `error_delta_threshold_pct: 50`,
  `latency_delta_threshold_pct: 20`, `capability_role: "deploy_correlator"`.
- **Connector KINDs pulled:** `pipeline` (deploys) + `logs` (error rate) +
  `metrics`/`traces` (latency).
- **Join strategy:** fetch all deploys in `lookback_s` (`kinds=["pipeline"]`).
  For each deploy event ts, compute error-rate and pXX-latency in
  `[ts - post_deploy_window_s, ts)` (pre) vs `[ts, ts + post_deploy_window_s)`
  (post) via two scoped `query_sources` calls. Correlation is *temporal per
  deploy* (each deploy is its own anchor); within a deploy, error/latency
  records are tied to the deploy by service + time window.
- **Outputs:** `deploy_correlator_result.json` =
  `{deploys:[{deploy_id, commit, ts, error_delta_pct, latency_delta_pct,
  status:"regressed"|"improved"|"neutral"}], regressing_deploys:[...]}`.
- **Decision logic:** a deploy is `regressed` if `error_delta_pct >=
  error_delta_threshold_pct` OR `latency_delta_pct >=
  latency_delta_threshold_pct`. Emit a ranked list; the top regressing deploy is
  the `rollback_candidate`. If `enable_writeback` (default `false`), file a
  follow-up todo via `gludd_db op=todo_create` (requires the role's
  `db_ops:["todo_create"]` grant).

### 2e. `saturation_capacity` — infra + metrics + model-deploy misconfig

- **Inputs/vars:** `target` (cluster/namespace/deployment, required),
  `window` `{start,end}`, `cpu_threshold_pct: 85`, `mem_threshold_pct: 85`,
  `model_deploy_ref` (optional; names a model deployment to sanity-check),
  `capability_role: "saturation_capacity"`.
- **Connector KINDs pulled:** `infra` (k8s requests/limits, ASG/MIG sizing) +
  `metrics` (CPU/mem/queue-depth utilization) + `events` (OOMKilled, evictions,
  HPA scaling events).
- **Join strategy:** `query_sources(kinds=["infra","metrics","events"], window)`
  filtered on `target`. Join `infra` (capacity: requests/limits) with `metrics`
  (actual utilization) **by `pod`/`namespace`/`cluster`** node labels to compute
  utilization ratios. Cross-reference `events` for OOM/eviction within the window
  on the same node labels.
- **`model_deploy_check` (NEW helper to add):** a pure function
  `model_deploy_check(deploy_spec: dict, observed: dict) -> list[Finding]` that
  flags model-deployment misconfig — e.g. requested GPU/mem below the model's
  known footprint, replica count of 1 for a saturated service, batch/concurrency
  settings inconsistent with observed queue depth. It reads the model deployment
  spec (via the existing `/api/deployments` endpoint in `routers/compute.py`,
  which returns `{deployments: [{instance_id, provider, model_name, state, ...}]}`;
  extend its payload or add a detail endpoint to expose resource requests) and
  compares against observed `metrics`. Lives in `src/general_ludd/observe/model_deploy_check.py`.
- **Outputs:** `saturation_capacity_result.json` =
  `{saturated_nodes:[{node, cpu_pct, mem_pct, oom_events:int}],
  capacity_findings:[...], model_deploy_findings:[Finding...],
  recommendation: str}`.
- **Decision logic:** a node is `saturated` if cpu_pct ≥ threshold OR mem_pct ≥
  threshold OR ≥1 OOM/eviction event. If `model_deploy_ref` is set and
  `model_deploy_check` returns findings, prioritize them (misconfig is a root
  cause, raw saturation is a symptom). Recommendation = scale-up vs. fix-request-
  limits vs. fix-model-deploy, chosen by which finding class dominates.

### 2f. `security_signal` — auth/audit events (okta/cloudflare/entra + k8s events)

- **Inputs/vars:** `principal` (user/service-account, optional),
  `window` `{start,end}` (required), `geo_velocity_check: true`,
  `failed_auth_threshold: 10`, `capability_role: "security_signal"`.
- **Connector KINDs pulled:** `security` (okta system log, entra sign-ins,
  cloudflare audit, k8s-audit, cloudtrail) + `events` (k8s events:
  RBAC denials, secret access).
- **Join strategy:** `query_sources(kinds=["security","events"], window)`
  filtered on `principal` when given. Correlate by `principal`/`user`/
  `correlation_id` (`correlate(by="...")` over normalized labels). Build a
  per-principal `timeline` to detect bursts (failed-auth runs) and impossible
  travel (geo on consecutive sign-ins from `labels` ip/geo).
- **Outputs:** `security_signal_result.json` =
  `{principals:[{principal, failed_auth_count, sources_seen:[...],
  impossible_travel: bool, suspicious: bool}], top_signals:[...]}`.
- **Decision logic:** a principal is `suspicious` if `failed_auth_count >=
  failed_auth_threshold`, OR `impossible_travel` (two successful auths from
  geo-distant IPs within physically implausible time), OR a privilege-escalation
  k8s-audit event correlates (same `correlation_id`) with a fresh sign-in. This
  role reads `security` connectors but, per capability policy, gets **no** db/fs
  write and only `secret_prefixes:["audit/"]` if it must resolve an audit token —
  mirroring the existing `security_auditor` grant.

---

## 3. Wiring plan

### 3.1 How roles call `gludd_observe`

Two layers, matching the existing module → daemon-API split:

1. **Implemented Ansible module `gludd_observe`**
   (`collections/.../plugins/modules/gludd_observe.py`) — a thin wrapper, built
   exactly like `gludd_db.py`:
   - `argument_spec`: `op` (choices: `query_sources`, `correlate_incident`,
     `timeline`, `topology`), plus op-specific params (`kinds`, `window`,
     `spec`, `seed`, `start`, `end`, …), `daemon_url`, `psk` (`no_log=True`),
     `timeout`, `role`.
   - First does the **capability gate** before any network call, copying the
     `gludd_db.py` idiom verbatim:
     ```python
     cap = for_role(module.params["role"])
     try:
         cap.check_network_host(extract_host(module.params["daemon_url"]))
     except CapabilityError as exc:
         module.fail_json(**error_result(f"observe denied by policy: {exc}", role=...))
         return
     ```
   - Uses `GluddClient(base_url, psk, timeout)` to discover named sources at
     `/api/observe/sources` and query them at `/api/observe/query`, then delegates
     merging/correlation/topology to the existing `GluddObserve` facade.
2. **Roles call the module** in their `block:` with
   `role: "{{ capability_role }}"` and `no_log: "{{ psk | length > 0 }}"`, then
   `set_fact` on the registered result and `ansible.builtin.copy` the artifact —
   identical to `agent_task`'s shape.

### 3.2 Capability-policy grants to add

Six entries now exist in `_builtin_table()` in
`module_utils/capability_policy.py`. These read-only analysis roles need only
**network to the local daemon**; they receive no db, filesystem, or secret
grant, honoring default-DENY. The direct-module test identity has the same
local-only network grant.

```python
for role in (
    "observe_incident_triage",
    "observe_latency_regression",
    "observe_error_spike_rca",
    "observe_deploy_correlator",
    "observe_saturation_capacity",
    "observe_security_signal",
    "molecule_observe_probe",
):
    table[role] = CapabilityPolicy(
        role=role,
        network_hosts=["localhost", "127.0.0.1"],
    )
```

No observe identity receives database, filesystem, or secret access. If a future
role adds write-back, its narrowly scoped grant and a denial-first wiring test
must land together; the beta.3 module is strictly read-only.

### 3.3 Daemon-side connector registry wiring

- The daemon builds the `SourceRegistry` once at startup from operator config
  (e.g. a new `config/observe_sources.yml`) via
  `ObserveFacade.from_config(sources_config, factories=..., transport_for=...)`,
  and stashes the façade on `app.state._observe_facade` (same pattern as
  `app.state._project_manager`, `app.state._session_factory` in `routers/todos.py`).
- `routers/observe.py` handlers pull `getattr(app.state, "_observe_facade", None)`;
  if `None`, return an empty/`not_configured` response (fail-soft, no 500),
  consistent with how `todos.py` degrades when `_session_factory is None`.
- The `factories` map (vendor → constructor) is where datadog/prometheus plug in
  today and where the other ~38 connectors register as they are implemented. The
  injectable-transport convention (`http_request`/`http_get`) is preserved, so
  the daemon supplies one real transport and tests supply mocks.

### 3.4 Test strategy (describe — do NOT write here)

**Unit (pytest, `tests/unit/`):**
- `test_observe_facade.py` — `query_sources`/`correlate_incident`/`timeline`/
  `topology` over hand-built `NormalizedRecord` lists and a fake `SourceRegistry`
  of stub `Source`s (the contract is built for this). Assert: ts-ordering matches
  `Observability._sort_by_ts`; `max_records` truncation; a stub source that
  raises becomes an error record (resilience); `time_window` fallback fires when
  no structured join key present.
- `test_normalize_join_keys.py` — alias canonicalization (`dd.trace_id`,
  `traceID`, `pod_name`) → JOIN_KEYS; `correlate` groups across alias drift;
  idempotency.
- `test_observe_from_config_ssrf.py` — `from_config`/loader SKIPS any source
  whose **connector constructor raises** on a bad endpoint (loopback, RFC-1918,
  metadata IP) — i.e. it relies on each connector's own `_validate_*` (the
  authoritative, stronger guard per finding F1), not on `is_safe_endpoint` alone.
  Assert the source is never registered and a warning is recorded. Reuse the SSRF
  vectors already covered for datadog/prometheus `_validate_*`.
- `test_observe_router.py` — FastAPI `TestClient`: window `end<=start` → 422;
  bad KIND → 422; `max_records` clamp; empty façade → 200 not 500; no raw URL is
  accepted from the body.
- `test_model_deploy_check.py` — under-provisioned GPU/replica/concurrency specs
  → findings; correct spec → none.
- `test_capability_grants_observe.py` — extend the existing wiring test: each new
  role's `db_ops`/`network_hosts`/`secret_prefixes` exactly match what its
  `tasks/main.yml` invokes (no over-grant); an unknown role denies all
  `/observe` ops (`check_network_host` fail-closed).

**Decision-logic unit tests (per role, table-driven):**
- For each role, feed a synthetic record set with a known planted root cause and
  assert the verdict field (`probable_root_service`, `verdict`,
  `most_likely_cause`, `rollback_candidate`, `recommendation`, `suspicious`).
  These are pure-function tests against the façade output — no daemon needed.

**Molecule (`collections/.../roles/<role>/molecule/`):**
- One scenario per role against a **mock daemon** exposing `/admin/observe/*`
  with canned responses (mirror the existing `molecule_db_probe` pattern for
  `gludd_db`). Assert the role: gates on required vars (assert), writes the
  expected `<role>_result.json`, never writes when `enable_*` is false, and
  fails-closed (rescue path) when the capability policy denies the op or the
  daemon returns an error.
- A `gludd_observe` direct-module molecule scenario (analogous to
  `molecule_db_probe`) exercising every `op` against the mock daemon, with a
  `molecule_observe_probe` capability grant added to the table.

**Negative/fail-closed coverage (binding per `AGENTS.md`):**
- Capability denial → role enters `rescue`, writes a `failed` artifact, exits
  non-zero (copy `agent_task`'s rescue shape).
- SSRF-prone source in config → never queried (registry-build skip), asserted at
  both unit (façade) and molecule (role still succeeds, source simply absent).

---

## 4. Build order for a follow-up agent (implementation-ready checklist)

0. **REUSE, don't recreate.** `connectors/normalize.py` (with
   `normalize_join_keys`/`correlate`/auth-families) and ~34 connectors already
   exist in the canonical tree — import them. The earlier "create normalize.py /
   extend VALID_KINDS" steps are **removed** (both were based on a stale worktree).
   Read `docs/OBSERVABILITY_SOURCES.md`, `docs/privileges/README.md`, and
   `docs/design/connector_wiring_plan.md` before writing any code.
1. **Land the plumbing layer first** per `connector_wiring_plan.md`:
   `connectors/loader.py` + `connectors/transport.py`, `connectors:` `UserConfig`
   field, registry built at daemon startup, base `/api/observe` router
   (sources/health/query) + `_connectors_facet`.
2. `src/general_ludd/observe/facade.py` — `ObserveFacade`, `TimeWindow`. Wrap the
   registry the loader built (`from_config` delegates to `loader.load_connectors`).
   Pure-function methods first (timeline/topology/`correlate` grouping — the last
   delegating to the existing `normalize.correlate`), then `query_sources`.
   Apply the F1 (defer SSRF to connector constructor) and F3 (scrub `raw` from
   error records) hardening from Section 1.5.
3. Add the higher-level `correlate`/`timeline`/`topology` operations onto the
   **same** router the wiring plan defines (one path prefix, not two).
4. `src/general_ludd/observe/model_deploy_check.py` (genuinely new — absent today).
5. `plugins/modules/gludd_observe.py` (copy `gludd_db.py` skeleton: capability
   gate → `GluddClient` → endpoint → `ok_result`/`error_result`).
6. Six role dirs under `roles/` (`tasks/main.yml` + `defaults/main.yml` per the
   `agent_task` template), six capability-policy grants (Section 3.2).
7. Add only the genuinely-missing connectors security_signal needs — confirm
   against `docs/privileges/README.md` first (okta EXISTS; likely-missing:
   cloudflare-audit, k8s-audit, entra sign-ins, cloudtrail, gitlab-CI).
8. Tests per Section 3.4 (unit first, TDD; molecule last). The
   `test_normalize_join_keys.py` test targets the EXISTING `normalize.py`
   vocabulary (`trace_id`/`host`/`service`/`k8s`/`cloud`/`severity`), not a
   new `JOIN_KEYS` constant.
All façade and role logic depends only on the `Source` Protocol + the existing
normalized-record / `normalize.py` correlation contract, so it is fully testable
against the ~34 connectors already in the tree (plus stub `Source`s) — no new
vendor connector is a prerequisite except the handful security_signal needs
(step 7).

---

## 5. Implemented module seam and operator evidence (2026-08-01)

`general_ludd.agent.gludd_observe` is now a real, read-only module rather than a
future placeholder referenced by the six roles. It discovers the daemon's
operator-registered source names through `GET /api/observe/sources`, adapts each
name to `POST /api/observe/query`, and delegates merge/correlation/timeline/
topology behavior to the canonical `GluddObserve` implementation. The module
never accepts a connector URL, caps the source catalog at 1,000 entries, rejects
malformed or duplicate catalog rows, returns deterministic JSON-safe topology
lists, and exposes generic failures without transport exception text.

This implementation deliberately fixes resolver diagnostics instead of mocking
modules or suppressing lint rules:

- Ansible operators have reported collection-resource lookup failures whose
  symptoms are misleading controller-relative paths; the discussion in
  [ansible/ansible#74917](https://github.com/ansible/ansible/issues/74917)
  reinforces that an FQCN must resolve to a valid shipped collection resource.
  The prior roles named an FQCN for a module that did not exist, so the correct
  response was to ship and test the module, not add it to a lint mock list.
- Relative task resolution remains an active operator pain point; the
  [ansible-lint nested `include_tasks` fix proposal](https://github.com/ansible/ansible-lint/pull/5055)
  documents the same `load-failure` class. Gludd's stale partial networking role
  referenced six files it did not own. It now delegates to the complete
  `general_ludd.networking.networking` role, leaving one canonical task tree.
- A long-running custom-module report,
  [ansible/ansible#82414](https://github.com/ansible/ansible/issues/82414),
  describes `AnsibleUnsafeText` casting changes breaking `Pathlib.Path` in
  custom modules. `gludd_observe` avoids filesystem/path conversion entirely,
  works on the typed argument-spec containers, and copies query dictionaries
  before forwarding them.

Verification is executable: the direct module tests cover source discovery,
bounded fan-out, ordering, correlation, deterministic topology, malformed
catalogs, query failures, and auth/transport failure. The production
`ansible-lint` profile reports zero failures and zero warnings without skip
rules.

The `test_gludd_observe` Molecule scenario now closes the direct-module coverage
item from Section 3.4. It runs all four operations against the reusable HTTP mock
daemon, proves client-side time-window filtering when a source ignores bounds,
checks deterministic topology and incident grouping, and verifies that a 503 from
one registered connector becomes an isolated error while the other sources are
returned. The scenario uses Molecule's local/default driver with no managed
platform because the system under test is the Ansible module-to-daemon HTTP seam.
That choice follows the operator guidance in the
[Ansible forum's Molecule setup discussion](https://forum.ansible.com/t/molecule-setup-documentation-and-testing/626):
scenario step playbooks are independent lifecycle units and simple cases may need
only a converge play. It also avoids the long-lived delegated-driver inventory
ambiguity reported in
[ansible/molecule#1292](https://github.com/ansible/molecule/issues/1292), where
maintainers explain that delegated users must supply their own connection setup.
Gludd instead binds its namespaced mock daemon to loopback, records every request,
and keeps its PID, async-job identifier, and result below Molecule's randomized
ephemeral directory. That subdirectory is mode `0700`, the result and job ID are
mode `0600`, launch/inspection/termination use argument-vector Ansible modules,
and cleanup kills only a PID whose command line contains the exact server,
port, and private PID-file path before removing that one directory. Repeated
cleanup is idempotent and refuses to conceal a listener it did not stop.

Molecule 26.2 changed scenario discovery for large and nested trees. Its
[upstream discovery change](https://github.com/ansible/molecule/pull/4613)
documents `MOLECULE_GLOB` as the supported selection boundary, and the earlier
[Ansible forum release discussion](https://forum.ansible.com/t/release-announcement-molecule-v24-8-0/7906)
records that Molecule commands were being aligned on that variable. Gludd's
`molecule-test` target therefore points directly at the canonical
`molecule/playbooks/*/molecule.yml` tree instead of copying a selected scenario
into a transient second source tree. A checked-in, no-op `default` scenario
declares `shared_state: false`; this satisfies Molecule's discovery probe without
creating infrastructure or emitting a false critical error. `molecule-reset`
uses Molecule's own scoped state reset rather than recursively deleting a source
directory.

---

## 6. Change log

- **2026-06-16 (rev 2):** Corrected Section 0 after parallel agents inventoried
  the **canonical** tree (this design was first drafted against a stale worktree):
  ~34 connectors exist, `connectors/normalize.py` + `ingest_formats.py` exist,
  okta exists. Removed the "create normalize.py / extend VALID_KINDS" steps
  (reuse instead). Folded in security findings F1 (SSRF authority = connector
  constructor, not the weaker/unused `is_safe_endpoint`) and F3 (scrub `raw`/
  redact `message` on error records before HTTP return). Added a reconciliation
  note tying this workflow-layer design to `docs/design/connector_wiring_plan.md`
  (the plumbing layer) and `docs/OBSERVABILITY_SOURCES.md` / `docs/privileges/`
  (the authoritative inventories). The façade API, the 6 roles, and the
  capability grants are unchanged in substance.
- **2026-08-01 (rev 3):** Implemented the missing `gludd_observe` Ansible seam,
  replaced the partial duplicate networking role with canonical collection
  delegation, and documented operator reports behind the resolver/path-safety
  decisions.
- **2026-08-02 (rev 4):** Added executable Molecule coverage for every
  `gludd_observe` operation, bounded fan-out, topology/correlation, and isolated
  connector failure; documented the relevant Molecule operator reports and
  aligned scenario discovery/reset with the Molecule 26 contract.
