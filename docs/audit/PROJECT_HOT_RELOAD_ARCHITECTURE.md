# Project Registration, Workspace Routing & Hot-Reload Architecture

**Scope:** audit of how gludd (a) registers projects and routes per-tick work
into the right workspace, and (b) hot-reloads running code — plus the gap
analysis for the specific feature:

> *"When self-improvement targets a project that IS gludd itself, run the work
> in that project's workspace and hot-reload the running process after commit."*

All file references are relative to repo root unless noted.

---

## PART A — Project Registration & Workspace Routing

### 1. How a project is registered

Entry point: `POST /admin/projects` in `src/general_ludd/routers/projects.py:66`.

```python
admin_add_project(req: AddProjectRequest)
  → ext["projects"].add_project(...)            # in-memory ProjectWeight
  → asyncio.to_thread(materialize_project_workspace, repo_url, workspace_path)
  → persist_project(repo, ...)                  # DB row (ProjectModel)
  → session.commit()
```

Three layers of state are written:

| Layer | Object | Where | Survives restart? |
|---|---|---|---|
| In-memory registry | `ProjectManager._projects[project_id] → ProjectWeight` | `src/general_ludd/projects/manager.py:125` | No |
| Filesystem workspace | `/tmp/gludd-workspaces/<workspace_path>/repo/…` | `materialize_project_workspace` (`manager.py:277`) clones `repo_url` into `ProjectWorkspace.repo_dir` | Yes (on disk) |
| Database row | `ProjectModel` (config JSON holds `repo_url`/`weight`/`dispatch_mode`) | `persist_project` (`manager.py:339`) + `rebuild_manager_from_db` (`manager.py:381`) on startup | Yes |

The `ProjectWeight` dataclass (`projects/manager.py:106-117`) is the in-memory
spine record — `project_id`, `name`, `weight`, `repo_url`, `workspace_path`,
`dispatch_mode`, `active`. **There is no `is_self` / `is_gludd_itself` field.**
Weights are capped at 100% total (`add_project`, `manager.py:138`) and selected
by weighted random in `ProjectManager.select_project` (`manager.py:234`).

### 2. Per-project working directory (worktree)

**Yes — each project gets a fully isolated filesystem workspace.**
`ProjectWorkspace` (`src/general_ludd/projects/workspace.py:46`) creates, under
`/tmp/gludd-workspaces/<project_id>/`:

```text
repo/           ← git clone of project.repo_url lands here
artifacts/  logs/  config/  runner/  playbooks/  templates/  roles/
```

`confine_workspace_path` (`workspace.py:10`) rejects absolute paths and `..`
traversal so an untrusted `workspace_path` cannot escape `base_dir`. The repo is
cloned by `GitAutomation.clone` with SSRF/RCE/option-injection guards
(`git_automation/repo.py:123` `reject_unsafe_repo_url`, `:173`
`_reject_clone_url`).

### 3. How dispatch decides WHICH project workspace to work in

Two-step per tick (`src/general_ludd/event_loop/loop.py`):

1. **Project selection** — `_select_tick_project_id` (`loop.py:781`) calls
   `ProjectManager.select_project()` (weighted random over `dispatch_mode ==
   "active"` projects) and stores the result on `self._tick_project_id`. When
   the selected project changes, `_rebuild_ansible_env_for_project` re-resolves
   the 3-tier Ansible collections path
   (PROJECT → USER → BUNDLED) against that project's `repo_dir`.

2. **Repo-root resolution** — `_resolve_repo_root(project_id)` (`loop.py:944`)
   is the single chokepoint that maps a `project_id` to a working directory:

   ```text
   priority 1: self._project_workspace[project_id].repo_dir   (the clone)
   priority 2: self.config["repo_root"]                        (daemon cwd fallback)
   priority 3: None → completion_verifier fail-closes
   ```

   `_resolve_project_root_for_collections` (`loop.py:790`) is the sibling used
   for Ansible content resolution (same lookup, falls back to workspace `root`
   instead of the config default).

The dispatched `JobSpec` carries `project_id` (`loop.py:915-922`), and the
git-delivery / completion-verification paths resolve the working tree through
`_resolve_repo_root`. The execution engine (`execution/engine.py`) takes a
`workspace_path` argument (`:122`, `:162`, `:280`) — that is the path that ends
up in the `repo_dir` of the selected project.

