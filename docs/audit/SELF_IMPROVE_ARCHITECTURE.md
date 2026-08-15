# Self-Improvement Architecture Audit (read-only)

Date: 2026-07-06
Scope: how gludd's self-improvement pipeline produces, lands, and reloads code changes today.

## TL;DR

There are **two parallel self-improvement pipelines**, both of which write
**directly into the running daemon's own source tree**. There is **no
"is this gludd itself?" detection** and **no routing through a registered
Project workspace**. The same `git_automation.GitAutomation` /
`materialize_project_workspace` machinery that handles *external* projects is
bypassed entirely when gludd improves itself.

---

## 1. Current self-improve flow (text diagram)

Two distinct entry points feed a shared backlog; both ultimately mutate the
daemon's live source tree.

```text
                       ┌─────────────────────────────────────────────┐
                       │  ENTRY POINTS                               │
                       └─────────────────────────────────────────────┘

   (A) Periodic gap analysis (F8 harness)         (B) Operator "update gludd:" request
       event_loop/loop.py::_phase_self_improve         POST /admin/self-update/{plan|enqueue}
       every N ticks                                   OR POST /admin/self-improve/apply
            │                                                │
            ▼                                                ▼
  SelfImprovementHarness.run_gap_analysis          self_update.classifier.classify(NL text)
   (src/general_ludd/self_improve/harness.py)       (src/general_ludd/self_update/classifier.py)
            │                                                │
            │  + chronic-blocker ingest                     │  → SelfUpdatePlan{subsystem,
            │  + grinding_detector todos                    │     change_kind, apply_tier,
            ▼                                                │     target_files, requires_approval}
  generate_fix_todos() → list[dict]                         │
            │                                                │
            ▼                                                ▼
  _persist_self_improve_todos()                    to_todo_spec() → TodoRepository.create()
   stamped work_type="self_improve"                 (priority.py; queue="self_update")
   parked in APPROVAL_REQUIRED by default          ┌────────────────────────────────┐
   (SelfImproveGate.auto_queue=False)              │ apply ladder (self_update/apply │
            │                                     │ .py::apply_plan)                │
            │  human releases via                 │  tier ladder:                  │
            │  /admin/self-improve/approvals      │   CONFIG  → auto-apply         │
            │  {approve|reject}                   │   SCAFFOLD → auto-apply        │
            ▼                                     │   CODE    → needs approval     │
  TodoRepository: APPROVAL_REQUIRED → QUEUED       │   *       → protected-path     │
            │                                     │              REFUSED            │
            ▼                                     └────────────────────────────────┘
  Worker claims the QUEUED todo (normal                 │
  backlog flow) — work happens in the                   │
  *project workspace* bound to the todo's               │
  project_id                                            │
                                                        ▼
                       ┌─────────────────────────────────────────────┐
                       │  CODE-TIER APPLY PATH                        │
                       └─────────────────────────────────────────────┘
  event_loop/loop.py::_apply_self_update_code  (lines 2607-2746)
   reads `module:<name>` + `candidate:<path>` tags from the todo
   arms SelfImprovementWorkflow.set_code_target(module, candidate_path,
                                                 health_check=_make_daemon_health_probe())
   fires reload_if_needed() →
            │
            ▼
  reload/self_improve.py::SelfImprovementWorkflow.reload_if_needed
   delegates to:
            │
            ▼
  reload/hot_reloader.py::HotReloader.reload_code_module   ◄── THE LIVE SWAP
   1. check_self_modification(live_path, role)            (guard)
   2. read live module bytes (snapshot for rollback)
   3. verify candidate sha256 if expected_sha256 supplied  (authenticity)
   4. resolve_apply_bytes (3-way merge if base_source_path supplied)
   5. tmpfile + os.replace(resolved_bytes, live_path)     ◄── WRITES TO LIVE TREE
   6. invalidate_caches + importlib.reload(module)
   7. health_check() → on failure, _restore_module_bytes rolls back
            │
            ▼
  TodoRepository.transition(todo_id, COMPLETE | FAILED)

                       ┌─────────────────────────────────────────────┐
                       │  CONFIG-TIER APPLY PATH                      │
                       └─────────────────────────────────────────────┘
  routers/self_improve.py::_apply_approved_config_change  (line 200)
   workspace_root = Path.cwd()                            ◄── DAEMON'S CWD
   safe_writer   = AtomicSafeWriter(workspace_root=workspace_root, recorder=...)
   applier       = UpdateApplier(writer=safe_writer,
                                  capability_checker=_ConfigTierCapabilityChecker(),
                                  workspace_root=workspace_root)
   applier.apply(plan, change_content)
            │
            ▼
  self_update/applier.py::UpdateApplier.apply
   1. capability gate, workspace-confinement gate, protected-path deny-list
   2. yaml.safe_load validation
   3. snapshot prior bytes
   4. writer.write(str(resolved), change_content)         ◄── WRITES TO LIVE TREE
   5. post-write yaml.safe_load re-validation, rollback on failure
            │
            ▼
  self_update/safe_writer.py::AtomicSafeWriter.write
   tmpfile → os.replace(tmp, target)                     ◄── ATOMIC SWAP
   (optional) ChangeRecordStore.record(file_path, old, new)
```

