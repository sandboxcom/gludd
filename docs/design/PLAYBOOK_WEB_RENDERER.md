# Design Doc: Playbook Web Renderers

Status: **IMPLEMENTED (Phase 1 — canonical + schema-driven)**
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
(via the existing `gludd_facts` module or direct `ansible.builtin.uri` calls
to `/api/facts`) and emits a JSON artifact. The daemon discovers the
playbook, runs it on demand, and renders the JSON as HTML through a server-
side Jinja2 template. Users visit `/render/<name>` in a browser; new
dashboards are added by dropping a YAML file in `playbooks/renderers/` — no
daemon restart, no code change, no JS toolchain. Two equally first-class
shapes are supported (Path A canonical / Path B schema-driven — see §3.3).

### Non-Goals

- General-purpose BI / chart-studio. Path A renderers compose a fixed set of
  section types; complex bespoke visualizations belong behind `raw_html` or an
  external tool.
- User-uploaded (untrusted) playbooks. Renderers run with the daemon's
  privileges — **operator-curated only** (checked into the repo or placed by
  an administrator). Same trust boundary applies to companion
  `<name>.schema.json` files (see §8).
- Client-side SPA framework integration. Default surface is server-rendered
  Jinja2 + HTMX (Appendix A).
- Real-time push (WebSocket/SSE). Phase 1 is request/response with a TTL
  cache; live refresh is achievable via HTMX polling.
- Authentication of individual end-users. Renderers reuse the daemon's PSK
  model (admin PSK now; optional read-only `GLUDD_RENDER_PSK` in Phase 4).

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
    renderer_timeout_seconds: 20
    artifact_dir: "{{ artifact_dir | default('/tmp/gludd-render-gpu') }}"
    daemon_url: "{{ daemon_url | default('http://localhost:8000') }}"
    psk: "{{ psk | default('') }}"
  tasks:
    - name: Pull live facts
      general_ludd.agent.gludd_facts: { daemon_url: "{{ daemon_url }}", psk: "{{ psk }}" }
      register: live_facts
      no_log: "{{ psk | length > 0 }}"
    - name: Build per-model cost table
      ansible.builtin.set_fact:
        _gpu_rows: >-
          {{ live_facts.ansible_facts.gludd.metrics.global_model_usage
             | dict2items(key_name='model', value_name='stats') }}
    - name: Write renderer JSON artifact (Path A — canonical sections[])
      ansible.builtin.copy:
        dest: "{{ artifact_dir }}/render.json"
        mode: "0644"
        content: >-
          {{ {
            'title': 'GPU Utilization Dashboard',
            'sections': [
              { 'type': 'metric_grid', 'metrics': [
                  {'label': 'Running agents',
                   'value': live_facts.ansible_facts.gludd.metrics.running_agents},
                  {'label': 'Total cost (USD)',
                   'value': live_facts.ansible_facts.gludd.metrics.cost_by_project
                            | dict | sum | round(2)} ]},
              { 'type': 'table', 'title': 'Per-model usage',
                'columns': ['model','total_calls','success_rate','total_cost_usd'],
                'rows': _gpu_rows } ] } | to_nice_json }}
```

The operator restarts (or hot-reloads) the daemon; the renderer is auto-
discovered. They visit `https://gludd.example/render/gpu_dashboard` and see
the rendered page. `GET /api/renderers` lists it alongside the built-in
`system_facts` renderer.

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
  schema_loader.py   Path B validator      <- jsonschema Draft 2020-12 wiring
  runner.py          run_renderer()        <- async wrapper around AnsibleRunnerAdapter

src/general_ludd/routers/
  render.py          FastAPI router        <- GET /render/<name>, /render/<name>/schema, /api/renderers

templates/render/
  base.html.j2       HTML scaffold + nav
  page.html.j2       Path A — renders canonical JSON via section partials
  schema_page.html.j2        Path B — walks schema properties
  _schema_field.html.j2      Path B — per-field dispatch macro
  schema_error.html.j2       Path B — 422 error page
  sections/  markdown.html.j2  table.html.j2  metric_grid.html.j2
             chart.html.j2  raw_html.html.j2  error.html.j2