### 4. What happens when `repo_url == git@github.com:sandboxcom/gludd.git`

**Nothing special. This is the core finding.** Grep across `src/` for
`gludd\.git`, `self_hosted`, `self-hosted`, `is_self`, `is_gludd_itself`,
`sandboxcom/gludd` returns **zero matches** (the only "self-hosted" hit is an
unrelated Cloudflare comment in `connectors/cloudflare.py:83`).

Concretely, if an operator registers a project with
`repo_url=git@github.com:sandboxcom/gludd.git`:

1. `materialize_project_workspace` clones it into
   `/tmp/gludd-workspaces/<id>/repo/` — a **disjoint checkout** from the
   running process's own installed package (`/Users/shawnwilson/gludd` or the
   venv site-packages).
2. `_phase_self_improve` (`loop.py:3444`) constructs
   `SelfImprovementHarness(repo_root=_resolve_repo_root(tick_project_id))` —
   i.e. gap analysis scans the **clone**, not the live process source.
3. Generated todos are dispatched and worked in the clone; commits land in the
   clone's `repo/` branch.
4. **The running gludd process never sees those commits.** Its imported
   `general_ludd.*` modules are unchanged, its `app.state.event_loop` is
   unchanged, and no reload is triggered. The improvement is "applied" to a
   detached sandbox copy that the process itself does not execute.

This is the architectural gap the requested feature would close.

---

## PART B — Hot-Reload Mechanism

### 1. What triggers a hot reload

**Explicit HTTP call only. There is no automatic file-watch trigger and no
SIGHUP handler for code reload.**

| Surface | Triggers reload? | Evidence |
|---|---|---|
| `POST /admin/reload` `{scope: "all\|"models\|"templates\|"playbooks\|"skills\|"config"}` | **Yes** (config/assets) | `src/general_ludd/routers/reload.py:73-103` |
| `POST /admin/config/reload` | **Yes** (live-mutable config keys merged into `event_loop.config`) | `reload.py:105-147` |
| File change on disk (watchdog) | **No** — `watchdog` is used by `integrity/scanner.py` and `worktree/core.py`, neither of which calls `HotReloader` | `grep watchdog src/general_ludd/reload/` → 0 |
| `SIGHUP` to the daemon | **No** — `SIGHUP` appears in `process/registry.py:53` (a signal-name list) and `worker/gunicorn_conf.py:16` (Gunicorn *worker* reload, not gludd code) | no handler binds `HotReloader.reload` to a signal |
| Git post-commit hook | **No** — `GitAutomation.commit` (`git_automation/repo.py:303`) runs `git add -A && git commit -m`; no hook callback into the reloader | |
| `SelfImprovementWorkflow.reload_if_needed` | **Only when a single leaf module is pre-armed via `set_code_target`** | `reload/self_improve.py:126-170` |

So a reload has to be *asked for*. Nothing in the daemon observes a file change
and fires one.

### 2. What gets reloaded

Two distinct code paths in `src/general_ludd/reload/hot_reloader.py`:

**(a) `HotReloader.reload(scope)` — config & assets only** (`hot_reloader.py:91`):

| Scope | What it does |
|---|---|
| `MODELS` / `CONFIG` | Parses `config/model_routing.yml`, pushes profiles into `ModelGateway` (`_reload_models`, `:466`) |
| `TEMPLATES` | Globs `*.j2` from `templates_dir`, calls `PromptRegistry.refresh()` (`_reload_templates`, `:524`) |
| `PLAYBOOKS` | Globs `*.yml` from `playbooks_dir`, emits `PlaybookRegisteredEvent` / `PlaybookRemovedEvent` (`_reload_playbooks`, `:535`) |
| `SKILLS` | Globs `*.md` from `skills_dirs`, calls `SkillRegistry.refresh()` (`_reload_skills`, `:551`) |

**It does NOT reload gludd's own Python code.** No `importlib.reload`, no
`sys.modules` mutation.

**(b) `HotReloader.reload_code_module(...)` — single leaf-module hot rotation**
(`hot_reloader.py:117`):

This is the only path that swaps live Python. Steps (all fail-closed):

