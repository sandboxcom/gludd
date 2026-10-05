# Connector Wiring Plan — make the observability connectors a reachable feature

Status: design, uncommitted. Author: planning agent. Date: 2026-06-16.

## Problem statement (verified against code)

`src/general_ludd/connectors/` ships a connector *spine* and ~32 concrete
backend connectors, but they are **dead code from the running product's point of
view**:

- `connectors/__init__.py` re-exports only the **contract** (`base.py`:
  `Source`, `SourceRegistry`, `Observability`, `normalized_record`,
  `is_safe_endpoint`). It does **not** import or register a single concrete
  connector.
- `daemon.py` never imports `general_ludd.connectors` at all. Its big router
  import block (`daemon.py:1048-1076`) and `register(...)` calls
  (`daemon.py:1078-1140`) contain no connector/observability router.
- There is **no loader**: `load_startup_config` (`daemon.py:72-164`) reads
  `model_routing`, `general-ludd.yml` → `UserConfig`, `binary_paths`, `openbao`,
  `ansible`, `mcp_servers`, `tasks`, `model_profiles` — nothing for connectors.
- `UserConfig` (`config/user_config.py:46-77`) has no `connectors:` field. Its
  `observability` field (`ObservabilityConfig`, lines 13-15) only carries
  `otel_endpoint` + `service_name`, consumed at `daemon.py:710-720` to build the
  `OTelBridge`. That is *export* telemetry, unrelated to the *connector* (pull)
  layer.
- `/api/facts` (`routers/facts.py:305-345`) has facets for work/todos/models/
  history/messages/metrics/traces/codebase/features/dispatch/spend/accounting/
  schedule/coordination — **no `connectors`/`observability` facet**.

So nothing instantiates a `PrometheusSource`/`GitHubActionsSource`/etc., nothing
populates a `SourceRegistry`, no `Observability` facade exists at runtime, and no
HTTP surface lists or queries sources.

This plan wires them in: a config-driven **loader**, a runtime
**`SourceRegistry` + `Observability`** placed on `app.state`, an
**`/api/observe` router** (list / health / query), and a **facts facet**.

## What the concrete connectors actually look like (grounding)

Read in full: `connectors/base.py`, `connectors/prometheus.py` (`PrometheusSource`,
KIND `metrics`), `connectors/github_actions.py` (`GitHubActionsSource`, KIND
`pipeline`), `connectors/grafana_loki.py` (`GrafanaLokiSource`, KIND `logs`),
`connectors/jaeger.py` (`JaegerSource`, KIND `traces`), `connectors/signoz.py`
(`SigNozSource`, KIND `traces`, plus `SECONDARY_KINDS = ("metrics",)`).

Shared, load-bearing facts the loader **must** accommodate:

1. **Duck-typed `Source`** (`base.py:112-130`): every connector exposes a class
   attr `KIND`, an instance attr `name` (set in `__init__`), `health() -> dict`
   (never raises), and `query(spec) -> list[dict]`. They deliberately do **not**
   inherit a base class. `SourceRegistry` keys by `.name`
   (`base.py:165-167`), last-write-wins.

2. **Constructors vary — this is the crux.** The first positional arg is always
   `config: dict`, but the HTTP transport differs:
   - `PrometheusSource(config, http_get=None, *, timeout=...)` — **requires**
     `http_get` (raises `ValueError` if `None`, line 136-137).
   - `GitHubActionsSource(config, *, http_get=None)` — transport optional;
     defaults to a real `urllib` transport (`_default_http_get`, line 143).
   - `GrafanaLokiSource(config, *, transport, timeout=...)` — **requires**
     keyword `transport` (different kwarg name, line 115-121).
   - `JaegerSource(config, transport=None)` — optional positional/keyword
     `transport`, defaults to `_UrllibTransport` (line 113, 129).
   - `SigNozSource(config, *, transport, timeout=...)` — requires `transport`.

   The transport *shape* also differs: Prometheus/GitHub use
   `http_get(url, params=, headers=) -> (status, json)` (note: GitHub's is
   `http_get(url, headers) -> (status, json)` — positional headers!), Loki/SigNoz
   use a `_Transport.request(method, url, *, headers, json, params, timeout)`
   object, Jaeger uses `HttpTransport.get(url, *, headers, timeout) -> HttpResponse`.

   **Design consequence:** the loader cannot assume one constructor shape. It
   must drive construction from a per-connector **spec entry** that names the
   import path, class, and which transport kwarg (if any) to inject — and may
   default to letting the connector build its own real transport (GitHub/Jaeger).