```

### 3.2 Playbook registry

The registry lives in `general_ludd.renderers.registry.RendererRegistry`. It
is constructed once at daemon startup (inside `create_daemon_app`, alongside
the other `register_*` calls near `daemon.py:1903`) and stored on
`app.state._renderer_registry`.

Discovery is **convention-over-config**:

1. Scan two directories for `*.yml`:
   - `<repo>/playbooks/renderers/` (shipped examples, version-controlled)
   - `<config_dir>/renderers/` (operator override/additions; wins on clash)
2. For each file, parse the YAML and check the top-level play's `vars` for
   `renderer: true`. Files without this marker are ignored.
3. The renderer **name** is the file stem (`gpu_dashboard.yml` → `gpu_dashboard`).
4. Validate the playbook declares `{{ artifact_dir }}/render.json` (checked
   by static string presence — we do **not** execute the playbook to register).
5. Detect a sibling `<name>.schema.json` to classify as Path B (see §3.7).

The registry exposes:
```python
class RendererRegistry:
    def discover(self) -> None: ...
    def names(self) -> list[str]: ...
    def get(self, name: str) -> RendererSpec: ...
    def metadata(self) -> list[dict]: ...   # for GET /api/renderers
```

`RendererSpec` is a dataclass: `name`, `path`, `timeout_seconds`, `schema_path`
(optional, populated iff Path B), `description` (from the playbook's `- name:`).

### 3.3 Schema declaration — two first-class paths

Every renderer belongs to exactly one of two paths. Both are first-class in
Phase 1; they share the same discovery, registry, caching, and routing
machinery. They differ only in how output is validated and rendered.

**Path A — "Canonical-shape".** The playbook emits the closed 5-type section
set (`markdown` / `metric_grid` / `table` / `chart` / `raw_html`). No schema
file needed. Validated by the pydantic `RenderDocument` models in
`renderers/schema.py`; rendered by `page.html.j2` + section partials.

**Path B — "Schema-driven".** The playbook emits **any** JSON shape matching
a companion `<stem>.schema.json` placed next to `<stem>.yml`. Validated by
`jsonschema` (Draft 2020-12) via `schema_loader.validate_against_schema`;
rendered by `schema_page.html.j2` walking the schema `properties` and
dispatching to the `_schema_field.html.j2` macro per field.

| | Path A (canonical) | Path B (schema-driven) |
|---|---|---|
| Output shape | closed 5-type `sections[]` | any JSON matching companion schema |
| Validator | pydantic `RenderDocument` | `jsonschema` Draft 2020-12 |
| Template | `page.html.j2` + section partials | `schema_page.html.j2` + `_schema_field.html.j2` |
| Required files | `<name>.yml` | `<name>.yml` + `<name>.schema.json` |
| Failure status | `500` + error section | `422` + `schema_error.html.j2` |

**When to use which:**
- **Path A** for quick operator dashboards composing the canned widgets
  (metric grid, table, chart). Lowest effort — declare `renderer: true` and
  emit `sections[]`.
- **Path B** when the data has a **formal contract** (titles, descriptions,
  types, enums, required vs optional) and rendering should be **derived from
  the schema** rather than hand-authored per section. Useful for stable fact
  surfaces — e.g. `system_facts.schema.json` describing the host fact tree.

Discovery decides which path applies per renderer (see §3.7). Both coexist in
the same registry; mixed deployments are the expected norm.

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

    @app.get("/render/{name}", response_class=HTMLResponse)
    async def render_named(name: str) -> HTMLResponse:
        spec = app.state._renderer_registry.get(name)   # miss -> 404
        if (hit := app.state._renderer_cache.get(name)) is not None:
            return HTMLResponse(hit)
        try:
            doc = await run_renderer(app, spec)         # asyncio.to_thread(...)
        except RendererTimeout:
            return HTMLResponse(_render_error(...), status_code=504)
        except RendererFailure as exc:
            return HTMLResponse(_render_error(exc), status_code=500)
        html = _render_jinja(doc)
        app.state._renderer_cache.set(name, html)
        return HTMLResponse(html)

    @app.get("/render/{name}/schema")                   # Path B only; 404 for Path A
    async def render_schema(name: str) -> Response:
        spec = app.state._renderer_registry.get(name)
        if spec.schema_path is None: raise HTTPException(404)
        return Response(spec.schema_path.read_text(), media_type="application/schema+json")

    @app.get("/api/renderers", summary="List renderer playbooks (admin)")
    async def list_renderers() -> dict[str, Any]:
        reg = app.state._renderer_registry
        return {"renderers": reg.metadata(), "count": len(reg)}
```