## 2. Where code changes land today

**Both code-tier and config-tier self-improvements write to the daemon's
running source tree.** Nothing in the path consults the Project registry or
materializes a workspace checkout.

| Path                                   | What it writes                            | Target root                                |
| -------------------------------------- | ----------------------------------------- | ------------------------------------------ |
| `_apply_approved_config_change`        | config/yaml edits                         | `Path.cwd()` (routers/self_improve.py:241) |
| `_apply_self_update_code` (code tier)  | live `.py` leaf module swap + reload      | live module's `__file__` (sys.modules)     |
| `SelfImprovementHarness.repo_root`     | (read-only scan, no write)                | defaults to `os.getcwd()`                  |
| `materialize_project_workspace`        | (used for *external* projects only)       | `<base_dir>/<project_id>/repo/`            |

The `_phase_self_improve` phase in `event_loop/loop.py:3444` explicitly
calls `_resolve_repo_root(self._tick_project_id)` (line 3458) so the
**read-only** gap analysis targets the *current tick's* project checkout
rather than gludd's own tree — but the **write path** for self-improvement
todos is just normal backlog execution: a worker picks up the QUEUED todo
and runs against whatever `project_id` is stamped on it.

For self-update (`/admin/self-update/*` and the `/admin/self-improve/apply`
code/config tier), there is no `project_id` routing at all — the write is
anchored to `Path.cwd()` or the live module path. **That is "patch internal
code directly."**

## 3. Project-vs-self routing — does it exist?

**No.** Searches against `gludd_repo|self_hosted|dogfood|self_target|is_self|
self_repo|GLUDD_SELF_REPO|register_self_project` return:

- `src/general_ludd/dogfood/` — a smoke-test harness (`DogfoodRunner`,
  `DogfoodValidator`) for the `make dogfood` target. Not a self-targeting
  mechanism for improvements; it runs a playbook syntax check and reports
  bypass findings. (`dogfood/orchestrator.py::run_smoke_and_validate`)
- `db/feature_seed.py` — feature-registry rows describing the dogfood
  scripts. Cosmetic.

There is no flag, env var, project_id sentinel, or detector that distinguishes
"this self-improvement targets gludd itself" from "this self-improvement
targets a user project." The harness's `repo_root` is whatever cwd the
daemon was launched in; the write path anchors to that same cwd.

The `ProjectManager` / `materialize_project_workspace` path
(`src/general_ludd/projects/manager.py:277`) **does** clone an external
repo via `GitAutomation.clone(safe_url, repo_dir)` into a per-project
workspace at `/tmp/gludd-workspaces/<project_id>/repo/` — but nothing in
`self_improve/`, `self_update/`, or the `/admin/self-improve/*` /
`/admin/self-update/*` routes calls it.