3. **Secrets are env-var *names*, never raw.** Every connector reads its token
   from `os.environ[config["*_env"]]` at call time (`prometheus.py:151-156`,
   `github_actions.py:147-156`, `grafana_loki.py:130-135`, `jaeger.py:126-127`,
   `signoz.py:151-160`). The config block carries `token_env`, never a literal
   secret. The loader must enforce this: reject any key literally named
   `token`/`api_key`/`password`/`secret` in a connector config (only `*_env` is
   allowed).

4. **SSRF is enforced at construction** by each connector's literal-host guard
   (`base.py:319` `is_safe_endpoint`, plus per-connector `_validate_base_url`).
   The `base_url` comes from operator config, **never** from a request. The
   `/api/observe` router never accepts a raw URL — only a registered source name
   + a `spec` dict.

## 1. Connector registry / loader

### 1.1 New module: `src/general_ludd/connectors/loader.py`

A declarative, signature-tolerant loader. Because constructors vary (§2 above),
the loader is driven by an explicit **kind→builder** table rather than blind
`cls(config)` calls. Each builder knows how to construct one connector family
and how to feed it a transport.

```python
# connectors/loader.py  (NEW)
from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import Any

from general_ludd.connectors.base import Source, SourceRegistry, VALID_KINDS

logger = logging.getLogger(__name__)

# Keys that may NEVER appear in a connector config block (raw-secret guard).
_FORBIDDEN_SECRET_KEYS = frozenset({"token", "api_key", "apikey", "password", "secret", "bearer"})

# Default import path + class for the known connector "kinds-of-backend".
# Operators reference these by `class` shorthand; an explicit dotted
# `class: "pkg.mod:ClassName"` is also accepted for connectors not in this map.
_BUILTIN_CLASSES: dict[str, str] = {
    "prometheus":      "general_ludd.connectors.prometheus:PrometheusSource",
    "github_actions":  "general_ludd.connectors.github_actions:GitHubActionsSource",
    "grafana_loki":    "general_ludd.connectors.grafana_loki:GrafanaLokiSource",
    "jaeger":          "general_ludd.connectors.jaeger:JaegerSource",
    "signoz":          "general_ludd.connectors.signoz:SigNozSource",
    # ... extend with the remaining concrete connectors (one line each).
    # The build agent fills this from the connectors/ directory inventory.
}


class ConnectorConfigError(ValueError):
    """Operator config is malformed (bad class, raw secret, missing base_url)."""


def _resolve_class(spec_class: str) -> type[Any]:
    """Resolve a 'pkg.mod:Class' or a builtin shorthand to a class object."""
    dotted = _BUILTIN_CLASSES.get(spec_class, spec_class)
    if ":" not in dotted:
        raise ConnectorConfigError(f"unknown connector class: {spec_class!r}")
    module_path, _, cls_name = dotted.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


def _reject_raw_secrets(config: dict[str, Any], source_name: str) -> None:
    for key in config:
        if key.lower() in _FORBIDDEN_SECRET_KEYS:
            raise ConnectorConfigError(
                f"connector {source_name!r}: key {key!r} looks like a raw secret; "
                f"use a '*_env' env-var NAME instead (e.g. token_env)."
            )


def _instantiate(cls: type[Any], config: dict[str, Any], transport_factory: Callable[[], Any] | None) -> Source:
    """Construct a connector, tolerating the divergent transport kwargs (§2).

    Strategy: try the broadest signature first, fall back. Connectors that build
    their own real transport when given None (GitHub, Jaeger) are constructed
    plainly; connectors that REQUIRE an injected transport get one from
    transport_factory (a real urllib/httpx adapter the daemon owns).
    """
    transport = transport_factory() if transport_factory is not None else None
    # Probe constructor kwargs by name; do not guess blindly.
    import inspect
    params = inspect.signature(cls.__init__).parameters
    kwargs: dict[str, Any] = {}
    if "http_get" in params and transport is not None:
        kwargs["http_get"] = transport
    if "transport" in params and transport is not None:
        kwargs["transport"] = transport
    return cls(config, **kwargs)  # type: ignore[return-value]


def load_connectors(
    connectors_cfg: list[dict[str, Any]] | None,
    *,
    transport_factory: Callable[[], Any] | None = None,
) -> SourceRegistry:
    """Build a SourceRegistry from the operator's `connectors:` config list.

    Each entry: {name?, kind, class, config{...}}.  `config` carries base_url,
    *_env names, and connector-specific keys — NEVER raw secrets.  A single bad
    entry is logged and skipped (fail-soft per-connector, fail-loud in logs); a
    structurally-broken block does not abort daemon startup.
    """
    registry = SourceRegistry()
    for entry in connectors_cfg or []:
        name = entry.get("name", "<unnamed>")
        try:
            kind = entry["kind"]
            if kind not in VALID_KINDS:
                raise ConnectorConfigError(f"invalid kind {kind!r}; want one of {sorted(VALID_KINDS)}")
            config = dict(entry.get("config") or {})
            _reject_raw_secrets(config, name)
            if name != "<unnamed>":
                config.setdefault("name", name)
            cls = _resolve_class(entry["class"])
            source = _instantiate(cls, config, transport_factory)
            # Cross-check declared kind vs the connector's own KIND.
            if getattr(source, "KIND", kind) != kind:
                logger.warning(
                    "connector %s: declared kind %r != class KIND %r; using class KIND",
                    name, kind, getattr(source, "KIND"),
                )
            registry.register(source)
            logger.info("connector loaded: name=%s kind=%s class=%s",
                        getattr(source, "name", name), getattr(source, "KIND", kind), entry["class"])
        except Exception as exc:  # fail-soft per connector, loud in logs
            logger.warning("skipping connector %s: %s", name, exc)
    return registry
```

