# Design Doc: Playbook Web Renderers

Status: **DESIGN — not yet implemented**
Owner: gludd core
Last updated: 2026-06-28

A configurable mechanism where an operator writes an Ansible playbook that
returns a specific JSON shape; the gludd daemon renders that JSON as an HTML
page at `GET /render/<renderer_name>`.

---

## 1. Goal & Non-Goals

### Goal

Let operators extend gludd with self-service web dashboards without writing
Python. A renderer is just an Ansible playbook that pulls facts/traces/metrics
(via the existing `gludd_facts` module or direct `ansible.builtin.uri` calls to
`/api/facts`) and emits a single canonical JSON artifact. The daemon discovers
that playbook, runs it on demand, and renders the JSON as HTML through a
server-side Jinja2 template. Users visit `/render/<name>` in a browser and see
a gludd-rendered view; new dashboards are added by dropping a YAML file in
`playbooks/renderers/` — no daemon restart, no code change, no JavaScript
toolchain.

### Non-Goals

- A general-purpose BI / chart-studio tool. Renderers produce a fixed set of
  section types (markdown, table, metric_grid, chart, raw_html); complex
  bespoke visualizations belong behind `raw_html` or an external tool.
- User-uploaded (untrusted) playbooks. Renderers run with the daemon's
  privileges and are **operator-curated only** — checked into the repo or
  placed in the operator's config dir by an administrator.
- Client-side SPA framework integration (React/Vue build pipelines). The
  default surface is server-rendered Jinja2 + HTMX. A client-side layer is a
  documented future option, not part of Phase 1.
- Real-time push (WebSocket/SSE) dashboards. Phase 1 is request/response with
  a TTL cache. Live refresh is achievable client-side via HTMX polling against
  the same endpoint.
- Authentication of individual end-users. Renderers reuse the daemon's existing
  PSK model (operator admin PSK, plus an optional separate read-only PSK in a
  later phase).

---

## 2. User Story

> **As an operator**, I want a "GPU utilization dashboard" so my team can see
> at a glance which agents are saturating GPUs, what the per-project cost is,
> and which models are dominating the burn.

The operator authors one file, `playbooks/renderers/gpu_dashboard.yml`:

```yaml
---
- name: GPU utilization dashboard
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    renderer: true                       # required marker
    renderer_timeout_seconds: 20         # optional, default 30
    artifact_dir: "{{ artifact_dir | default('/tmp/gludd-render-gpu') }}"
    daemon_url: "{{ daemon_url | default('http://localhost:8000') }}"
    psk: "{{ psk | default('') }}"

  tasks:
    - name: Pull live facts
      general_ludd.agent.gludd_facts:
        daemon_url: "{{ daemon_url }}"
        psk: "{{ psk }}"
      register: live_facts
      no_log: "{{ psk | length > 0 }}"

    - name: Build per-model cost table
      ansible.builtin.set_fact:
        _gpu_rows: >-
          {{
            live_facts.ansible_facts.gludd.metrics.global_model_usage
            | dict2items(key_name='model', value_name='stats')
          }}

    - name: Write renderer JSON artifact
      ansible.builtin.copy:
        dest: "{{ artifact_dir }}/render.json"
        mode: "0644"
        content: >-
          {{ {
            'title': 'GPU Utilization Dashboard',
            'sections': [
              {
                'type': 'metric_grid',
                'metrics': [
                  {'label': 'Running agents',
                   'value': live_facts.ansible_facts.gludd.metrics.running_agents},
                  {'label': 'Total cost (USD)',
                   'value': live_facts.ansible_facts.gludd.metrics.cost_by_project
                            | dict | sum | round(2)}
                ]
              },
              {
                'type': 'table',
                'title': 'Per-model usage',
                'columns': ['model', 'total_calls', 'success_rate', 'total_cost_usd'],
                'rows': _gpu_rows
              }
            ]
          } | to_nice_json }}
```

