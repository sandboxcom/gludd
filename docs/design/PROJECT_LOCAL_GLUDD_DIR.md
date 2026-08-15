# Design: Project-local `.gludd/` directory (request #17)

Status: **DESIGNED (research complete, not yet built)** — 2026-06-26.
Source: 4 read-only mapping agents (config resolution, collections/skills search,
export/archive/weights, live-reload). Owner artifact for ledger request #17.

> Operator request: a project-specific `.gludd/` directory (analogous to
> `$XDG_CONFIG/gludd` but discovered per-repo) with a `collections/` subdir on the
> search path holding project-specific ansible build roles/collections/variables
> for tests + workflows, project-specific skills, weight export + portable gludd
> backup data, support for a full gludd archive export, and live reflection of
> edits within the session — scoped to the specific repo/app.

## Discovery & precedence

`.gludd/` is found by walking UP from cwd to the first ancestor containing a
`.gludd/` directory (git-root style). It takes precedence over (layers on top of)
the user-level `$XDG_CONFIG/gludd`. Layout:

```text
<repo>/.gludd/
  general-ludd.yml        # project config overlay (merged over user config)
  collections/            # ansible_collections/ + roles/ + group_vars/ (search path)
  skills/                 # *.md project skills (prepended to skill search path)
  templates/              # *.j2 project prompt templates (optional)
  mcp_servers/            # *.yml project MCP server defs (optional)
  binaries/               # project-pinned tool binaries (osquery/rg/...) (optional)
  weights/                # benchmark/scoring export (portable learned state)
  gludd.db                # optional per-project SQLite DB
  filestore/              # optional per-project filestore overlay
  archive/                # full archive export target
```

## Grounding facts (verified by research)

- **Config chokepoint:** `daemon.py::load_startup_config()` (`daemon.py:81-180`),
  specifically the `if config_dir is None:` block (lines 94-107) which searches
  `~/.config/general-ludd`. `config_dir` flow: `--config-dir` (cli.py:743) →
  `GLUDD_CONFIG_DIR` env (cli.py:2559, daemon.py:1464) → `create_daemon_app`
  (daemon.py:1454) → `load_startup_config` (daemon.py:1502) → EventLoop via
  `app.state._startup_config`. **No walk-up / project precedence exists today.**
  The only relative-cwd reader is `config/loader.py::load_agent_config` (reads
  `.general-ludd/agent_config.yml`, agent-prefs only, not walked up).
- **DB is already relocatable:** `db/session.py::get_default_db_path` (24-29)
  honors `GLUDD_DB_PATH` then `XDG_DATA_HOME`. `GLUDD_DB_PATH=.gludd/gludd.db`
  gives a per-project DB today (alembic upgrade head initializes any sqlite URL).
- **FileStore:** `filestore/store.py` root `~/.local/share/general-ludd/filestore`
  (:26) + read-only overlay `~/.config/gludd/fs` (:31). Full tree API
  (write/read/tree/list/copy/move) but **no export/import/dump/archive method**.
  `bootstrap.py::_find_dist_bundled_dir` (99-108) already probes `dist/binaries/`
  — add `.gludd/binaries/` as a candidate.
- **Collections/skills/templates search paths** (from the collections agent — see
  that map): ansible collections path, skill registry `discover_skills(*paths)`
  (`skills/registry.py`), prompt registry single `_template_dir`.
- **Weight/benchmark export: net-new.** Only learned state is `BenchmarkResultModel`
  (`db/models.py:624-669`, project-aware via `project_id`), DB-only, no serializer.
- **Archive/backup: net-new.** Only `make dist` (Makefile:1942-1971) exists and it
  bundles CODE+binaries only (explicitly NOT the DB/filestore/benchmark data).
- **Live-reload:** no file watcher anywhere; demand-driven via `POST /admin/reload`
  → `reload/hot_reloader.py::HotReloader.reload(scope)`. PromptRegistry.refresh()
  ✅ wired; SkillRegistry.refresh() exists but **unwired** in `_reload_skills`
  (hot_reloader.py:510 only fires an event); UserConfig is **load-once**
  (no endpoint re-runs load_startup_config); MCP/Agent registries load-once
  (AgentRegistry is sealed). EventLoop holds **live refs** to prompt/skill
  registries (in-place mutation visible) but a **frozen copy** of config.

## Build plan (phased)

**Phase 1 — discovery + config overlay.**
- New `config/project_dir.py::find_project_gludd_dir(start=cwd) -> Path | None`
  (walk up to first ancestor with `.gludd/`). Add `GLUDD_PROJECT_DIR` env override.
- Extend `load_startup_config`: if a project `.gludd/general-ludd.yml` exists, load
  it and DEEP-MERGE over the user config (project wins). Record the project dir on
  `app.state._project_gludd_dir`.
- Tests: walk-up finds nearest `.gludd/`, merge precedence, env override, absent → None.

**Phase 2 — collections + skills + templates search paths.**
- Prepend `.gludd/collections/` to the ansible collections path (AnsibleRunnerAdapter
  / runner env `ANSIBLE_COLLECTIONS_PATH`), `.gludd/collections/group_vars` to vars.
- Prepend `.gludd/skills/` to `SkillRegistry` search paths (registry supports
  `project_id` scoping — ideal for repo scope).
- Add `.gludd/templates/` as a second prompt-template search dir.
- Tests: a project role/skill/template shadows/augments the user/default one.

**Phase 3 — live reload.**
- Fix `HotReloader.__init__` + `routers/reload.py:67` to pass `skills_dirs` +
  `model_gateway`; call `skill_registry.refresh(search_paths=[.gludd/skills], project_id)`
  inside `_reload_skills`. (Two-line unlock per the reload map.)
- New `POST /admin/config/reload`: re-run `load_startup_config(config_dir)` (incl.
  project overlay), update `app.state._startup_config`, MERGE deltas in place into
  `app.state.event_loop.config`, fire `config_reloaded`.
- Tests: editing `.gludd/skills/*.md` then POST /admin/reload reflects live; config
  reload merges without restart.

**Phase 4 — portable backup: weight export + full archive.**
- `filestore/store.py`: add `export_to(dest)` / `import_from(src)` (walk `tree("/")`).
- `BenchmarkRepository.export_to_json()` → `.gludd/weights/benchmark_export.json`
  (the "weight export"); `import_from_json()` to restore.
- New `make gludd-archive` / `make gludd-restore`: bundle per-project DB +
  filestore + benchmark export into `.gludd/archive/<ts>.tar.gz` (+ sha256).
  Distinct from `make dist` (code-only).
- `bootstrap.py`: mirror `sync_bundled_to_filestore` reverse (filestore → `.gludd/`).
- Tests: archive round-trips DB+filestore+weights; restore rehydrates a clean tree.

## Risks / decisions
1. **Merge semantics** for `general-ludd.yml` — deep-merge with project-wins, but
   list fields (rules/queues) need a documented merge rule (replace vs append).
2. **DB-per-project vs shared** — `GLUDD_DB_PATH=.gludd/gludd.db` is opt-in; default
   stays shared. Archive export must snapshot whichever is active (WAL checkpoint
   before copy).
3. **Security** — never serve binaries from a project overlay without the same
   invariant FileStore enforces (store.py:42). Project `.gludd/` is repo-trusted;
   document that a malicious repo's `.gludd/collections` could run arbitrary
   ansible — gate behind the existing approval/role policy.
4. **Sealed AgentRegistry** — project `.gludd/agents/` is out of scope for v1
   (needs an unseal path); collections/skills/templates/config cover the request.