**Why `inspect.signature` rather than a try/except cascade:** it deterministically
matches the kwarg name the constructor actually declares (`http_get` vs
`transport`), so we never pass an unexpected kwarg and never silently drop a
required transport. `PrometheusSource`/`GrafanaLokiSource`/`SigNozSource` *require*
a transport; if `transport_factory` is `None` for those, the connector raises
`ValueError` and the entry is skipped with a clear log line — fail-closed, not a
silent half-wired source.

### 1.2 The real transport factory

The injectable transports are normally `None`-defaulted to a stdlib `urllib`
adapter inside each connector. For the connectors that *require* injection
(Prometheus/Loki/SigNoz), the daemon owns a small adapter module so all egress
shares one timeout/policy:

```python
# connectors/transport.py  (NEW)  — real, time-bounded transports
# - http_get(url, *, params=None, headers=None) -> (status, json)   (Prometheus shape)
# - a request(...)-style object                  (Loki / SigNoz _Transport shape)
# Each wraps urllib with a hard timeout, no shell, no redirects to private hosts.
```

The loader is passed `transport_factory=make_default_transport` (returns the
appropriate adapter). Because Loki/SigNoz want a `request(...)` object and
Prometheus wants an `http_get` callable, `make_default_transport` returns an
object that satisfies *both* (a callable that is also `.request`-capable) **or**
the loader's `_instantiate` selects per-kwarg-name — the latter is cleaner: the
factory can return a small adapter instance and `_instantiate` injects it under
whichever kwarg name the constructor declares. (Note the GitHub transport takes
`headers` positionally; GitHub's transport is optional so we let it self-build
its own `_default_http_get` rather than inject — i.e. for `github_actions` the
spec sets no injected transport.)

> Build-agent note: keep the transport adapter dead simple. The connectors are
> already SSRF-guarded at construction; the adapter just enforces a timeout and
> never uses `shell=True`. Do not reimplement SSRF here.

### 1.3 Where it is built in the daemon lifespan

Built **once** during `_lifespan`, alongside the other subsystems, after
`uc = startup_config.get("user_config")` is available
(`daemon.py:438-443`) and near the OTel bridge block (`daemon.py:710-720`).