Registration is added to `daemon.py` next to the existing `facts.register(...)`
call (~line 1903) and to `routers/__init__.register_all`.

### 3.6 HTML rendering layer — server-side Jinja2 + HTMX (RECOMMENDED)

**Recommendation: server-rendered Jinja2 + HTMX.** Tradeoff matrix and full
rationale live in Appendix A; summary: zero build step, Jinja2 is already a
dep, HTMX is a single 14 KB `<script>` (CDN or vendored). The default page
loads with HTMX optional — `hx-get="/render/<name>?partial=1"` on a `<div>`
gives auto-refresh every N seconds.

**Tradeoff explicitly accepted:** no rich client-side interactivity (sortable
tables, drag-and-drop). Renderers needing that should emit `raw_html` with
inline `<script>` or link out to an external tool. This is a deliberate scope
boundary for Phase 1–3.

### 3.7 Schema-driven rendering (Path B)

This subsection covers the Path B flow end-to-end. Path A flows through the
existing pydantic validation + `page.html.j2` template (§3.4–§3.6).

**Discovery.** For each `<stem>.yml`, `RendererRegistry.discover()` also looks
for a sibling `<stem>.schema.json`. If present → Path B, `spec.schema_path`
populated; otherwise → Path A. The schema is parsed eagerly
(`jsonschema.Draft202012Validator`) so malformed schemas surface at daemon
startup, not first request.

**Execution.** `run_renderer` branches on `spec.schema_path`:
- **Path A** — `RenderDocument.model_validate(raw)`.
- **Path B** — `renderers.schema_loader.validate_against_schema(raw, spec.schema_path)`.
  Failure → `SchemaValidationError` → router maps to **HTTP 422** and renders
  `templates/render/schema_error.html.j2` (path + message; full details
  operator-only).

The metadata-overwrite step (`execution_ms`, `playbook`, `generated_at`,
`renderer_version`) applies identically to both paths.

**Rendering.** Path B uses `schema_page.html.j2` (top-level page; title and
description from the schema, then iterates `schema["properties"]`) plus the
`_schema_field.html.j2` field-dispatch macro:

| JSON Schema construct | Rendered widget |
|---|---|
| `type: string` (no `enum`) | text cell |
| `type: string` + `enum` | `<select>` (read-only) |
| `type: number` / `integer` | metric card (label = `title`) |
| `type: boolean` | yes/no badge |
| `type: array` (items primitive) | `<table>` (one column) |
| `type: array` (items object) | `<table>` (columns = item properties) |
| `type: object` | nested fieldset (recurses) |
| `format: date-time` | timestamp cell |
| `$ref` / `allOf` / `oneOf` | resolved by `schema_loader` first |

**Field-metadata bridge.** `schema_loader.extract_field_metadata(prop, name)
-> FieldMetadata` flattens a JSON Schema fragment into the dataclass the macro
consumes: `title` (fallback: humanized `name`), `description`, resolved
`type`, `enum`, `format`, `required`, `items`. Other keywords are preserved
on `FieldMetadata.raw` for operators who extend the template.

**New endpoint.** `GET /render/<name>/schema` returns the raw
`application/schema+json` for a Path B renderer (404 for Path A) — the
canonical contract for clients that render the form themselves. Same auth
posture as `GET /render/<name>` (Phase 1: public GET; Phase 4:
`GLUDD_RENDER_PSK` if set).