1. `check_self_modification(live_path, role)` — refuses protected guardrail /
   policy files; requires `collections_self_modify` capability for collections
   paths (`security/capability_lattice.py`).
2. Snapshot live bytes → rollback buffer.
3. Authenticity gate: if `expected_sha256` supplied, verify candidate hash
   (constant-time) before any write (`hot_reloader.py:237`).
4. Anti-clobber 3-way merge via `integration.safe_merge` when `base_source_path`
   supplied — overlapping divergence refuses, disjoint merges (`:327-379`).
5. `os.replace` merged/candidate bytes over the live `__file__` +
   `importlib.reload(module)` + source-cache invalidation (`:423-444`).
6. **Health gate is REQUIRED** — no `health_check` ⇒ rollback + refusal
   (`:295-304`). Health fail ⇒ rollback + verified restore (`:312-321`,
   `:381-421`).

This method is invoked only by `SelfImprovementWorkflow.reload_if_needed`
(`reload/self_improve.py:142`) and only when `set_code_target(...)` was called
first to arm exactly one `(module_name, candidate_source_path)` pair. There is
**no multi-module / post-commit batch reload** entry point.

> Note: `reload/manager.py` (`ReloadManager.execute_reload`) is deliberately a
> no-op (`manager.py:74-94`, "BUG#2 FAIL-OPEN FIX") — it records bookkeeping
> only and returns `status="no_op"`. It is not a real reload path.

### 3. Is `POST /admin/reload` the endpoint?