```python
# daemon.py _lifespan, after secrets_resolver / before/after otel bridge:
from general_ludd.connectors.base import Observability
from general_ludd.connectors.loader import load_connectors
from general_ludd.connectors.transport import make_default_transport

connectors_cfg = []
if uc is not None and getattr(uc, "connectors", None):
    connectors_cfg = uc.connectors
source_registry = load_connectors(connectors_cfg, transport_factory=make_default_transport)
app.state._source_registry = source_registry
app.state._observability = Observability(source_registry)
logger.info("Observability connectors: %d source(s) registered", len(source_registry.all()))
```

Placed inside the same `try:` as the rest of startup so a malformed block surfaces
via the existing `app.state._degraded` path (`daemon.py:721-723`) — but because
`load_connectors` is itself fail-soft per entry, normal operation only logs
skips. On the **degraded / not-configured** path `app.state._source_registry`
may be unset; every reader uses `getattr(app.state, "_source_registry", None)`
and treats `None` as "no sources" (mirrors `_spend_limiter` handling,
`facts.py:242-250`).

## 2. The `/api/observe` router (list / health / query)

### 2.1 New module: `src/general_ludd/routers/observe.py`

Follows the established `register(app, _daemon_state)` convention
(`routers/spend.py:40`, `routers/todos.py:76`, `routers/mcp.py:10`). All paths
are under `/api/observe`, which is **not** in `_PUBLIC_PATHS`
(`daemon.py:887-890`), so the global PSK middleware (`daemon.py:904-945`) gates
every call — least-privilege by default, identical to `/api/facts` and
`/api/spend`.

Endpoints:

| Method + path | Purpose | Auth |
|---|---|---|
| `GET /api/observe/sources` | List configured sources: `[{name, kind}]` | PSK (non-public) |
| `GET /api/observe/health` | Run `health()` across all sources (or `?kind=`) | PSK |
| `POST /api/observe/query` | Run `Observability.find(spec, kinds)` | PSK (mutating method → never public even if path were listed; matches AUTH-1, `daemon.py:892-902`) |
| `POST /api/observe/associate` *(optional)* | Correlate a prior result via `Observability.associate` | PSK |

```python
# routers/observe.py  (NEW)
from __future__ import annotations
import logging
from typing import Any
from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
_VALID_KINDS = {"pipeline", "logs", "metrics", "traces"}


class ObserveQueryRequest(BaseModel):
    # NOTE: no `url` / `base_url` field — SSRF-safe by construction. Callers can
    # only target operator-registered sources by name/kind, never an arbitrary host.
    spec: dict[str, Any] = Field(default_factory=dict)
    kinds: list[str] | None = None
    source: str | None = None          # restrict to one registered source by name
    limit: int = Field(default=200, ge=1, le=2000)


def _registry(app: FastAPI) -> Any:
    return getattr(app.state, "_source_registry", None)

def _observability(app: FastAPI) -> Any:
    return getattr(app.state, "_observability", None)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get("/api/observe/sources")
    async def observe_sources() -> dict[str, Any]:
        reg = _registry(app)
        sources = reg.all() if reg is not None else []
        return {
            "count": len(sources),
            "sources": [{"name": s.name, "kind": getattr(s, "KIND", "unknown")} for s in sources],
        }

    @app.get("/api/observe/health")
    async def observe_health(kind: str | None = None) -> dict[str, Any]:
        reg = _registry(app)
        if reg is None:
            return {"count": 0, "results": []}
        sources = reg.by_kind(kind) if kind else reg.all()
        results = []
        for s in sources:
            try:
                h = s.health()              # contract: never raises (base.py:123-124)
            except Exception as exc:        # defensive belt-and-suspenders
                h = {"ok": False, "error": str(exc)}
            results.append({"name": s.name, "kind": getattr(s, "KIND", "unknown"), "health": h})
        return {"count": len(results), "results": results}

    @app.post("/api/observe/query")
    async def observe_query(req: ObserveQueryRequest) -> dict[str, Any]:
        obs = _observability(app)
        reg = _registry(app)
        if obs is None or reg is None:
            return {"count": 0, "records": [], "error": "no sources configured"}
        if req.kinds:
            bad = [k for k in req.kinds if k not in _VALID_KINDS]
            if bad:
                from fastapi import HTTPException
                raise HTTPException(status_code=422, detail=f"invalid kinds: {bad}")
        if req.source is not None:
            src = reg.get(req.source)
            if src is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail=f"unknown source: {req.source!r}")
            records = src.query(req.spec)          # single registered source
        else:
            records = obs.find(req.spec, kinds=req.kinds)   # fan-out (base.py:197)
        return {"count": min(len(records), req.limit), "records": records[: req.limit]}
```