---

## 4. JSON Output Contract

Every Path A renderer MUST write `{{ artifact_dir }}/render.json` matching
the canonical shape below. Validated at execution time by `renderers/schema.py`
(pydantic). Path B renderers follow §"Path B output contract" below.

```json
{
  "title": "GPU Utilization Dashboard",
  "sections": [
    { "type": "markdown",  "content": "## Hello\n\nSome **markdown**." },
    { "type": "metric_grid", "metrics": [
      { "label": "Running agents", "value": 7, "unit": "" },
      { "label": "Burn rate",      "value": 0.42, "unit": "USD/min" }
    ]},
    { "type": "table", "title": "Per-model usage",
      "columns": ["model", "calls", "success_rate", "cost_usd"],
      "rows": [ ["sonnet", 1234, 0.96, 12.30], ["opus", 18, 0.88, 4.10] ]
    },
    { "type": "chart", "title": "Cost over time", "chart_type": "line",
      "data": { "labels": ["00:00","01:00","02:00"],
                "series": [{"name":"cost","values":[1.1,2.3,3.8]}] } },
    { "type": "raw_html", "html": "<iframe src='...'></iframe>" }
  ],
  "metadata": { "generated_at": "2026-06-28T14:03:22Z",
                "playbook": "gpu_dashboard.yml",
                "execution_ms": 412, "renderer_version": 1 }
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

### Path B output contract (schema-driven)

Path B renderers do **not** need to match the canonical `sections[]` shape —
they need to match their companion `<name>.schema.json`. The runner still
injects the same `metadata` block. Example schema (flat object of host facts):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "System Facts",
  "type": "object",
  "required": ["hostname", "uptime_seconds"],
  "properties": {
    "hostname":       { "type": "string", "description": "Kernel hostname" },
    "uptime_seconds": { "type": "integer", "description": "Seconds since boot" },
    "load": { "type": "array", "items": { "type": "number" },
              "description": "1/5/15-min load averages" }
  }
}
```

New renderer schemas and tests use the explicit 2020-12 dialect. The validator
continues to recognize legacy drafts for operator-owned resources, but Gludd does
not emit draft-07 examples that keep deprecated-validator warnings alive. The
JSON Schema community's long-running version-support discussion recommends
2020-12 for new work while acknowledging draft-07 compatibility needs:
[json-schema-org discussion #192](https://github.com/orgs/json-schema-org/discussions/192).
Its follow-up roadmap discussion records that simple object schemas generally
migrate unchanged while tooling support and incompatible advanced keywords are
the practical upgrade boundary:
[json-schema-org discussion #282](https://github.com/orgs/json-schema-org/discussions/282).

Matching `render.json` — **no** `sections[]`:

```json
{ "hostname": "gpu-node-03", "uptime_seconds": 918273, "load": [1.42, 1.08, 0.93] }
```

The metadata block is added by the runner on top of this payload.

---

## 5. Playbook Contract

A renderer playbook is a normal gludd playbook with three additional
requirements:

1. **Marker.** The first play's `vars:` MUST contain `renderer: true`.
2. **Artifact path.** The playbook MUST write its output to
   `{{ artifact_dir }}/render.json`. The runner creates `artifact_dir`
   (default `/tmp/gludd-render-<name>-<uuid>`) and reads this path afterward.
3. **Optional knobs (vars).**
   - `renderer_timeout_seconds` (int, default 30) — per-renderer timeout.
   - `renderer_cache_ttl_seconds` (int, default 30) — per-renderer cache TTL.
   - `renderer_description` (string) — shown in `/api/renderers`.
   - `renderer_allow_raw_html` (bool, default false) — see §8.

The playbook may use any existing role/module — typically
`general_ludd.agent.gludd_facts` (the same module the `report_*` roles use)
to pull `/api/facts`, then compute derived values with `set_fact` and dump
them via `ansible.builtin.copy` to `render.json`.

Phase 1 acceptance fixtures: `playbooks/renderers/system_facts.yml` (Path A
example emitting a `metric_grid` from `gludd.work.*` / `gludd.history.*`)
and its companion `system_facts.schema.json` (Path B example over the same
fact tree).

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
    cache = app.state._renderer_cache
    if (hit := cache.get(spec.name)) is not None:
        return hit
    artifact_dir = tempfile.mkdtemp(prefix=f"gludd-render-{spec.name}-")
    extra_vars = {"artifact_dir": artifact_dir,
                  "daemon_url": _daemon_url(app), "psk": _local_psk(app)}
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_ansible_runner.run, spec.path, extra_vars),
            timeout=spec.timeout_seconds)
    except asyncio.TimeoutError:
        raise RendererTimeout(spec.name, spec.timeout_seconds)
    if result.rc != 0:
        raise RendererFailure(spec.name, result.stdout, result.stderr)
    try:
        raw = json.loads((Path(artifact_dir) / "render.json").read_text())
    except FileNotFoundError:
        raise RendererFailure(spec.name, msg="render.json not written")
    # Path A vs Path B branch (see §3.7)
    doc = (RenderDocument.model_validate(raw)
           if spec.schema_path is None
           else _validate_path_b(raw, spec.schema_path))
    doc.metadata.execution_ms = int((time.monotonic() - start) * 1000)
    doc.metadata.playbook = spec.path.name
    cache.set(spec.name, doc, ttl=spec.cache_ttl_seconds)
    return doc