The operator restarts (or hot-reloads) the daemon. The renderer is auto-
discovered. They visit `https://gludd.example/render/gpu_dashboard` and see
the rendered page. They curl `GET /api/renderers` and see it listed alongside
the built-in `system_facts` renderer.

---

## 3. Architecture

### 3.1 Component map

```
playbooks/renderers/                       <- operator-curated renderer playbooks
  gpu_dashboard.yml
  system_facts.yml                          <- shipped example (Phase 1 acceptance fixture)

src/general_ludd/renderers/
  registry.py        RendererRegistry      <- discovers + catalogs playbooks at startup
  cache.py           RendererCache         <- in-memory TTL cache (Phase 3)
  schema.py          canonical JSON shape  <- pydantic models for validation
  runner.py          run_renderer()        <- async wrapper around AnsibleRunnerAdapter

src/general_ludd/routers/
  render.py          FastAPI router        <- GET /render/<name>, GET /api/renderers

templates/render/
  base.html.j2       HTML scaffold + nav
  page.html.j2       renders canonical JSON via section partials
  sections/
    markdown.html.j2
    table.html.j2
    metric_grid.html.j2
    chart.html.j2
    raw_html.html.j2
    error.html.j2
```

### 3.2 Playbook registry

The registry lives in `general_ludd.renderers.registry.RendererRegistry`. It
is constructed once at daemon startup (inside `create_daemon_app`, alongside
the other `register_*` calls near `daemon.py:1903`) and stored on
`app.state._renderer_registry`.

Discovery is **convention-over-config**:

1. Scan two directories for `*.yml`:
   - `<repo>/playbooks/renderers/` (shipped examples, version-controlled)
   - `<config_dir>/renderers/` (operator override/additions; wins on name clash)
2. For each file, parse the YAML and check the top-level play's `vars` for
   `renderer: true`. Files without this marker are ignored (so other playbooks
   can coexist in the dir).
3. The renderer **name** is the file stem (`gpu_dashboard.yml` → `gpu_dashboard`).
4. Validate the playbook declares the required artifact path
   `{{ artifact_dir }}/render.json` (checked by static string presence — we do
   **not** execute the playbook to register it).
5. Optionally validate a companion schema file (see §3.3).

The registry exposes:
```python
class RendererRegistry:
    def discover(self) -> None: ...
    def names(self) -> list[str]: ...
    def get(self, name: str) -> RendererSpec: ...
    def metadata(self) -> list[dict]: ...   # for GET /api/renderers
```