**Auth posture (explicit):**
- Reuses the *existing* middleware; no new auth code. `/api/observe/*` is absent
  from `_PUBLIC_PATHS`, so when `GLUDD_AUTH_PSK` is set every method requires the
  Bearer token; when `GLUDD_REQUIRE_AUTH=1` and no PSK, it 503s
  (`daemon.py:913-921`); the open-but-warned default applies as for all other
  non-public routes.
- `POST` is intrinsically non-public under AUTH-1 (`_is_public` returns False for
  non-safe methods, `daemon.py:899-902`).

**SSRF posture (explicit):**
- The request schema has **no** URL/host field. The only way to reach a backend
  is through a source the *operator* registered in `connectors:` config, whose
  `base_url` was SSRF-validated at construction (§ grounding pt 4). A caller can
  pass a `spec` (PromQL/LogQL/service filters) and pick among registered
  sources, but can never introduce a new host. This closes the request-driven
  SSRF surface entirely.

## 3. `/api/facts` integration

Add a `connectors` facet to the facts payload, mirroring `_spend_facet`
(`facts.py:236-257`) — defensive, `None`-safe, reuses the live registry/health.

```python
# routers/facts.py  (EDIT) — new facet function near _spend_facet
def _connectors_facet(app: FastAPI) -> dict[str, Any]:
    reg = getattr(app.state, "_source_registry", None)
    if reg is None:
        return {"configured": False, "count": 0, "by_kind": {}, "sources": []}
    sources = reg.all()
    by_kind: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for s in sources:
        kind = getattr(s, "KIND", "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        # health() is contractually non-raising; include a compact ok/flag only
        # so /api/facts stays cheap (full detail lives at /api/observe/health).
        try:
            ok = bool(s.health().get("ok"))
        except Exception:
            ok = False
        rows.append({"name": s.name, "kind": kind, "healthy": ok})
    return {"configured": True, "count": len(sources), "by_kind": by_kind, "sources": rows}
```

Wire it into the `api_facts` return dict (`facts.py:329-345`), adding one key:

```python
        "connectors": _connectors_facet(app),
```

**Cost note for the build agent:** `_connectors_facet` calls each source's
`health()`, which performs a network probe. `/api/facts` is already an
aggregation endpoint, but to keep it cheap consider: (a) gating the health probe
behind a `?include_connector_health=true` query param (default off → return only
`name`/`kind`/`configured`), or (b) caching health with a short TTL on
`app.state`. Recommend (a) for a first cut — list sources unconditionally, probe
health only on demand. Full health always available at `/api/observe/health`.

## 4. Config schema — the `connectors:` UserConfig block

### 4.1 `config/user_config.py` (EDIT)

`UserConfig` already has `extra="ignore"` (line 63) so unknown YAML keys are
dropped today — that's exactly why a `connectors:` block currently vanishes. Add
a typed field:

```python
# config/user_config.py  (EDIT) — add to UserConfig (after `observability`, line 75)
    connectors: list[dict[str, Any]] = []
```

Keep it a `list[dict]` (not a strict model) so the loader owns validation and a
malformed entry degrades gracefully rather than failing `UserConfig` construction
at startup (`daemon.py:112` does `UserConfig(**data)` — a strict model there
would abort all of startup on one bad connector). Optionally also extend
`ObservabilityConfig` (lines 13-15) with `connector_health_in_facts: bool = False`
to back the §3 cost toggle.

### 4.2 YAML shape (operator-facing, goes in `general-ludd.yml`)