Yes, plus its siblings. Full surface in `routers/reload.py`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/reload` | `HotReloader.reload(scope)` — assets/config |
| POST | `/admin/config/reload` | Re-read `load_startup_config`, merge live-mutable keys into `event_loop.config` |
| GET | `/admin/reload/status` | Last 20 events from the event-bus history |
| POST | `/admin/templates/refresh` | `PromptRegistry.refresh()` directly |
| POST | `/admin/playbooks/refresh` | `AnsibleRunnerAdapter.refresh_playbooks()` directly |

There is **no** `POST /admin/reload/code` or similar endpoint that hot-swaps a
live Python module — that capability is only reachable through
`SelfImprovementWorkflow.set_code_target` + `reload_if_needed`, which is an
internal Python API, not an HTTP one.

---

## The Feature Gap — Files/Functions That Must Change

**Feature statement:** *When self-improvement targets a project that IS gludd
itself, run the work in that project's workspace and hot-reload the running
process after commit.*

Five changes are required. Ordered by dependency.

### Change 1 — Add "is this project gludd itself?" detection
**File:** `src/general_ludd/projects/manager.py` (new helper) + `ProjectWeight`
dataclass (`manager.py:106`).

- Add `is_self: bool = False` to `ProjectWeight`.
- New helper `detect_self_project(repo_url, workspace_path) -> bool` that
  compares the project's `repo_url` (and/or the clone's `git remote get-url
  origin`) against the running process's own package root
  (`os.path.dirname(general_ludd.__file__)` walked up to its `.git`). Normalize
  both sides (strip trailing `.git`, lower-case host, drop `git@` scp prefix)
  before compare.
- Set the flag in `add_project`, `seed_from_config`, and
  `rebuild_manager_from_db` so it survives restarts.
- **Why here:** every downstream branch (`_phase_self_improve`, the commit
  path, the reloader) needs a single cheap boolean, not a re-derive each tick.

### Change 2 — Route self-improvement work into the self-workspace (mostly done) + tag todos
**File:** `src/general_ludd/event_loop/loop.py` `_phase_self_improve`
(`loop.py:3444`) and `_persist_self_improve_todos`.

- `_resolve_repo_root(tick_project_id)` already points the
  `SelfImprovementHarness` at the clone — so gap analysis already runs in the
  right workspace. **This part works.**
- The missing piece: stamp the generated todos with `target_project_id` AND a
  `self_modification: bool` flag so the commit path (Change 3) and the reload
  path (Change 4) can branch. Today `_persist_self_improve_todos` carries
  `project_id` but no self-flag.

### Change 3 — Post-commit hook: trigger reload when a self-project commit lands
**File:** `src/general_ludd/event_loop/loop.py` git-delivery / completion path
(the code around `_resolve_repo_root` usage at `:944` and where
`GitAutomation.commit` results are recorded — search `loop.py` for
`commit_sha`).

- After a commit succeeds in a workspace whose project has `is_self=True`,
  compute the changed-file set (already available via
  `GitAutomation.changed_files`, `git_automation/repo.py:367`, or
  `lines_changed_in_commit`) and hand it to the new batch-reload entry point
  (Change 4).
- Must be dispatched to a worker thread (`asyncio.to_thread`) so the event loop
  is not frozen during `importlib.reload` + health-gate polling.
- Must respect the existing `check_self_modification` guardrail
  (`security/capability_lattice.py`) so a self-improvement commit cannot
  silently swap a protected guard file — the guard already exists in
  `reload_code_module` but the **caller** must pass the acting `role`.

### Change 4 — Batch multi-module hot reload
**File:** `src/general_ludd/reload/hot_reloader.py` — add
`reload_changed_modules(repo_dir, changed_paths, health_check, role)`.

- Today `reload_code_module` (`hot_reloader.py:117`) handles **one** module per
  call. `SelfImprovementWorkflow.set_code_target`
  (`reload/self_improve.py:45`) arms **one** `(module_name,
  candidate_source_path)`. A commit typically touches many files.
- New method walks `changed_paths`, maps each repo-relative `src/...py` to
  `(module_name, live_path)` (mirroring the `sys.modules` ↔ `__file__` lookup
  already in `reload_code_module`), and hot-rotates each in dependency order
  (leaf-first), reusing the existing authenticity / anti-clobber / health /
  rollback machinery verbatim.
- A single health gate at the END of the batch (not per-module) — if it fails,
  roll back every module that was swapped in this batch (transactional).

### Change 5 — Expose the batch reload over HTTP + wire into `SelfImprovementWorkflow`
**Files:** `src/general_ludd/routers/reload.py` (new `POST /admin/reload/code`
endpoint) + `src/general_ludd/reload/self_improve.py`
(`SelfImprovementWorkflow`, currently `:45` `set_code_target`).

- `SelfImprovementWorkflow.set_code_target` must accept a **list** of
  `(module_name, candidate_source_path, expected_sha256)` triples (or a repo
  dir + changed-paths list) instead of a single pair, then call the new
  `reload_changed_modules` from Change 4 inside `reload_if_needed`
  (`self_improve.py:126`).
- New `POST /admin/reload/code` endpoint in `routers/reload.py` so an operator
  (or the post-commit hook from Change 3) can request a code reload without
  going through the self-improve workflow. Body: `{repo_dir, changed_paths,
  health_endpoint}`. Reuses the same `HotReloader` instance the daemon already
  builds (`reload.py:87`).

### (Optional) Change 6 — Health gate for self-reload
**File:** new `health` callable, likely in `src/general_ludd/daemon.py` next to
the existing `/readyz` handler.

- `reload_code_module` requires a `health_check: Callable[[], bool]` and
  fail-closes (rollback) when it returns False or is None (`hot_reloader.py:295`).
- The self-reload batch needs one that polls `/readyz` (which already reflects
  `app.state._degraded`) after the swap. Today no such callable is wired for
  the post-commit case — only the test/standalone
  `SelfImprovementWorkflow` path supplies one.

---

## Summary of the Gap

| Question | Answer |
|---|---|
| Per-project workspace? | **Yes** — `ProjectWorkspace` clones each project into `/tmp/gludd-workspaces/<id>/repo/` |
| Self-hosted gludd detection? | **No** — zero matches in `src/`; a gludd project is treated like any external repo |
| Hot-reload trigger? | **Explicit only** — `POST /admin/reload` (config/assets) or `SelfImprovementWorkflow.reload_if_needed` (single pre-armed module). No file-watch, no SIGHUP, no post-commit hook. |
| What reloads? | Config + templates + playbooks + skills via `reload(scope)`; **one leaf module** via `reload_code_module`. No multi-module batch. |
| Does a self-improvement commit on the gludd project reach the running process? | **No.** Commits land in the detached clone; the live `general_ludd.*` imports are never swapped and never reloaded. |

The five changes above close the loop: **detect → route → commit → batch-reload
→ health-gate**, reusing the existing `check_self_modification` guard,
`safe_merge` anti-clobber, and `_restore_module_bytes` rollback machinery
rather than reinventing them.
