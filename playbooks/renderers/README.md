# Playbook Web Renderers

This directory holds **renderer playbooks** — Ansible playbooks that produce a
fixed JSON shape the gludd daemon renders as an HTML dashboard at
`GET /render/<renderer_name>`.

Full design spec: [`docs/design/PLAYBOOK_WEB_RENDERER.md`](../../docs/design/PLAYBOOK_WEB_RENDERER.md).

## What a renderer playbook is

A renderer is a normal gludd playbook that:

1. Sets `vars.renderer: true` (the discovery marker).
2. Pulls its data — typically via `general_ludd.agent.gludd_facts`, which wraps
   the daemon's read-only `/api/facts` endpoint.
3. Writes a single artifact to `{{ artifact_dir }}/render.json` using
   `ansible.builtin.copy` with `| to_nice_json`.
4. The artifact matches the **canonical JSON shape** from §4 of the design doc:
   `{ title, sections[], metadata }`. `sections` is a closed set:
   `markdown`, `metric_grid`, `table`, `chart`, `raw_html`.

The daemon discovers the playbook at startup (file stem = renderer name),
executes it on demand with a per-renderer timeout, validates the JSON against
the pydantic models in `src/general_ludd/renderers/schema.py`, renders it
through the Jinja2 templates under `templates/render/`, and serves the result.

## Files

| File | Purpose |
|---|---|
| `system_facts.yml` | Phase 1 acceptance fixture. A thin wrapper around `gludd_facts` that emits a `metric_grid` from `gludd.work.*` + `gludd.history.*` and a `table` from `gludd.models.*`. |

## Adding a new renderer

1. Drop a `<name>.yml` file in this directory (or the operator override dir
   `<config_dir>/renderers/` — operator wins on name clash).
2. The first play's `vars:` MUST contain `renderer: true`.
3. Set the optional knobs as needed:
   - `renderer_timeout_seconds` (default 30) — per-renderer execution timeout.
   - `renderer_cache_ttl_seconds` (default 30) — per-renderer cache TTL.
   - `renderer_description` — shown by `GET /api/renderers`.
   - `renderer_allow_raw_html` (default `false`) — opt-in for `raw_html`
     sections; see §8 of the design doc.
4. The playbook MUST write `{{ artifact_dir }}/render.json`. The runner
   creates `artifact_dir` before execution and reads that exact path after.
5. Visit `GET /render/<name>` to see the rendered page.

No daemon restart, no code change, no JavaScript toolchain.

## Security: operator-curated ONLY

Renderers run with the daemon's privileges and are **operator-curated only**.
Discovery scans `<repo>/playbooks/renderers/` and `<config_dir>/renderers/` —
**never** user-writable paths. There is no HTTP endpoint that accepts playbook
uploads. Treat a renderer playbook with the same trust boundary as the
existing `system_report.yml`: expected to be report-only (the `gludd_facts`
module is read-only). A `renderer: true` playbook that mutates the repo is a
misuse and will be flagged in code review. See §8 of the design doc for the
full security posture (PSK gating, HTML autoescaping, `raw_html` kill-switch,
resource limits).