```yaml
# general-ludd.yml
connectors:
  - name: ci-prod                 # registry key (else derived from class/host)
    kind: pipeline
    class: github_actions         # builtin shorthand (or "pkg.mod:ClassName")
    config:
      repo: my-org/my-repo
      base_url: https://api.github.com
      token_env: GITHUB_TOKEN     # env-var NAME — never a literal token
  - name: prom-prod
    kind: metrics
    class: prometheus
    config:
      base_url: https://prom.internal.example.com
      token_env: PROM_BEARER_ENV
  - name: loki-prod
    kind: logs
    class: grafana_loki
    config:
      base_url: https://loki.example.com
      token_env: LOKI_TOKEN
  - name: traces-prod
    kind: traces
    class: jaeger
    config:
      base_url: https://jaeger.example.com
      service: my-service
      token_env: JAEGER_TOKEN
```

### 4.3 Env-var resolution

- **Backend secrets** (tokens) are resolved **inside the connector** via
  `os.environ[config["*_env"]]` at call time (already implemented, §grounding
  pt 3). The loader passes the `*_env` *name* through untouched; it never reads
  the secret. This means rotating a token requires no daemon restart-of-config —
  only the env var changes.
- **`UserConfig` itself** is env-overridable via `GLUDD_CONNECTORS` (JSON) thanks
  to pydantic-settings (`user_config.py:46-64`, env prefix `GLUDD_`,
  `from_yaml` env-merge at lines 96-105). So
  `GLUDD_CONNECTORS='[{"name":"x","kind":"metrics","class":"prometheus","config":{"base_url":"https://..","token_env":"PROM_TOKEN"}}]'`
  works for env-only deployments.
- The loader's raw-secret guard (`_reject_raw_secrets`) ensures an operator who
  mistakenly inlines a token (`token: abc`) gets a loud skip + log, not a
  silently-leaked secret in `app.state`/facts.

## 5. File-by-file change list + test plan

### New files

| File | Contents | Grounding |
|---|---|---|
| `src/general_ludd/connectors/loader.py` | `load_connectors()`, `_resolve_class`, `_reject_raw_secrets`, `_instantiate`, `_BUILTIN_CLASSES`, `ConnectorConfigError` (§1.1) | constructor variance from prometheus/github_actions/grafana_loki/jaeger/signoz `__init__` |
| `src/general_ludd/connectors/transport.py` | `make_default_transport()` real time-bounded urllib adapter(s) for the inject-required connectors (§1.2) | transport contracts in each connector |
| `src/general_ludd/routers/observe.py` | `register(app, _daemon_state)` + `/api/observe/{sources,health,query,associate}` (§2.1) | `routers/spend.py`, `routers/todos.py` register pattern |

### Edited files

| File | Edit | Line anchor |
|---|---|---|
| `src/general_ludd/connectors/__init__.py` | (optional) also export `load_connectors` for convenience; keep base re-exports | currently lines 16-40 export only base |
| `src/general_ludd/config/user_config.py` | add `connectors: list[dict[str, Any]] = []` to `UserConfig`; optional `connector_health_in_facts` on `ObservabilityConfig` | after line 75 / lines 13-15 |
| `src/general_ludd/daemon.py` | in `_lifespan`: build `SourceRegistry` + `Observability` from `uc.connectors`, store on `app.state._source_registry` / `_observability` (§1.3) | inside the `try:` block, near otel bridge `daemon.py:710-720` |
| `src/general_ludd/daemon.py` | import `observe` in the router import block; call `observe.register(app, _daemon_state)` | import block `daemon.py:1048-1076`; register calls `daemon.py:1078-1140` |
| `src/general_ludd/routers/facts.py` | add `_connectors_facet(app)`; add `"connectors": _connectors_facet(app)` to the `api_facts` return | new fn near `_spend_facet` (line 236); return dict lines 329-345 |
| `src/general_ludd/routers/__init__.py` | (if `register_all` is used anywhere) add `register_observe` import + call | lines 11-46 — note `daemon.py` registers routers directly, so this is only for parity |

> The build agent must replace the `_BUILTIN_CLASSES` `...` placeholder with the
> full connector inventory from `connectors/*.py` (one `name: "module:Class"`
> line per concrete connector). Use the per-connector `KIND` class attr to pick
> the right `kind` in example config. Each connector's `__init__` signature must
> be re-read to confirm whether it needs `transport_factory` (required-inject:
> Prometheus/Loki/SigNoz; self-building: GitHub/Jaeger) — `_instantiate`'s
> `inspect.signature` handles both, but the `class` shorthand list must be
> complete for operator ergonomics.