`RendererSpec` is a dataclass: `name`, `path`, `timeout_seconds`, `schema_path`
(optional), `description` (parsed from the playbook's `- name:`).

### 3.3 Schema declaration

Two equivalent mechanisms, either is sufficient:

**(a) Inline marker only (default).** The playbook sets `renderer: true` and
the registry accepts any output that matches the canonical JSON shape (§4).
Validation happens at **execution time**, not registration time — the runner
parses `render.json` and validates against the pydantic models in
`renderers/schema.py`. Mismatch → 500 + error section in the HTML.

**(b) Companion JSON Schema file (optional, for stricter authoring).** The
playbook ships alongside `gpu_dashboard.schema.json`; if present, the registry
validates the playbook's *output* against it after each run. This is for
operators who want to catch drift early; it is not required.

We deliberately do **not** require playbooks to declare a per-renderer JSON
shape — the whole point is that **every renderer returns the same canonical
shape** (§4). The shape's `sections` list is the only variable part, and its
section types are a closed set enforced by pydantic.

### 3.4 Discovery, execution, caching flow

```
[ startup ]
  create_daemon_app()
    -> RendererRegistry().discover()           # scan both dirs
    -> app.state._renderer_registry = registry
    -> RendererCache(ttl_default=30).attach(app.state)
    -> render.register(app, daemon_state)      # new router

[ request: GET /render/gpu_dashboard ]
  1. registry.get("gpu_dashboard") -> spec     # 404 if unknown
  2. cache.get(spec.name) -> hit? return rendered HTML
  3. miss: runner.run(spec, timeout=spec.timeout) -> JSON artifact
     - parses artifact_dir/render.json
     - validates via schema.py pydantic models
  4. Jinja2 renders page.html.j2 with the validated JSON
  5. cache.set(spec.name, html, ttl=spec.ttl_seconds)
  6. return HTMLResponse(html)
```

### 3.5 New FastAPI router — `src/general_ludd/routers/render.py`

```python
def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.get(
        "/render/{name}",
        response_class=HTMLResponse,
        summary="Render a renderer playbook as HTML",
    )
    async def render_named(name: str) -> HTMLResponse:
        registry: RendererRegistry = app.state._renderer_registry
        cache: RendererCache = app.state._renderer_cache
        spec = registry.get(name)              # raises -> 404
        cached = cache.get(name)
        if cached is not None:
            return HTMLResponse(cached)
        try:
            doc = await run_renderer(app, spec)   # asyncio.to_thread(...)
        except RendererTimeout:
            return HTMLResponse(_render_error(...), status_code=504)
        except RendererFailure as exc:
            return HTMLResponse(_render_error(exc), status_code=500)
        html = _render_jinja(doc)
        cache.set(name, html)
        return HTMLResponse(html)

    @app.get(
        "/api/renderers",
        summary="List registered renderer playbooks (admin)",
    )
    async def list_renderers() -> dict[str, Any]:
        registry: RendererRegistry = app.state._renderer_registry
        return {"renderers": registry.metadata(), "count": len(registry)}
```

Registration is added to `daemon.py` next to the existing `facts.register(...)`
call (~line 1903) and to `routers/__init__.register_all`.

### 3.6 HTML rendering layer — server-side Jinja2 + HTMX (RECOMMENDED)

**Recommendation: server-rendered Jinja2 + HTMX.** Rationale:

| Approach | Build step | Latency | Complexity | New deps |
|---|---|---|---|---|
| **Jinja2 + HTMX (chosen)** | none | one round-trip per render | low | htmx via CDN `<script>` or vendored 14 KB file |
| Jinja2 only (no JS) | none | one round-trip per render | lowest | none |
| Vue/React SPA | npm/esbuild | cold-start cost, API round-trip | high | framework + bundler |
| HTMX-only (no Jinja2) | none | partial swaps | low | htmx |

Jinja2 is already a dependency (`pyproject.toml:38`). HTMX is added as a
single `<script>` tag served from `templates/render/base.html.j2` — either
via CDN or vendored under `src/general_ludd/templates/vendor/htmx.min.js`
(served through FastAPI's `StaticFiles`). The default page loads with HTMX
present but optional: a `hx-get="/render/<name>?partial=1"` on a `<div>`
gives auto-refresh every N seconds without a full page reload.

**Tradeoff explicitly accepted:** no rich client-side interactivity (sortable
tables, drag-and-drop). Renderers needing that should emit `raw_html` with
inline `<script>` or link out to an external tool. This is a deliberate scope
boundary for Phase 1–3.

---

## 4. JSON Output Contract

Every renderer playbook MUST write a file at
`{{ artifact_dir }}/render.json` matching this shape. Validated at execution
time by `renderers/schema.py` (pydantic).

```json
{
  "title": "GPU Utilization Dashboard",
  "sections": [
    { "type": "markdown",  "content": "## Hello\n\nSome **markdown**." },
    { "type": "metric_grid", "metrics": [
      { "label": "Running agents", "value": 7, "unit": "" },
      { "label": "Burn rate",      "value": 0.42, "unit": "USD/min" }
    ]},
    { "type": "table",
      "title": "Per-model usage",
      "columns": ["model", "calls", "success_rate", "cost_usd"],
      "rows": [
        ["sonnet", 1234, 0.96, 12.30],
        ["opus",   18,   0.88, 4.10]
      ]
    },
    { "type": "chart",
      "title": "Cost over time",
      "chart_type": "line",
      "data": { "labels": ["00:00","01:00","02:00"],
                "series": [{"name":"cost","values":[1.1,2.3,3.8}] }
    },
    { "type": "raw_html", "html": "<iframe src='...'></iframe>" }
  ],
  "metadata": {
    "generated_at": "2026-06-28T14:03:22Z",
    "playbook": "gpu_dashboard.yml",
    "execution_ms": 412,
    "renderer_version": 1
  }
}
```

### Section type reference

| type | required keys | rendering |
|---|---|---|
| `markdown` | `content` (string, CommonMark) | rendered via Jinja2 + a markdown filter (python `markdown` lib if already a dep; else a minimal converter — see §6 Phasing) |
| `metric_grid` | `metrics: [{label, value, unit?}]` | responsive grid of cards |
| `table` | `title?`, `columns: [str]`, `rows: [[cell]]` | HTML `<table>`; cells are strings/numbers |
| `chart` | `title?`, `chart_type` (`line`\|`bar`\|`pie`), `data: {labels, series}` | rendered with inline SVG (Phase 2). Phase 1 may render the `data` as a table fallback. |
| `raw_html` | `html` (string) | **escaped by default**; only allowed when playbook sets `renderer_allow_raw_html: true` (operator opt-in per-renderer). See §8. |

The `metadata` block is populated by the **runner** (not the playbook): it
overwrites/merges `generated_at`, `execution_ms`, `renderer_version`, and
`playbook` after execution so the playbook cannot lie about timing.

---

## 5. Playbook Contract

A renderer playbook is a normal gludd playbook with three additional
requirements:

1. **Marker.** The first play's `vars:` MUST contain `renderer: true`. This is
   what the registry keys on during discovery.
2. **Artifact path.** The playbook MUST write its output to
   `{{ artifact_dir }}/render.json`. The runner creates `artifact_dir`
   (default `/tmp/gludd-render-<name>-<uuid>`) before execution and reads
   this exact path afterward.
3. **Optional knobs (vars).**
   - `renderer_timeout_seconds` (int, default 30) — per-renderer timeout.
   - `renderer_cache_ttl_seconds` (int, default 30) — per-renderer cache TTL.
   - `renderer_description` (string) — shown in `/api/renderers`.
   - `renderer_allow_raw_html` (bool, default false) — see §8.

The playbook may use any existing role/module — typically
`general_ludd.agent.gludd_facts` (the same module the `report_*` roles use,
see `roles/report_status/tasks/main.yml:11`) to pull `/api/facts`, then
compute derived values with `set_fact` and dump them via
`ansible.builtin.copy` to `render.json`.

A complete example is the Phase 1 acceptance fixture
`playbooks/renderers/system_facts.yml` — a thin wrapper around
`general_ludd.agent.gludd_facts` that emits a `metric_grid` section from
`gludd.work.*` and `gludd.history.*`.

---

## 6. Discovery & Registration

```python
# src/general_ludd/renderers/registry.py (skeleton)
@dataclass
class RendererSpec:
    name: str
    path: Path
    description: str
    timeout_seconds: int = 30
    cache_ttl_seconds: int = 30
    allow_raw_html: bool = False
    schema_path: Path | None = None

class RendererRegistry:
    def __init__(self, bundled_dir: Path, operator_dir: Path | None):
        self._specs: dict[str, RendererSpec] = {}

    def discover(self) -> None:
        for d in (self._bundled_dir, self._operator_dir):
            if not d or not d.is_dir():
                continue
            for yml in sorted(d.glob("*.yml")):
                spec = self._parse(yml)         # parses vars: block only
                if spec is not None:
                    self._specs[spec.name] = spec   # operator overrides bundled
```

**Validation at registration:**
- File parses as valid Ansible YAML.
- First play has `vars.renderer == true`.
- Playbook source contains the substring `render.json` (cheap liveness check;
  full execution-time validation covers the real shape).

Schema validation of the **output** is deferred to execution (§7).

---

## 7. Execution Model

```python
# src/general_ludd/renderers/runner.py (skeleton)
async def run_renderer(app: FastAPI, spec: RendererSpec) -> RenderDocument:
    cache: RendererCache = app.state._renderer_cache
    if (hit := cache.get(spec.name)) is not None:
        return hit
    artifact_dir = tempfile.mkdtemp(prefix=f"gludd-render-{spec.name}-")
    extra_vars = {"artifact_dir": artifact_dir,
                  "daemon_url": _daemon_url(app),
                  "psk": _local_psk(app)}
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_ansible_runner.run, spec.path, extra_vars),
            timeout=spec.timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise RendererTimeout(spec.name, spec.timeout_seconds)
    if result.rc != 0:
        raise RendererFailure(spec.name, result.stdout, result.stderr)
    try:
        raw = json.loads((Path(artifact_dir) / "render.json").read_text())
    except FileNotFoundError:
        raise RendererFailure(spec.name, msg="render.json not written")
    doc = RenderDocument.model_validate(raw)        # pydantic schema.py
    doc.metadata.execution_ms = int((time.monotonic() - start) * 1000)
    doc.metadata.playbook = spec.path.name
    cache.set(spec.name, doc, ttl=spec.cache_ttl_seconds)
    return doc
```

**Key choices:**
- **Async via `asyncio.to_thread`** wrapping the existing
  `AnsibleRunnerAdapter` (already imported in `daemon.py:21`). Does not block
  the event loop. Multiple renderers can execute concurrently.
- **Timeout.** `asyncio.wait_for(..., timeout=spec.timeout_seconds)`. Default
  30s, configurable per-renderer via `renderer_timeout_seconds`.
- **Caching.** In-memory `RendererCache` (TTL dict, default 30s, configurable
  per-renderer). Cache is keyed by renderer name only (not by query params —
  Phase 1 renderers are not parameterized). Cache stores the **validated
  `RenderDocument`**, not the HTML, so the HTML layer can be re-rendered
  without re-running the playbook. `DELETE /api/renderers/<name>/cache` (PSK-
  gated) invalidates one entry; `DELETE /api/renderers/cache` clears all.
- **Error handling.**
  - Timeout → `504 Gateway Timeout` with an `error` section showing the
    playbook name + timeout value.
  - Non-zero exit / missing `render.json` / shape validation failure → `500`
    with an `error` section that includes the runner's stdout/stderr tail
    (operator-only — see §8; never exposed to unauthenticated viewers).
  - Registry miss → `404`.

---

## 8. Security

| Concern | Mitigation |
|---|---|
| **Untrusted playbook upload** | Renderers are operator-curated ONLY. Discovery scans `<repo>/playbooks/renderers/` and `<config_dir>/renderers/` — never user-writable paths. No HTTP endpoint accepts playbook uploads. Documented in AGENTS.md/README. |
| **Playbook runs with daemon privileges** | Same trust boundary as the existing `system_report.yml` playbook. Renderers are expected to be report-only (`gludd_facts` is read-only); a `renderer: true` playbook that mutates the repo is a misuse and will be flagged in code review. |
| **PSK auth on `/api/renderers`** | Admin PSK-gated by the existing daemon middleware (NOT added to `_PUBLIC_PATHS`). Matches the pattern in `routers/observe.py:19` and `routers/messages.py:4`. |
| **Read access on `/render/<name>`** | Phase 1: `/render/<name>` IS added to `_PUBLIC_PATHS` (read-only GET — the middleware's method-aware `_is_public` already restricts to GET/HEAD/OPTIONS, see `daemon.py:1644-1656`). Phase 4 introduces an optional separate read-only PSK (`GLUDD_RENDER_PSK`) that, when set, replaces public access for `/render/*` only. |
| **HTML injection** | All Jinja2 templates use autoescape (the default). Dynamic content from `sections[*]` flows through typed partials (`table.html.j2`, `metric_grid.html.j2`, etc.) — never through `{% raw %}` or `| safe`. |
| **`raw_html` section** | Disabled by default. Only rendered when the playbook sets `renderer_allow_raw_html: true` AND `app.state._renderer_allow_raw_html_global` is true (operator kill-switch, default false). When disabled, the section renders as a quoted `<pre>` block. |
| **Resource limits** | Per-renderer timeout (default 30s). Max output size: runner reads at most `GLUDD_RENDER_MAX_BYTES` (default 1 MiB) from `render.json`; larger → `RendererFailure`. |
| **SSRF inside playbooks** | `gludd_facts` module already targets the daemon's own URL only. Any playbook making outbound calls is the operator's responsibility — same posture as existing playbooks. |

---

## 9. Testing Strategy

### Unit — `tests/unit/test_renderer_registry.py`

- `test_discover_finds_bundled_renderer` — drop a fixture playbook in a tmp
  `bundled_dir`, assert `registry.names()` contains it.
- `test_discover_ignores_non_renderer_playbooks` — a playbook without
  `renderer: true` is skipped.
- `test_operator_dir_overrides_bundled` — same stem in both dirs → operator
  wins.
- `test_schema_validation_rejects_bad_shape` — feed malformed JSON through
  `RenderDocument.model_validate` → `ValidationError`.
- `test_metadata_overwrite` — runner replaces `metadata.execution_ms` /
  `playbook` regardless of what the playbook wrote.

### Integration — `tests/integration/test_render_api.py`

Mirrors the structure of `tests/integration/test_messages_and_facts_api.py`
(real daemon app via `ASGITransport`, PSK auth via `monkeypatch.setenv`):

- `test_render_known_renderer_returns_html` — registers a trivial renderer
  that writes a fixed `render.json`, hits `GET /render/system_facts`, asserts
  `text/html` + the title appears in the body.
- `test_render_unknown_returns_404`.
- `test_api_renderers_lists_renderer` — `GET /api/renderers` (with PSK)
  returns the registered renderer metadata.
- `test_api_renderers_requires_psk` — no auth header → 401.
- `test_renderer_cache_hit_skips_execution` — patch the runner, hit twice,
  assert runner called once.
- `test_renderer_failure_returns_500` — renderer writes no `render.json` →
  500 with error section in HTML.
- `test_renderer_timeout_returns_504` — renderer sleeps beyond its
  `renderer_timeout_seconds` → 504.

### Fixture

The Phase 1 example renderer
`playbooks/renderers/system_facts.yml` is **checked into the repo** and is the
fixture the integration test executes. It must:
- Not depend on the network (mocks `gludd_facts` via a fixture facts file, or
  uses `ansible.builtin.set_fact` to synthesize the JSON directly in tests).
- Complete in under 2 seconds in CI.

### Guardrail coverage

Add `tests/unit/test_render_security.py`:
- Assert `/render/*` is in `_PUBLIC_PATHS` only for GET/HEAD/OPTIONS (matches
  the existing `_SAFE_METHODS` guard).
- Assert `/api/renderers` is NOT in `_PUBLIC_PATHS`.
- Assert `raw_html` section is escaped when `renderer_allow_raw_html` is false.

---

## 10. Phased Rollout

### Phase 1 — MVP (lands the surface, proves the contract)
- `renderers/registry.py` (discovery + `RendererSpec`)
- `renderers/schema.py` (pydantic models for the canonical JSON shape)
- `renderers/runner.py` (sync execution via `asyncio.to_thread`)
- `routers/render.py` (`GET /render/<name>`, `GET /api/renderers`)
- Jinja2 templates: `base.html.j2`, `page.html.j2`, partials for `markdown`,
  `metric_grid`, `table`, plus an `error.html.j2`.
- One shipped renderer: `playbooks/renderers/system_facts.yml` (wraps
  `gludd_facts`, emits a `metric_grid` from work/history).
- Unit + integration tests (§9).
- Wire into `daemon.py` (`render.register(app, daemon_state)` near line 1903)
  and `routers/__init__.register_all`.
- Markdown rendering: use the `markdown` library IF already a dep; otherwise
  Phase 1 renders `markdown` sections as `<pre>` and Phase 2 adds proper
  CommonMark rendering.

### Phase 2 — Richer sections
- `chart` section type (inline SVG; no JS dep — render server-side from the
  `data` block). If a JS chart lib is desired, vendor it (no npm).
- Proper CommonMark rendering for `markdown` sections (add `markdown` to
  `pyproject.toml` if not already present — small, pure-Python, acceptable).
- HTMX auto-refresh: `base.html.j2` includes an optional
  `hx-get="...?partial=1"` on a `<main>` div for polling.

### Phase 3 — Caching
- `RendererCache` (in-memory TTL dict).
- Per-renderer TTL via `renderer_cache_ttl_seconds`.
- Invalidation endpoints: `DELETE /api/renderers/<name>/cache`,
  `DELETE /api/renderers/cache` (PSK-gated).
- Cache metrics exposed via the existing metrics exporter
  (`gludd_renderer_cache_hits_total`, `gludd_renderer_cache_misses_total`).

### Phase 4 — Per-renderer auth
- Optional separate read-only PSK (`GLUDD_RENDER_PSK`). When set,
  `/render/*` is removed from `_PUBLIC_PATHS` and gated by this PSK instead
  of the admin PSK.
- Per-renderer ACL: a `renderer_acl:` list in the playbook's `vars:` naming
  which PSK "roles" may view it (operator-configured). Default: any valid
  render PSK.
- Audit log entry on each render (renderer name, caller, status, ms).

---

## Appendix A: Why Jinja2 + HTMX and not React/Vue

- **Zero build step.** The daemon serves templates directly; no npm, no
  bundler, no transpile cache. This matches the rest of gludd's "operator-
  runnable from `make init`" posture.
- **Jinja2 is already a dep** (`pyproject.toml:38`).
- **HTMX is ~14 KB**, served as a single `<script>` tag. It gives partial-
  page refresh (polling, click-to-refresh) without a SPA.
- **Renderers are server-curated.** The browser is a thin viewer; complex
  interactivity belongs in operator-authored playbooks (more `sections`) or
  behind `raw_html`.
- **Tradeoff:** no client-side state (sortable tables, drag-and-drop).
  Accepted — the `table` section type can grow server-side sort query params
  in a future phase if needed.

## Appendix B: Relationship to existing `/api/facts`

`/api/facts` (in `routers/facts.py`) is the **data source** most renderers
will consume — it already aggregates work/todos/models/history/messages/
metrics/traces/codebase/features/accounting/schedule/coordination/osquery
into one PSK-gated JSON snapshot. The `gludd_facts` Ansible module (used by
every `report_*` role) wraps that endpoint. Renderers are the **presentation
layer** over the same data: a renderer is essentially a `report_*` role that
emits the canonical `render.json` instead of free-form markdown.

## Appendix C: File-touch checklist (for the implementation task)

New files:
- `src/general_ludd/renderers/__init__.py`
- `src/general_ludd/renderers/registry.py`
- `src/general_ludd/renderers/schema.py`
- `src/general_ludd/renderers/runner.py`
- `src/general_ludd/renderers/cache.py`
- `src/general_ludd/routers/render.py`
- `src/general_ludd/templates/render/base.html.j2`
- `src/general_ludd/templates/render/page.html.j2`
- `src/general_ludd/templates/render/sections/markdown.html.j2`
- `src/general_ludd/templates/render/sections/metric_grid.html.j2`
- `src/general_ludd/templates/render/sections/table.html.j2`
- `src/general_ludd/templates/render/sections/chart.html.j2`
- `src/general_ludd/templates/render/sections/raw_html.html.j2`
- `src/general_ludd/templates/render/error.html.j2`
- `playbooks/renderers/system_facts.yml`
- `tests/unit/test_renderer_registry.py`
- `tests/unit/test_render_security.py`
- `tests/integration/test_render_api.py`

Modified files:
- `src/general_ludd/daemon.py` — instantiate `RendererRegistry`,
  `RendererCache`, call `render.register(app, daemon_state)` near line 1903;
  add `/render/` prefix handling to `_is_public` for GET-only.
- `src/general_ludd/routers/__init__.py` — add `render` to `register_all`.
- `pyproject.toml` — confirm `jinja2` (already present); add `markdown` only
  in Phase 2.