```

**Key choices:**
- **Async via `asyncio.to_thread`** wrapping `AnsibleRunnerAdapter` (already
  imported in `daemon.py:21`). Does not block the event loop; multiple
  renderers execute concurrently.
- **Timeout.** `asyncio.wait_for(..., timeout=spec.timeout_seconds)`. Default
  30s, configurable per-renderer via `renderer_timeout_seconds`.
- **Caching.** In-memory `RendererCache` (TTL dict, default 30s, per-renderer
  override). Keyed by renderer name only (Phase 1 renderers are not
  parameterized). Stores the **validated `RenderDocument`**, not HTML, so the
  HTML layer can be re-rendered without re-running the playbook.
  `DELETE /api/renderers/<name>/cache` and `DELETE /api/renderers/cache`
  (PSK-gated) invalidate.
- **Error handling.** Timeout → `504` + error section (name + timeout). Non-
  zero exit / missing `render.json` / shape validation failure → `500` +
  error section with stdout/stderr tail (operator-only — see §8). Path B
  schema validation failure → `422` + `schema_error.html.j2`. Registry miss
  → `404`. Any other runner exception is logged with its internal context and
  becomes a generic `500` page; exception text never crosses the public HTTP
  boundary.

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
| **Companion schema is operator-curated** | `<name>.schema.json` lives alongside the playbook in `playbooks/renderers/` or `<config_dir>/renderers/` — the same directories the registry already scans. Never user-uploadable. Same trust boundary as the playbook. |
| **Schema-driven rendering autoescape** | All values rendered through `schema_page.html.j2` are autoescaped (Jinja2 default), including schema snippets embedded in validation errors. Schema-side strings (`title`, `description`, `enum` labels) are also escaped for defense-in-depth — operator-curated but never trusted as raw HTML. |
| **Unexpected runner failure** | The public endpoint returns only a generic `500` error page. The original exception and stack remain in the operator log, preventing unauthenticated disclosure of backend paths, commands, or credentials. |

### Practitioner evidence

A [FastAPI practitioner discussion about generic exception handling](https://github.com/fastapi/fastapi/discussions/9478)
shows the failure mode this boundary guards against: without an explicit handler,
an unexpected application exception can escape the route instead of producing the
intended HTTP response. The renderer therefore catches unexpected runner failures
at its HTTP boundary, logs the internal exception, and regression-tests both the
sanitized response and the absence of the exception text.

---

## 9. Testing Strategy

### Unit — `tests/unit/test_render_schema_loader.py` (Path B)

- `test_validate_against_schema_accepts_valid_payload` — flat payload matching fixture schema → no raise.
- `test_validate_against_schema_rejects_missing_required` — missing `required` → `SchemaValidationError` with correct path.
- `test_validate_against_schema_rejects_wrong_type` — `string` where `integer` → raises.
- `test_validate_against_schema_rejects_bad_enum_value` — value outside `enum` → raises.
- `test_extract_field_metadata_*` — `title`/`description` picked, name humanized as fallback, `enum` populated.
- `test_drafted_schema_load_failure_surfaces_at_startup` — malformed `<name>.schema.json` → `discover()` raises (fail-fast).

### Unit — `tests/unit/test_schema_template_render.py` (Path B)

- `test_render_string_field` → text cell.
- `test_render_enum_field_renders_select` → `<select>`.
- `test_render_number_field_renders_metric` → metric card.
- `test_render_object_field_recurses` → fieldset that calls back into `_schema_field`.
- `test_render_array_of_objects_renders_table` → `<table>` with item properties as columns.
- `test_schema_strings_are_escaped` — schema `title` with `<script>` → literal text (no XSS).

### Unit — `tests/unit/test_system_facts_schema.py` (acceptance fixture)

- `test_system_facts_schema_is_draft_2020_12` — parses under `Draft202012Validator`.
- `test_system_facts_schema_covers_fixture_payload` — emitted JSON validates against its schema.
- `test_system_facts_render_returns_200` — `GET /render/system_facts` yields `text/html` with the schema `title`.

### Unit — `tests/unit/test_renderer_registry.py`

- `test_discover_finds_bundled_renderer` — fixture playbook in tmp dir → in `registry.names()`.
- `test_discover_ignores_non_renderer_playbooks` — no `renderer: true` → skipped.
- `test_operator_dir_overrides_bundled` — same stem both dirs → operator wins.
- `test_schema_validation_rejects_bad_shape` — malformed JSON → `ValidationError`.
- `test_metadata_overwrite` — runner replaces `metadata.execution_ms` / `playbook` regardless.

### Integration — `tests/integration/test_render_api.py`

Mirrors `tests/integration/test_messages_and_facts_api.py` (real daemon via `ASGITransport`, PSK via `monkeypatch.setenv`):

- `test_render_known_renderer_returns_html` — fixed `render.json` → `text/html` + title.
- `test_render_unknown_returns_404`.
- `test_api_renderers_lists_renderer` — `GET /api/renderers` (PSK) → metadata.
- `test_api_renderers_requires_psk` — no auth → 401.
- `test_renderer_cache_hit_skips_execution` — patch runner, hit twice, called once.
- `test_renderer_failure_returns_500` — no `render.json` → 500 + error section.
- `test_renderer_timeout_returns_504` — over `renderer_timeout_seconds` → 504.

Schema-driven flows (same file, cont.):

- `test_render_schema_driven_renderer_returns_html` — Path B (`<stem>.yml` + `<stem>.schema.json`) writes flat payload → HTML with schema `title`.
- `test_render_schema_driven_validation_failure_returns_422` — missing `required` → 422 + `schema_error.html.j2`.
- `test_render_schema_endpoint_returns_application_schema_json` — `GET /render/<stem>/schema` → `application/schema+json`.
- `test_render_schema_endpoint_404_for_path_a_renderer` — canonical renderer → 404.
- `test_render_path_a_and_path_b_coexist` — both render through their respective templates.

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

### Phase 1 — MVP (canonical + schema-driven)

Path A (canonical-shape):
- `renderers/registry.py` (discovery + `RendererSpec`, incl. `schema_path`)
- `renderers/schema.py` (pydantic models for canonical JSON shape)
- `renderers/runner.py` (sync execution via `asyncio.to_thread`)
- `routers/render.py` (`GET /render/<name>`, `GET /api/renderers`)
- Templates: `base.html.j2`, `page.html.j2`, section partials (`markdown`,
  `metric_grid`, `table`), `error.html.j2`.
- Shipped fixture: `playbooks/renderers/system_facts.yml`.

Path B (schema-driven) — first-class in Phase 1 because formal-contract use
cases (dashboards over stable fact trees) are an MVP goal:
- `renderers/schema_loader.py` (`validate_against_schema`,
  `extract_field_metadata`, `Draft202012Validator` wiring).
- Templates: `schema_page.html.j2`, `_schema_field.html.j2`, `schema_error.html.j2`.
- New endpoint: `GET /render/<name>/schema` (`application/schema+json`).
- Shipped fixture: `playbooks/renderers/system_facts.schema.json`.
- Path B unit suites (§9).

Shared:
- Wire into `daemon.py` (`render.register(app, daemon_state)` ~line 1903) and
  `routers/__init__.register_all`.
- `pyproject.toml`: confirm `jinja2`; add `jsonschema>=4.21` (Path B).
- Markdown rendering: use `markdown` lib IF already a dep; otherwise Phase 1
  renders `markdown` sections as `<pre>` (Phase 2 adds CommonMark).

### Phase 2 — Richer sections
- `chart` section type (inline SVG; no JS dep). Vendor a JS chart lib only if
  needed (no npm).
- Proper CommonMark rendering for `markdown` sections (add `markdown` to
  `pyproject.toml` — small, pure-Python).
- HTMX auto-refresh: `base.html.j2` includes optional `hx-get="...?partial=1"`
  polling. Applies to both Path A and Path B pages.

### Phase 3 — Caching
- `RendererCache` (in-memory TTL dict); per-renderer TTL via
  `renderer_cache_ttl_seconds`.
- Invalidation endpoints: `DELETE /api/renderers/<name>/cache`,
  `DELETE /api/renderers/cache` (PSK-gated).
- Cache metrics (`gludd_renderer_cache_hits_total`,
  `gludd_renderer_cache_misses_total`).

### Phase 4 — Per-renderer auth
- Optional read-only PSK (`GLUDD_RENDER_PSK`). When set, `/render/*`
  (including `/render/<name>/schema`) is removed from `_PUBLIC_PATHS` and
  gated by this PSK instead of the admin PSK.
- Per-renderer ACL via `renderer_acl:` list in `vars:` naming PSK "roles"
  (operator-configured; default: any valid render PSK).
- Audit log entry on each render (renderer name, caller, status, ms).

---

## Appendix A: Why Jinja2 + HTMX and not React/Vue

- **Zero build step** — daemon serves templates directly; no npm/bundler.
- **Jinja2 is already a dep** (`pyproject.toml:38`).
- **HTMX is ~14 KB** (single `<script>` tag) for partial-page refresh without
  a SPA.
- **Renderers are server-curated** — browser is a thin viewer; complex
  interactivity belongs in playbooks or behind `raw_html`.
- **Tradeoff:** no client-side state (sortable tables, drag-and-drop). The
  `table` section can grow server-side sort query params later if needed.

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
- `src/general_ludd/renderers/__init__.py`, `registry.py`, `schema.py`,
  `schema_loader.py`, `runner.py`, `cache.py`
- `src/general_ludd/routers/render.py`
- `src/general_ludd/templates/render/base.html.j2`, `page.html.j2`,
  `schema_page.html.j2`, `_schema_field.html.j2`, `schema_error.html.j2`,
  `error.html.j2`, and `sections/{markdown,metric_grid,table,chart,raw_html}.html.j2`
- `playbooks/renderers/system_facts.yml`
- `playbooks/renderers/system_facts.schema.json`
- `tests/unit/test_renderer_registry.py`, `test_render_schema_loader.py`,
  `test_schema_template_render.py`, `test_system_facts_schema.py`,
  `test_render_security.py`
- `tests/integration/test_render_api.py`

Modified files:
- `src/general_ludd/daemon.py` — instantiate `RendererRegistry`/`RendererCache`,
  call `render.register(app, daemon_state)` ~line 1903; add `/render/` prefix
  handling to `_is_public` for GET-only.
- `src/general_ludd/routers/__init__.py` — add `render` to `register_all`.
- `pyproject.toml` — confirm `jinja2`; add `jsonschema>=4.21` (Path B).
  Add `markdown` only in Phase 2.