## 4. Hot-reload trigger mechanism

Two distinct reload triggers, one per tier:

### Config tier (routers/self_improve.py::_apply_approved_config_change)

1. `AtomicSafeWriter.write(path, content)` does same-dir `tempfile.mkstemp`
   → `os.fsync` → `os.replace(tmp, target)` (atomic on POSIX).
2. Optional `recorder` hook fires — records `(path, old_bytes, new_text)`
   to `ChangeRecordStore` (used by `core-changes list/commit`).
3. Post-write `yaml.safe_load(target.read_text())` re-validates; rollback
   on parse failure via `_restore_snapshots`.
4. **No explicit reload signal.** The daemon's `HotReloader` watches the
   `config_dir` and the file-swap is picked up on the next reload sweep.
   `SelfImprovementHarness.write_config_value` (line 345) optionally
   accepts an injected `reloader` and calls
   `reloader.reload(ReloadScope.CONFIG)` immediately.

### Code tier (HotReloader.reload_code_module)

The explicit reload chain (file: `reload/hot_reloader.py:117`):

1. Resolve `module = sys.modules[module_name]` (or `importlib.import_module`
   fallback).
2. Read `live_path = module.__file__`.
3. `check_self_modification(live_path, role)` — capability-lattice guard.
4. `original_bytes = live_path.read_bytes()` (rollback snapshot).
5. Read candidate bytes; if `expected_sha256` supplied, constant-time
   compare before any write (task #20 authenticity gate).
6. `_resolve_apply_bytes`: 3-way merge of base/live/candidate if
   `base_source_path` supplied; refuse on overlap; otherwise verbatim.
7. **`tmpfile.write_bytes(resolved)` → `os.replace(tmp, live_path)`** ←
   this is the on-disk write.
8. `_invalidate_source_cache(live_path)` — bumps mtime + unlinks the
   `.pyc` + `importlib.invalidate_caches()`.
9. **`importlib.reload(module)`** ← this is the live-process reload.
10. `health_check()` gate (default: `_make_daemon_health_probe` reading
    `app.state._degraded`). Failure → `_restore_module_bytes` swaps the
    snapshot back, reloads again, returns `rolled_back=True`.

`ReloadManager.execute_reload` (reload/manager.py:64) is the legacy
in-memory bookkeeping path; since BUG#2 it explicitly returns `status="no_op"`
and refuses to claim success — only `HotReloader.reload_code_module` is
authoritative for code-tier reloads.

---

## 5. Files that must change to route self-improvements to a gludd project workspace

Today the assumption "self-improvement target == running daemon's tree" is
encoded in five specific places. Each would need an explicit routing
decision to send self-improvements to a *registered Project* (e.g. a
"gludd-self" project whose `repo_url` is gludd's own repo and whose
`workspace_path` is a checked-out clone, NOT the running tree).

1. **`src/general_ludd/routers/self_improve.py:241`**
   `_apply_approved_config_change` hardcodes `workspace_root = Path.cwd()`.
   This is the single line that anchors every config-tier self-improvement
   write to the daemon's source tree. Needs: resolve `workspace_root` from
   a self-project registry lookup (e.g.
   `ProjectManager.get_workspace("gludd-self").repo_dir`).

2. **`src/general_ludd/event_loop/loop.py:2607-2746`** (`_apply_self_update_code`)
   Arms `SelfImprovementWorkflow.set_code_target(module_name, candidate_path)`
   where `module_name` resolves via `sys.modules` — i.e. the *live* module.
   To route through a workspace instead, this would need to write the
   candidate to `<workspace>/src/general_ludd/<module>.py` and dispatch a
   *separate* worker (or `git_automation.GitAutomation.commit` + open_pr)
   rather than `importlib.reload`-ing the running process.

3. **`src/general_ludd/self_improve/harness.py:43`**
   `SelfImprovementHarness.__init__` defaults `repo_root = repo_root or os.getcwd()`.
   For the F8 periodic path this is corrected in
   `event_loop/loop.py:3458` (`_resolve_repo_root(project_id)`), but the
   `/admin/self-improve/analyze` and `/admin/self-improve/run` routes in
   `routers/self_improve.py:307-336` construct `SelfImprovementHarness()`
   with no `repo_root` — so admin-triggered scans still target the cwd.

4. **`src/general_ludd/reload/self_improve.py:45` (`set_code_target`) and
   `reload/hot_reloader.py:117` (`reload_code_module`)**
   The contract "candidate bytes are swapped over the live `__file__` +
   `importlib.reload`" IS the "patch internal code directly" behavior. To
   route to a project workspace, `reload_code_module` would need a workspace
   flag that defers the swap to a worker (or to `git_automation.commit +
   push`) instead of `os.replace` + `importlib.reload` in-process.

5. **`src/general_ludd/self_update/classifier.py:262` (`classify`) and
   `self_update/priority.py:63` (`to_todo_spec`)**
   Neither carries a `project_id`. `to_todo_spec` accepts an optional
   `project_id` kwarg, but `routers/self_update.py:99` only forwards one
   when the *request payload* includes it. There is no default mapping
   ("self-update → gludd-self project"). Needs: a `SELF_PROJECT_ID`
   constant + auto-stamp on every self-update todo.

**Bonus (config, not code):** a new daemon-startup step that registers
gludd as a Project — calls `persist_project(project_id="gludd-self",
repo_url=<gludd's own origin URL>, workspace_path="gludd-self")` and
runs `materialize_project_workspace` so there is a fresh clone to route
to. Today this registration does not exist.

## 6. Cross-references

- F8 human-approval gate: `self_improve/gate.py`, `self_improve/approval.py`
- Recurring-failure ingest: `self_improve/harness.py:130` (`_check_recurring_failures`)
- Outcome-driven learning: `self_improve/outcomes.py` (`OutcomeAnalyzer`)
- Cross-cycle de-dup: `self_improve/dedup.py` (`SelfImproveDeduplicator`)
- Grinding detection: `self_update/grinding_detector.py`
- Dogfood (unrelated smoke test): `dogfood/orchestrator.py::run_smoke_and_validate`
- External-project workspace materialization: `projects/manager.py:277` (`materialize_project_workspace`)
- Git automation used by external projects only: `git_automation/repo.py::GitAutomation.clone`

---

## Summary (3 bullets)

- **Two pipelines, same target.** F8 `SelfImprovementHarness` (gap-analysis
  todos via the normal backlog) and `self_update` (`"update gludd:"` requests
  via `/admin/self-update/*` and `/admin/self-improve/apply`) both land
  changes in the running daemon's source tree — config writes via
  `AtomicSafeWriter` anchored at `Path.cwd()`, code writes via
  `HotReloader.reload_code_module`'s in-process `os.replace` +
  `importlib.reload`.
- **No self-detection, no project routing.** There is no `gludd_repo` /
  `is_self` / `self_target` flag anywhere; `SelfImprovementHarness.repo_root`
  and `_apply_approved_config_change`'s `workspace_root` both default to the
  daemon's cwd. The `materialize_project_workspace` / `GitAutomation.clone`
  path used for external projects is never invoked for self-improvement.
- **To route self-improvements to a gludd project workspace instead, five
  files must change** — the workspace anchoring in `routers/self_improve.py`
  and `event_loop/loop.py::_apply_self_update_code`, the
  `SelfImprovementHarness` default `repo_root`, the
  `reload_code_module`/`set_code_target` "swap live module" contract, and
  the `to_todo_spec` project-id stamping — plus a new daemon-startup step
  that registers gludd itself as a Project and clones its workspace.