### Test plan (describe — TDD, write tests first per AGENTS.md)

**Unit — loader (`tests/unit/test_connector_loader.py`)**
- `load_connectors([])` → empty registry, no error.
- A valid prometheus entry with an injected fake transport_factory → registry has
  one source, `.by_kind("metrics")` returns it, `.name` matches config `name`.
- Entry with `class: "nonsense"` → skipped, registry empty, warning logged
  (caplog), startup not aborted.
- Entry with a raw secret key (`config: {token: "abc"}`) → `ConnectorConfigError`
  caught → skipped + logged; assert the source is **not** registered and the
  literal never appears in any registered object.
- `kind` mismatch vs class `KIND` → source still registered under class KIND,
  warning logged.
- A connector that *requires* a transport (Prometheus) with
  `transport_factory=None` → `ValueError` from the connector → entry skipped
  (fail-closed), registry empty.
- kwarg-name selection: a fake class declaring `http_get` vs one declaring
  `transport` each receive the transport under the correct kwarg
  (`inspect.signature` path).

**Unit — facts facet (`tests/unit/test_facts_connectors_facet.py`)**
- No registry on state → `{"configured": False, count: 0, ...}`.
- Registry with two fake sources (one healthy, one unhealthy) → correct
  `by_kind` counts and `healthy` flags; health-probe exceptions → `healthy:false`,
  never raises.

**Integration — observe router (`tests/integration/test_observe_router.py`)**
- Build app via `create_daemon_app` with a temp config dir whose
  `general-ludd.yml` has a `connectors:` block using a fake/echo connector
  (monkeypatch `_resolve_class` or point `class` at a test-only module).
- `GET /api/observe/sources` (with PSK header) → lists the configured sources;
  without PSK and `GLUDD_AUTH_PSK` set → 401 (verifies middleware gating, mirrors
  existing facts/spend auth tests).
- `GET /api/observe/health` → per-source health dicts; a source whose `health()`
  raises is reported `ok:false`, request still 200.
- `POST /api/observe/query` with `{spec, kinds}` → fan-out records; `?source=`
  path restricts to one source; unknown `source` → 404; invalid `kind` → 422.
- **SSRF assertion:** confirm `ObserveQueryRequest` has no url/host field and that
  no request body can introduce a new backend host (schema-level test +
  `pytest.raises`/422 on an attempt to smuggle `base_url` — pydantic `extra`
  default ignores it, so assert it is dropped, not honored).

**Integration — facts (`tests/integration/test_facts_endpoint.py`, extend)**
- `GET /api/facts` includes a `connectors` key; with no `connectors:` config it
  is `{"configured": False, ...}`; with config it lists sources.

**Lifespan (`tests/integration/test_daemon_lifespan.py`, extend — already in the
working tree's modified set)**
- After startup with a `connectors:` block, `app.state._source_registry` and
  `app.state._observability` are populated; with none, they are absent/`None` and
  every reader degrades cleanly.

### Validation gates (per CLAUDE.md / make-only)
`make test-count` (collection), `make lint`, `make typecheck`, `make test`,
then `make qa`. New router is registered exactly once (`observe.register`) — add
an assertion to the existing "all routers registered" test if one exists, else
grep-style count check in the lifespan test.

## Design invariants (carry into implementation)

1. **No raw secrets anywhere** — only `*_env` names in config; loader rejects
   literal-secret keys; facts/observe never echo a token.
2. **SSRF-safe by construction** — operators register hosts; requests pick by
   name; no request carries a URL. Per-connector `_validate_base_url` stays the
   single egress-host gate.
3. **Fail-soft per connector, fail-loud in logs** — one bad entry never aborts
   startup; every reader treats a missing registry as "no sources".
4. **Reuse, don't duplicate** — registry/observability come from `base.py`
   verbatim; auth comes from the existing middleware; the facts facet mirrors
   `_spend_facet`; the router mirrors `spend.py`/`todos.py`.
5. **Least privilege** — `/api/observe/*` is non-public; PSK-gated like
   `/api/facts`.
```text
```
