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

## Merge snapshot integrity

Implemented and re-verified on 2026-08-20. `merge_config()` returns a detached
configuration graph: mutable mappings and lists in the result do not alias either
input. The project layer still wins, mappings still merge recursively, and lists
still replace wholesale. This makes idempotence durable across later state changes,
not merely equal at the instant the merge returns.

Practitioner and upstream evidence reviewed on 2026-08-20:

- A [Python practitioner report from 2022-06-11](https://stackoverflow.com/questions/72587500/python-merging-nested-dictionaries-into-a-new-dictionary-and-update-that-diction)
  reproduces the same failure mode: a new top-level merged dictionary retained
  references to nested mutable inputs, so editing the result edited both sources.
- OmegaConf's [documented safe and unsafe merge split](https://omegaconf.readthedocs.io/en/latest/usage.html#omegaconf-unsafe-merge)
  makes the ownership contract explicit: safe merge preserves inputs, while its
  faster destructive merge requires callers to stop using them. Its
  [safe merge implementation](https://github.com/omry/omegaconf/blob/main/omegaconf/omegaconf.py)
  starts from a deep copy. Gludd keeps only the safe behavior because user config
  remains the rollback source if a project overlay fails validation.

ZDD follows from building and validating an independent candidate before replacing
the active config object: readers retain the previous snapshot until validation
succeeds, and rollback discards the candidate without repairing mutated source
state. The merge allocates one detached user graph plus copied project replacements,
does no I/O, and starts no processes; CPU and peak memory remain linear in the
already-loaded configuration size.

## Agent-config permission boundary

Implemented and verified on 2026-08-25. The project-local
`.general-ludd/agent_config.yml` reader treats a `PermissionError` while checking or
opening the file as an unavailable optional layer and returns a fresh default
`AgentConfig`. It never consumes content that the running identity cannot read.
The exception boundary is deliberately narrow: malformed YAML, non-mapping data,
and schema-validation errors still fail visibly, and path traversal and symlink
semantics are unchanged.

Upstream and practitioner evidence reviewed on 2026-08-25:

- Python's [upstream `pathlib` documentation](https://docs.python.org/3.14/library/pathlib.html#querying-file-type-and-status)
  records that Python 3.14 changed `Path.exists()` to return `False` for every OS
  error; earlier supported interpreters can still raise selected `OSError`
  subclasses. The loader handles `PermissionError` explicitly so Python 3.11 and
  newer runtimes share the same fail-closed application contract.
- A [Python.org practitioner discussion from 2024-03-31](https://discuss.python.org/t/handle-not-executable-directories-for-os-listdir/49978)
  demonstrates that directory search permissions can make existence and listing
  results disagree. Participants also call out the race between an existence check
  and a later open, so the regression covers both operations rather than assuming
  the check authorizes the read.
- Python's [upstream issue 35692, opened 2019-01-09](https://bugs.python.org/issue35692)
  includes a concrete `Path.exists()` `PermissionError` and the maintainers'
  distinction between inaccessible paths and absent paths. Gludd preserves that
  distinction internally but applies its documented optional-layer policy at the
  config boundary.

This is ZDD-safe because a denied optional layer produces a complete default object
without mutating any active configuration, persistent state, or file permissions.
Rollback is a code-only revert; there is no schema, data, wire, or deployment
migration. Restoring access makes the next bounded load consume the file normally.
Each attempt performs at most one metadata check and one open, adds no retry or
directory walk, allocates only the default model on denial, and starts no process.

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
