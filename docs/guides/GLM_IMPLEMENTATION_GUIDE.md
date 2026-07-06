# GLM Implementation Guide — Making General Ludd Actually Work

> **Audience:** the GLM 5.1 coding agent.
> **Author:** repository analysis pass, 2026-06-10 (revised with full subsystem audit).
> **Goal:** turn this repo from "boots and looks finished" into "a submitted todo produces real, model-driven code changes that are committed, reviewed, and reconciled."

---

## 0. How to use this document (read first, do not skip)

These rules are mechanical. Follow them literally.

1. **Work top to bottom.** Tasks are ordered by dependency. Do `G0` before `G1`, etc. Do not reorder.
2. **One task = one branch = one commit.** For each task: `make feature-start MSG='feature/g1-session-per-tick'`, do the work, prove it green, then `make git-add FILES='...'` + `make git-commit MSG='...'`.
3. **TDD is required.** For every task, write the failing test(s) FIRST, run them and watch them fail, then write code until they pass. A task is not done until its new tests pass AND the full suite is still green.
4. **Use `make` targets only.** Never invoke `uv`, `pytest`, `ruff`, or `mypy` directly. The only test/lint/type commands you may run are the `make` targets listed in Section 2. (They call `uv` internally — that is fine; you must not call `uv` yourself.)
5. **Respond and reason in English.** Keep your own reasoning short and concrete.
6. **Do not trust `SESSION.md`.** It says "ALL GAPS CLOSED." That is false. The real state is in Section 1. When `SESSION.md` and the code disagree, the code is the truth.
7. **Confirm line numbers before editing.** This document gives symbol names and approximate line numbers. Files drift. Open the file, find the symbol, edit by symbol — not by blind line number.
8. **Watch for the `# noqa: F401` pattern.** `daemon.py` imports ~25 classes (`GitAutomation`, `ReturnReviewer`, `AgentDispatcher`, `AutoBenchmarkRecorder`, `RunBudgetGuard`, `DogfoodRunner`, …) purely so they *look* wired — none are instantiated. These imports also defeat the project's own dead-code auditor (`quality/preflight.py::run_completion_audit` counts name occurrences, and the import makes the count pass). When you genuinely wire a class, remove its `# noqa`. Never add a new one.
9. **Definition of Done for every task:** new tests written and passing + `make qa` passes (lint, typecheck, test, healthcheck) + you have manually confirmed the behavior the task describes. See Section 8.

---

## 1. Ground truth — what is actually broken

The system **boots**, creates a SQLite DB, seeds queues, and spins an event loop. But the agentic spine — submit a todo → an AI edits code → the change is reviewed → the todo is reconciled — **does not work at any step**, and the daemon you start in production is not even the configured one. Every break below is verified against the code, not `SESSION.md`.

### 1.1 Critical spine breaks (the product does not function)

| ID | Symptom | Root cause (confirm exact lines yourself) |
|----|---------|-------------------------------------------|
| **C0** | The production daemon always starts **unconfigured**. No config file, no model profiles, no OpenBao, no MCP servers, no prompt templates — ever. | `cli.py::_build_daemon_start_cmd` (~L2543) spawns `gunicorn "general_ludd.daemon:create_daemon_app()"` — invoked with **no arguments**, so `config_dir=None, templates_dir=None, playbooks_dir=None`. `_cmd_daemon` builds a configured app in the parent process and **discards it** before spawning gunicorn. All `--config-dir`/`--tick-interval`/`--log-level` flags are silently dropped. Also: there is no default config search path (`~/.config/general-ludd`, `/etc/general-ludd`) despite docs claiming one. |
| **C1** | No code is ever generated. Every dispatched job runs a placeholder playbook that writes `{"status":"success"}`. | All `playbooks/*.yml` are stubs. `return_review.yml` hardcodes `decision: complete, confidence: 0.8`. The real `ModelGateway` is never called by any job. The worker (`worker/app.py`) ignores `job.prompt_text`/`model_profile` entirely and `PLAYBOOK_REGISTRY = {"noop.yml"}` rejects every real playbook with HTTP 400. |
| **C2** | The event loop claims 0 todos, dispatches 0 jobs, reconciles 0 decisions — forever. | `EventLoop.__init__` (`event_loop/loop.py` ~L178-215): when a `session_factory` is passed (the production path), `self.session = None`, so all repos are `None`. `self._session_factory` is stored but **never used in `tick()`**. Every DB phase early-returns. Additionally, the loop **never commits** — all repo writes are `flush()` only. |
| **C3** | Todos submitted by users never reach the loop. | `POST /api/todos` (`routers/todos.py` ~L37-51) appends to the in-memory list `_daemon_state["todos"]`; never calls `TodoRepository.create`. The loop reads the DB. Two disconnected stores. `/api/status` counts only the in-memory list. |
| **C4** | If a todo *were* dispatched, the event loop task would **crash and die silently**. | Production runner is `AnsibleRunnerAdapter()` with no `playbooks_dir` → registry only knows `noop.yml`; any mapped playbook (`validate_task.yml`…) makes `resolve_playbook` raise → uncaught in `_dispatch_execute_job` → `tick()` raises → `run_forever` exits. `daemon.py` creates the task with **no done-callback** (~L373), so the loop is permanently dead with no log. Also `run_playbook` discards `private_data_dir` and never feeds the written extravars to the run. |
| **C5** | Cloud GPU instances launched via `gludd compute launch` **cannot be destroyed** via the API — a real money leak. | `routers/compute.py` (~L24) builds a **fresh** `DeploymentManager` per request; each gets a new `tempfile.mkdtemp` working dir. `destroy()` (`infra/deployment.py` ~L89-99) ignores `instance_id`, uses `_last_config` (None on the fresh instance), and runs `terraform destroy` in an **empty dir with no state file**. `max_cost_usd`/`timeout_minutes` are accepted and never enforced. |
| **C6** | Daemon startup failures are invisible. | `daemon.py` lifespan wraps the entire startup (DB, engine, EventLoop, secrets, preflight) in one `except Exception: logger.warning(...)` (~L405). Any failure leaves the daemon serving HTTP with no event loop and no DB while `/healthz` still reports `healthy`. |

### 1.2 High-severity: features that look implemented but are non-functional

| ID | Symptom | Root cause |
|----|---------|-----------|
| **H1** | Benchmark/leaderboard/observability endpoints always empty; `POST /admin/benchmark/record` always 503; `gludd scores`/`leaderboard` permanently blank. | Routers read `app.state._session`; lifespan only ever sets `app.state._session_factory`. Also `BenchmarkRepository._get_session` on the factory path opens a session per call, never commits, never closes — even a fixed caller would silently lose writes. |
| **H2** | Self-improvement does nothing, twice over. | `self_improve_interval` defaults to 0 and the daemon never passes it → `_phase_self_improve` never runs. Even when invoked via router, `SelfImprovementHarness.enqueue_todos` appends to an in-memory list on a throwaway instance — "todos_enqueued: N" is reported for todos that were discarded. |
| **H3** | Worker job endpoints are fake. | `/jobs/return-review`, `/jobs/validate`, `/jobs/policy-validate`, `/jobs/reload-request` just log and return `{"status":"...dispatched"}`. |
| **H4** | The one real review implementation is dead code; failure degrades to a silent pass. | `review/reviewer.py::ReturnReviewer` genuinely calls the gateway but is never instantiated. On model failure `_call_model` returns `str(prompt)` → JSON parse fails → silently becomes `decision="ignore_duplicate"`. `review/decision_applier.py::apply_decision` (real evidence-gated logic) also has no caller. |
| **H5** | Subagent dispatch is decorative. | `agents/dispatcher.py` default executor returns `""`; class never constructed in production. |
| **H6** | The agent's work would evaporate even if generated: **no path commits, branches, pushes, or opens a PR on task completion.** | `git_automation/repo.py` is real (commit/branch/push/worktree/merge all work) but has zero production callers. Reconcile only flips a DB status. |
| **H7** | Adaptive routing has never influenced a single dispatch. | The router is wired (the only fully-connected chain) but **starved**: nothing ever records benchmark results. `AutoBenchmarkRecorder` (`observability/recorder.py`) is complete and never instantiated; `loop.py` sets `self._benchmark_recorder = None` forever. Every `route()` returns `fallback=True`. |
| **H8** | MCP is end-to-end dead despite a real stdio client. | No `MCPClient` is ever constructed in production (`daemon.py` passes `mcp_client=None, mcp_tool_registry=None`); loaded `cfg["mcp_servers"]` is never used; only `mcp_servers/example.yml` is read (hardcoded filename). Worse: even fully wired, **tools could never reach a model** — the only consumer drops tool *name strings* into Ansible job vars; `ModelGateway` has no tools parameter. `resolve_mcp_env` (the no-plaintext-secrets design) is never called at runtime. The stdio transport also never sends `notifications/initialized` and matches responses by line order, not `id`. |
| **H9** | Skills can never fire. | Discovery scans `config_dir/*.md` non-recursively while skills install to `config_dir/skills/`; no curated skill defines `trigger_patterns`, and both fetch paths **drop** `trigger_patterns` when rewriting frontmatter → `match_trigger` can never match. `JobSpec` has no `skill_body` field, so skills can't reach an HTTP worker at all. |
| **H10** | Prompts never reach a model. | `templates/prompts/*.md.j2` are real and substantive, but `templates_dir` is always `None` in production (C0) → `render()` raises → swallowed → `prompt_text=None`. `get_template_name_for_work_type` has no caller. (`autoescape=True` also HTML-escapes Markdown prompts — wrong setting.) |
| **H11** | No cost/budget limit exists anywhere. | `budget_guard=None` in production; `RunBudgetGuard` never constructed; the `budget:` section of `general-ludd.yml` has no consumer; `record_spend` can never fire. |
| **H12** | Metrics and cost tracking see zero real data. | The only writer (`ModelGateway.call_model`) is constructed in `routers/models.py` **without** `metrics_collector`. `/admin/agents`, `/admin/metrics/cost`, `/admin/metrics/report` always empty/zero. |
| **H13** | Projects: `repo_url` is write-only — **nothing ever clones a repo**; a dispatched job has no code to edit. Projects are in-memory only (DB table stays empty, restart loses all); API-added projects never get a workspace; `workspace_path` is ignored even at startup. | `projects/manager.py` (~L59 stores, never consumes), `daemon.py::_init_project_workspaces` (~L201-211, startup-only, ignores `workspace_path`), `ProjectRepository` imported noqa-only. |
| **H14** | Hot-reload is ~80% theater. | `_reload_models` checks file existence and returns `"models_reloaded": True` without parsing/applying; `ReloadManager.execute_reload` sets `status="success"` while doing nothing; EventLoop's `_on_config_reloaded` copies its own stale config; `WorkerBroadcaster` targets endpoints that don't exist on the worker and has no registration path. Only template reload is real. |
| **H15** | Stuck tasks are forever; retries don't exist. | A claimed todo set `ACTIVE` is never recovered on dispatch failure or restart. `queues.retry_policy` is seeded into the DB and read by nothing. Lease reclaim exists but **lease acquisition does not** — no code creates `BucketLeaseModel` rows. |
| **H16** | The quality gate can't say no. | `preflight.py::verify_task_completion` auto-passes any criterion it doesn't recognize (`met = True; reason = "assumed_met"`). `check_coverage` parses a possibly-stale `coverage.xml`, never runs tests. `REPO_ROOT` is computed 4 parents up from `__file__` — meaningless in an installed wheel. A preflight FAIL gates nothing (display only). `DogfoodValidator` hardcodes its "evidence". |
| **H17** | OpenBao secrets are effectively unreachable; migration is write-only. | `build_secrets_resolver` only uses OpenBao when `mode == "external"` + URL; the shipped default `mode: auto` falls to env vars without trying anything. "Connected" is logged without any I/O (`connect()` just constructs the client; async `health_check()` is never called). `migrate_profile_secrets` writes to vault but **nothing ever reads back at startup** — delete the env vars (the point of migrating) and resolution returns `None` forever. `scrub_inline_secrets` is never called. |
| **H18** | Postgres production deploy cannot work. | `ensure_tables` (create_all) runs **only for SQLite**; the daemon never invokes alembic; `alembic.ini` hardcodes `sqlite:///./test.db`; no `alembic stamp` after create_all; migration 002 drops a uniqueness constraint from the wrong table (fails on Postgres). |

### 1.3 Medium-severity

| ID | Symptom / cause |
|----|-----------------|
| **M1** | Ansible run events always `[]` — no callback plugin registered (`core_runner.py`); `stats` returns host vars, not run stats. |
| **M2** | `/api/deployments` hardcoded `return []`; compute deployments live in a closure-local dict no endpoint lists. |
| **M3** | `_inject_auth_env` warns and proceeds without credentials → opaque Terraform failures. |
| **M4** | `/admin/slurm/jobs` swallows all errors into `{"jobs": []}` — broken backend looks like "no jobs". |
| **M5** | **CLI ↔ API response-shape mismatches make working endpoints look broken:** `gludd models list` reads `models` but endpoint returns `profiles` (always prints "No models registered"); `gludd hooks register` posts `{event, handler}` but endpoint wants `{event_name, url}` (always 422); `hooks list`/`workers list` print wrong keys ("?" columns); `quantization detect` reads top-level fields the endpoint nests under `best`; `templates/playbooks refresh` print `count` that isn't returned. |
| **M6** | `/admin/playbooks/refresh` refreshes `app.state._runner` — a different instance than the EventLoop's runner. |
| **M7** | Worktree monitor: router reads subsystem key `worktree_monitor` that is never created → status permanently empty; `todo_creator` stored, never called; no background scan. |
| **M8** | gunicorn `--workers N` → N processes each with its own lifespan: N EventLoops, N in-memory todo lists, divergent API responses; SQLite ignores `FOR UPDATE SKIP LOCKED` → cross-process double-claim. |
| **M9** | The sync `runner.run_playbook` call inside an async phase blocks the entire asyncio loop (HTTP stalls during playbook runs); shutdown `task.cancel()` lands mid-tick with no drain. |
| **M10** | Integrity "OpenBao signing" is local HMAC with a hardcoded default key (`"general-ludd-integrity-key"`) — forgeable whenever the env var is unset; approvals are module-level lists lost on restart; nothing reacts to detected changes. |
| **M11** | `gludd code graph` / `gludd code search` call `/admin/code/*` endpoints that don't exist (no code-intelligence router) — always 404. The five `code_intelligence/` utilities are real and have zero callers. |
| **M12** | PID phase: production config never sets `queues` → early return; `pid_outputs` written and never read; `active_jobs=0` hardcoded; `LoadController` is threshold-based, not PID. |
| **M13** | `UserConfig` silently ignores most of `general-ludd.yml` (`secrets`, `budget`, `projects`, `compute_endpoints`, `rules`, `quality_gates`, …) — sections with no consumer anywhere. The commented `projects:` block can never work. |
| **M14** | Each EventLoop phase calls `select_project()` independently — claim/review/reconcile can target different random projects in one tick. |
| **M15** | `SecretsManager._fetch_remote_digest` returns a **random** sha256 → image-update scan always reports a fake update. |

**The minimum to make the product real is C0 → C5 + H4 + H6.** Everything else is secondary. Do them in order.

---

## 2. The only commands you may run (make targets)

| Need | Command |
|------|---------|
| Full suite + coverage | `make test` |
| All unit tests | `make test-unit` |
| One test file | `make test-unit TESTFILE='tests/unit/test_x.py'` |
| One test case | `make test-specific TESTFILE='tests/unit/test_x.py::TestC::test_m'` |
| Collection count (does it even import?) | `make test-count` |
| Integration tests | `make test-integration` |
| E2E tests | `make test-e2e` |
| Lint | `make lint` |
| Auto-fix lint | `make lint-fix` |
| Types (strict mypy) | `make typecheck` |
| Import smoke test | `make healthcheck` |
| Playbook syntax | `make ansible-syntax` |
| **Combined gate (use before every commit)** | `make qa` |
| Full gate incl. ansible | `make validate` |
| Live GLM review/gen tests | `make test-live-zai` |
| Live GLM identity test | `make test-zai-identity` |
| Branch | `make feature-start MSG='feature/...'` |
| Stage files | `make git-add FILES='a b c'` |
| Commit | `make git-commit MSG='...'` |
| Status / diff | `make git-status` / `make git-diff` |

> `make qa` = `lint` + `typecheck` + `test` + `healthcheck`. Treat a green `make qa` as the bar for "done."
> `make test-live-zai` and `make test-zai-identity` exercise the **real GLM 5.1 model** (`ZAI_MODEL=glm-5.1`). Use them only when validating the model-calling path (G4/G5) and only when a key is configured; they will skip/fail without credentials and that is expected.

---

## 3. Phase 0 — Establish the real baseline (do this before any code change)

You must know the true starting state. Do not write any feature code yet.

**Step 0.1** — Run `make test-count`. Record the collected test count. If collection errors, fixing imports is your first job (nothing else matters until the suite imports).

**Step 0.2** — Run `make test`. Record exact pass / fail / skip / error counts and the names of every failing test. `SESSION.md` claims "3 skipped, 0 failures." Verify or refute. Write the real numbers into a new file `BASELINE.md` at repo root.

**Step 0.3** — Run `make lint` and `make typecheck`. Record real error counts in `BASELINE.md`.

**Step 0.4** — Run `make healthcheck`. Confirm the worker app factory and event loop import cleanly.

**Acceptance for Phase 0:** `BASELINE.md` exists and contains the real, observed numbers (not the `SESSION.md` numbers). Commit it: `make git-add FILES='BASELINE.md'` then `make git-commit MSG='docs: record real test/lint/type baseline'`.

> If the baseline has pre-existing failures, **do not** mix fixing them into feature tasks. Note them in `BASELINE.md`; fix them as their own task `G0-fixups` if they block you.

---

## 4. Phase 1 — Make the spine work (CRITICAL path)

### G0 — The daemon must start configured

**Problem (C0).** `gludd daemon` spawns gunicorn invoking `create_daemon_app()` with no arguments. Every flag the user passes is dropped; no config ever loads in a real start.

**Files.** `src/general_ludd/cli.py` (`_build_daemon_start_cmd`, `_cmd_daemon`, the daemon subparser), `src/general_ludd/daemon.py` (`create_daemon_app`, `load_startup_config`).

**Design.**
1. Pass settings to the gunicorn child via environment variables (`GLUDD_CONFIG_DIR`, `GLUDD_TEMPLATES_DIR`, `GLUDD_PLAYBOOKS_DIR`, `GLUDD_TICK_INTERVAL`, `GLUDD_LOG_LEVEL`) set on the subprocess env — the same mechanism already used for `GLUDD_PSK`. In `create_daemon_app()`, when an argument is `None`, fall back to the corresponding env var.
2. Add a **default config search path**: when nothing is supplied, probe `~/.config/general-ludd` then `/etc/general-ludd` (the locations the shipped YAML header already documents). First hit wins; log which one was chosen, or log clearly that the daemon is running unconfigured.
3. Add the missing `--templates-dir` / `--playbooks-dir` flags to the daemon subparser (the MAN page already advertises them).
4. `load_startup_config` must load **all** YAML files in `mcp_servers/` and use the real openbao config dir — not the hardcoded `example.yml` / `default.yml` filenames only.

**Tests first** (`tests/unit/test_daemon_launch_config.py`):
- `test_build_daemon_start_cmd_propagates_env`: assert the spawn env contains `GLUDD_CONFIG_DIR` etc. when flags are given.
- `test_create_daemon_app_reads_env_fallback`: set the env vars, call `create_daemon_app()` with no args, assert the app's startup config reflects the env-provided dir (point it at a temp config dir with a minimal `general-ludd.yml`).
- `test_default_config_search_path`: with no args and no env, a fake `~/.config/general-ludd` (monkeypatched HOME) is discovered.
- `test_all_mcp_server_files_loaded`: two YAML files in `mcp_servers/` → both appear in startup config.

**Prove it:** `make test-unit TESTFILE='tests/unit/test_daemon_launch_config.py'` then `make qa`.

**Acceptance.** A daemon started via the CLI runs with the user's config dir: model profiles, prompt templates, playbooks dir, OpenBao and MCP configs all present in `app.state._startup_config` in the gunicorn child. `make qa` green.

---

### G1 — EventLoop must open a DB session every tick, and commit

**Problem (C2).** `self._session_factory` is stored and never used; in production every DB phase no-ops. Separately, the loop only ever `flush()`es — nothing commits.

**File.** `src/general_ludd/event_loop/loop.py`.

**Design (follow exactly).**
1. Add a helper that binds a session for the tick:
   - If `self.session` is already a live `AsyncSession` (test path), use it as-is, do not close, do not commit on its behalf (tests manage their own transactions).
   - Else if `self._session_factory` is set, `async with self._session_factory() as session:` for the duration of the tick; build the four repos (`TodoRepository`, `TaskReturnRepository`, `AuditEventRepository`, `VariableNamespaceRepository`) against that session; `await session.commit()` at the end of a successful tick; roll back on exception.
2. Store the live session on `self._active_session` with a small accessor the phases use; set at tick start, clear at tick end. Phases must read the per-tick session/repos, not the constructor-time `None`s.
3. **Crash-proof the tick (C4):** wrap each phase call in `tick()` so one phase's exception is logged with the phase name and does not kill the tick; and in `daemon.py`, attach a done-callback to the `run_forever` task that logs loudly (ERROR) if it ever exits. The loop dying silently is the single worst debugging trap in this codebase.

**Do not** change the constructor signature or the test-path behavior where a live `AsyncSession` is injected — those tests must still pass.

**Tests first** (`tests/unit/test_event_loop_session_per_tick.py`):
- `test_tick_opens_session_from_factory`: `EventLoop(session=<async_sessionmaker over in-memory sqlite>)`, seed one runnable todo via repo, run `tick()`, assert it was claimed.
- `test_tick_commits`: after the tick, open a **new** session from the same factory and assert the claim is visible (proves commit, not just flush).
- `test_tick_with_live_session_still_works`: backward compatibility.
- `test_phase_exception_does_not_kill_tick`: monkeypatch one phase to raise; assert the remaining phases still run and the error is logged with the phase name.
- `test_session_closed_after_tick`: no session leak across ticks.
- (`tests/unit/test_daemon_loop_death_logged.py`) `test_run_forever_death_is_logged`: force `run_forever` to exit; assert the done-callback logs at ERROR.

**Prove it:** `make test-unit TESTFILE='tests/unit/test_event_loop_session_per_tick.py'` then `make qa`.

**Acceptance.** With a `session_factory`, a runnable todo in the DB is claimed during `tick()` and the claim survives into a new session. A phase exception cannot kill the loop; loop-task death is logged. `make qa` green.

---

### G2 — `POST /api/todos` must persist to the database

**Problem (C3).** The API writes todos to an in-memory list the loop never reads.

**Files.** `src/general_ludd/routers/todos.py`, daemon lifespan in `src/general_ludd/daemon.py`.

**Design.**
1. The todo router opens a session from `app.state._session_factory` and calls `TodoRepository.create(...)` with the request fields (`title`, `description`, `queue`, `priority`, `work_type`, `project_id`, …). Commit. Return the persisted todo's id/fields.
2. `GET /api/todos`, `GET /api/status`, `/admin/todos` read from the DB (via the repo) instead of the in-memory list. Keep query params (`status`, `queue`, `project`) working. `/api/status` must also honor its `project_id` query param (currently silently ignored).
3. Remove the in-memory `_daemon_state["todos"]` path, OR keep it only as a fallback when no session factory exists (dev). Pick one; one-line comment documenting the choice.

**Tests first** (`tests/e2e/test_todos_persistence.py`):
- `test_post_todo_persists_to_db`: POST against the real app (ASGITransport), query the DB directly, row exists.
- `test_get_todos_reads_from_db`: insert via repo, GET, present.
- `test_posted_todo_is_claimable_by_event_loop`: POST a todo, run one `EventLoop.tick()` over the same DB, claimed. **This is the integration proof that G1 + G2 connect.**
- `test_status_project_filter_works`.

**Prove it:** `make test-unit TESTFILE='tests/e2e/test_todos_persistence.py'`, `make test-e2e`, `make qa`.

---

### G3 — Playbook resolution and the runner must be real

**Problem (C4 second half).** The production runner knows only `noop.yml`; dispatch of any real work type raises; `run_playbook` ignores its `private_data_dir`/extravars; `_PLAYBOOKS_ROOT` is computed relative to `__file__` (breaks installed).

**Files.** `src/general_ludd/ansible/runner.py`, `src/general_ludd/daemon.py` (runner construction), `src/general_ludd/worker/app.py` (`PLAYBOOK_REGISTRY`).

**Design.**
1. Construct the production runner with the real playbooks dir (from G0's config; default to the repo/`dist` playbooks location, resolved robustly — not 4 `.parent`s up).
2. `run_playbook` must actually pass the written vars file / `private_data_dir` into the execution, and unknown playbooks must produce a structured failure result — never an uncaught exception into the loop (G1's phase guard is the backstop, not the handler).
3. Replace the worker's `PLAYBOOK_REGISTRY = {"noop.yml"}` with the real discovered set (it will be properly consumed in G4/H3).

**Tests first** (`tests/unit/test_runner_resolution.py`):
- `test_runner_discovers_playbooks_dir`: runner built with a temp dir containing `a.yml` resolves it.
- `test_unknown_playbook_returns_failed_result_not_raise`.
- `test_extravars_reach_playbook`: run a tiny real playbook that writes one of its vars to a file; assert the value round-trips. (This is the regression test for the discarded-vars bug.)

**Prove it:** `make test-unit TESTFILE='tests/unit/test_runner_resolution.py'`, `make ansible-syntax`, `make qa`.

---

### G4 — A dispatched `code` job must call the real model and produce a real result

**Problem (C1).** Dispatch runs a stub playbook that calls no model and edits no code. `ModelGateway`/`LangGraphGateway` are real but never invoked by a job. Model profiles (including the shipped `zai_coder` → `glm-5.1`) are loaded into a config dict nothing reads.

**Decision.** The system is branded "Ansible-driven," but Ansible is the wrong layer to host an LLM code-generation loop. **Recommended: add a real in-process execution engine that the worker's `/jobs/execute` (and the loop's runner path) invokes for model work types**, keeping Ansible for genuine infra/test steps. (Only build a custom Ansible module wrapping the gateway if the project owner explicitly insists.)

**Build `src/general_ludd/execution/engine.py` — `ExecutionEngine`:**
1. Input: a `JobSpec` (`prompt_text`, `work_type`, `todo_id`, `model_profile`, `project_id`, `artifact_dir`).
2. Resolve a `ModelGateway` from the **loaded model profiles** (G0 makes them available; `zai_coder`/`glm-5.1` is the shipped default route). Reuse `src/general_ludd/models/` — do not reimplement model calls. Construct the gateway **with** `metrics_collector` and the budget guard (forward-wired for H11/H12).
3. Build the prompt: system text from `prompt_text` (+ `skill_body` if present), user text from the todo title/description. **Keep the system prompt short and mechanical** (GLM degrades with verbose prompts).
4. Call the gateway. Use a **strict, parseable output contract** — instruct the model to return a unified diff or `{path, new_content}` file-write blocks inside a fenced region. Parse defensively. Apply the writes to the project workspace repo path.
5. Run the project's configured test command (for this repo: `make test`); capture pass/fail and a short summary.
6. Produce a real `TaskReturn` (`return_id`, `todo_id`, `job_id`, `exit_code`, `result_summary`, evidence refs = changed files + test output path). Persist via `TaskReturnRepository`. Malformed model output → a *failed* `TaskReturn` with the parse error in the summary — never a fake success.

**Wire it in:**
- `worker/app.py` `/jobs/execute`: for `work_type` in the model set (`code`, `bug_fix`, `test`, `refactor`, `feature`, `security`), call `ExecutionEngine`; keep the runner path for genuine playbooks.
- `event_loop/loop.py` `_dispatch_execute_job`: model work types route to the engine, not to a stub `*.yml`. Also: add `skill_body` to `JobSpec` (H9 prerequisite) so the HTTP path carries it.

**Tests first:**
- `tests/unit/test_execution_engine.py` (mocked gateway, no network): canned model response with a file-write block → parsed, written into a temp workspace, fake test command run, populated `TaskReturn` returned. Cover: malformed output → failed `TaskReturn` with explanatory summary; empty diff → no-op result; multi-file write; test-command failure → `exit_code != 0`.
- `tests/integration/test_execute_job_real_path.py`: dispatch a `code` job through the worker handler with a mocked gateway; real `TaskReturn` persisted.
- **Live (optional, requires key):** extend `tests/live/test_zai_live.py` so `make test-live-zai` proves the engine drives GLM 5.1 and parses its output.

**Prove it:** `make test-unit TESTFILE='tests/unit/test_execution_engine.py'`, `make test-integration`, `make qa`, and with a key: `make test-live-zai`.

---

### G5 — Return review must call the real model, not a hardcoded playbook

**Problem (H4).** `return_review.yml` hardcodes `decision: complete, confidence: 0.8`. The real `ReturnReviewer` is dead code, and its failure mode silently passes.

**Files.** `review/reviewer.py`, `review/decision_applier.py`, `event_loop/loop.py` (`_dispatch_review_job`, `_persist_review_response`, `_phase_reconcile_completed_decisions`), `worker/app.py` `/jobs/return-review`.

**Design.**
1. Instantiate `ReturnReviewer` with a real gateway in the daemon lifespan; pass into `EventLoop` (new optional param `return_reviewer`, default `None`).
2. `_dispatch_review_job`: when a reviewer is present, call it with the `TaskReturn` + evidence → `TaskDecision` (`complete`/`needs_more_work`/`failed`/`blocked`/`manual_hold`) with real confidence and evidence refs. Persist via the existing `TaskDecisionModel` path (reachable now thanks to G1).
3. Fix `ReturnReviewer._call_model`: model failure → explicit `failed`/`manual_hold` decision with an error note. Never `str(prompt)` → silent `ignore_duplicate`.
4. Use `review/decision_applier.py::apply_decision` (the evidence-gated logic that already exists) in reconcile instead of the inline mapping that skips the evidence check — or fold its evidence requirement into the inline path. One path, not two.
5. Retire `return_review.yml` or reduce it to a thin shim. No parallel hardcoded path may remain.

**Tests first** (`tests/unit/test_return_review_wired.py`, mocked gateway):
- `test_review_calls_model_and_persists_decision`.
- `test_review_model_failure_is_explicit` (gateway raises → `failed`/`manual_hold`, never silent `complete`).
- `test_reconcile_requires_evidence`: a `complete` decision without evidence refs does not transition the todo to COMPLETE.
- `test_reconcile_applies_real_decision`.

**Prove it:** `make test-unit TESTFILE='tests/unit/test_return_review_wired.py'`, `make qa`, with key: `make test-live-zai`.

---

### G6 — Completed work must land in git

**Problem (H6).** Even with G4 generating real changes, nothing commits/branches/pushes — the work evaporates. `git_automation/repo.py` already implements everything needed and has zero callers.

**Files.** `src/general_ludd/git_automation/repo.py` (exists, works), `src/general_ludd/execution/engine.py` (G4), config.

**Design.**
1. Before applying model output, `ExecutionEngine` creates a branch `gludd/<todo_id>-<slug>` in the project repo via `GitAutomation`.
2. After applying + testing, commit with a structured message (todo id, title, summary, test result). The commit SHA and branch name go into the `TaskReturn` evidence refs.
3. On a `complete` review decision (G5), optionally push (config flag `git_automation.push: true/false`, default false). PR creation is Phase 4 (F1), not here.
4. If the workspace is not a git repo, initialize one (or fail the job explicitly with a clear summary) — never write into an untracked directory silently.

**Tests first** (`tests/unit/test_execution_git_delivery.py`, temp git repos):
- `test_engine_creates_branch_and_commits`: run a mocked-gateway job in a temp repo; assert branch exists, commit contains the written file, SHA is in evidence refs.
- `test_no_changes_no_commit`.
- `test_non_repo_workspace_fails_explicitly`.
- `test_push_only_when_configured` (mock the push).

**Prove it:** `make test-unit TESTFILE='tests/unit/test_execution_git_delivery.py'`, `make qa`.

---

### G7 — End-to-end proof of the spine

After G0–G6, write the proof. **This is the most important test in the repo.**

**Test** (`tests/integration/test_full_pipeline_e2e.py`, mocked gateway, temp SQLite + temp git workspace):
1. POST a `code` todo via the API.
2. Run `EventLoop.tick()` enough times to: claim it → dispatch (engine produces a commit + `TaskReturn`) → claim the return → review (mocked reviewer) → reconcile.
3. Assert: todo final status matches the decision; `TaskReturn` + `TaskDecision` rows exist; the commit exists in the workspace repo with the expected file content.

**Prove it:** `make test-unit TESTFILE='tests/integration/test_full_pipeline_e2e.py'`, then `make validate`.

**Acceptance.** One test drives a todo from API submission to a reconciled status and a real git commit, through real (mocked) model calls. **When this passes, the product is real.** Update `SESSION.md` to reflect reality.

---

## 5. Phase 2 — Secondary functional gaps

Same pattern every time: failing test first, mechanical fix, `make qa`. Recommended order as listed.

### S1 — DB sessions for routers + benchmark repo correctness (H1)
- Open a session from `app.state._session_factory` inside each handler (preferred) — stop reading the never-set `app.state._session`. Fix `BenchmarkRepository._get_session` factory path: commit and close per call (or accept a session per request from the router).
- Tests (`tests/unit/test_benchmark_endpoints_real.py`): insert rows via repo → `/admin/benchmark/scores` + `/leaderboard` return them; `POST /admin/benchmark/record` writes a row that is visible from a fresh session (proves commit); no session leak.

### S2 — Benchmark feedback loop: feed the AdaptiveRouter (H7)
- Instantiate `AutoBenchmarkRecorder` in the lifespan; after each real job (G4) and review (G5), record a `BenchmarkResult` (task type, prompt/model profile, scores from the scoring engine, tokens, cost, success).
- Test (`tests/unit/test_benchmark_recording_loop.py`): run a mocked end-to-end job; assert a `BenchmarkResultModel` row exists; after `min_samples` runs, `AdaptiveRouter.route()` returns `fallback=False`. **This makes the system self-optimizing — the headline feature the repo advertises.**

### S3 — Self-improvement persists todos (H2)
- `_phase_self_improve` persists generated todos via `TodoRepository.create` (claimable next tick). Daemon passes a config-driven `self_improve_interval` > 0. Both router variants persist to DB too (and return real ids); delete the throwaway in-memory enqueue.
- Test: a fake analysis returning N findings → N rows in the DB, claimable.

### S4 — Worker endpoints real or honest (H3)
- `/jobs/return-review` calls the reviewer (G5); `/jobs/validate` runs the runner's validate path; anything not implemented returns **501**, never `{"status":"...dispatched"}`.
- Test: each endpoint produces a verifiable effect or 501.

### S5 — Stuck-task reaper + retry policy (H15)
- New loop phase (or fold into `refill_task_buckets`): todos `ACTIVE` longer than a configurable lease window revert to `QUEUED` with an attempt counter; after `max_retries` (read `queues.retry_policy` — it's already seeded) → `FAILED` with an audit event. Implement lease acquisition when claiming (create `BucketLeaseModel` rows) so `reclaim_expired_leases` reclaims something that exists.
- Tests: stale ACTIVE todo reverts; exceeds retries → FAILED; lease row created on claim and reclaimed after expiry.

### S6 — Budget guard wired (H11)
- Construct `RunBudgetGuard` from the `budget:` config section (add the field to `UserConfig`); pass to `EventLoop` and the engine's gateway; `record_spend` on every model call.
- Tests: exceeding a configured limit blocks dispatch (the existing dead branches at `loop.py` ~L326/486 become live); spend accumulates from gateway calls.

### S7 — Metrics wired (H12)
- Construct the engine/router `ModelGateway` with `metrics_collector` + agent id; register agents on dispatch.
- Test: after a mocked job, `/admin/agents` and `/admin/metrics/cost` return non-zero real data.

### S8 — Projects: persist, clone, workspace (H13)
- `ProjectManager` backed by `ProjectRepository` (the table exists; use it): add/list/deactivate hit the DB; manager hydrates from DB at startup; `seed_from_config` actually receives `config["projects"]` (add the `projects` field to `UserConfig`).
- `POST /admin/projects` creates the workspace immediately (`ensure_dirs()`), honors `workspace_path`, and registers it with the running EventLoop (the workspaces dict must not be frozen at startup).
- **Consume `repo_url`: clone into `workspace.repo_dir`** (shallow clone acceptable) so jobs have code to edit. Failure to clone = project added in `error` state, surfaced in the API — not silent.
- Fix M14 while here: select the project **once per tick** and reuse it across phases.
- Tests: project added via API survives restart (new manager instance over same DB); workspace dirs exist; `repo_url` cloned (use a local temp origin repo); one-tick project consistency.

### S9 — Skills actually fire (H9)
- Discovery scans `config_dir/skills/**/*.md` (recursive). Catalog writes and both fetch paths **preserve `trigger_patterns`** in frontmatter; add sensible default trigger patterns to the curated entries. `JobSpec.skill_body` (added in G4) carries content to workers.
- Tests: installed skill discovered; a todo titled to match a trigger gets `skill_body` injected into the engine prompt; fetched skill retains its `trigger_patterns`.

### S10 — Prompts production-ready (H10)
- With G0, `templates_dir` resolves in production — add a regression test that the registry renders `implementation.md.j2` in a daemon started from a config dir. Map work_type → template at dispatch when the todo has no `prompt_profile` (`get_template_name_for_work_type` finally gets its caller). Set `autoescape=False` for these Markdown templates (they are not HTML).
- Tests: dispatch with no profile renders the work-type template; rendered text contains no HTML entities (`&quot;` etc.).

### S11 — MCP wired end to end (H8)
- Lifespan constructs `MCPClient` from **all** loaded server configs, calls `start_all()`, populates `MCPToolRegistry`, passes both to `EventLoop`. Resolve env via `resolve_mcp_env` (the no-plaintext design finally does something). Transport: send `notifications/initialized` after `initialize`; match responses by JSON-RPC `id`, skipping notifications.
- Add admin endpoints: list live tools, call a tool (for debugging).
- Tool → model integration is Phase 4 (F2); here you only get servers connected and tools enumerated.
- Tests: fake stdio server script (echo JSON-RPC) → client initializes, lists tools, registry populated; env aliases resolved; notification interleaved in the stream doesn't corrupt a request/response pair.

### S12 — Secrets honesty (H17)
- `build_secrets_resolver`: implement `mode: auto` honestly (try external if URL set, else log clearly that env fallback is in use — no fake "connected" message). Call (or schedule) the async `health_check()` and log its real result.
- Make the vault round-trip real: at startup, registered aliases resolve **from the vault** when the env var is absent. `scrub_inline_secrets` gets called after successful migration (behind a config flag).
- Tests: alias written to (mocked) vault resolves after env var removal; `mode: auto` without URL logs the truthful message; failed health check logged at WARNING.

### S13 — CLI/API contract fixes (M5) + missing router (M11)
- Fix every shape mismatch: `models list` reads `profiles`; `hooks register` sends `event_name`/`url`; `hooks list`/`workers list` print real keys; `quantization detect`/`drift-check` read the nested/correct fields; `templates`/`playbooks refresh` print the list lengths. Either add the code-intelligence router (`/admin/code/graph`, `/admin/code/search` backed by the real `CallGraph`/`CodeSearch`) or remove the `gludd code` commands — no permanently-404 commands.
- **Test style (important):** these bugs survived because CLI tests mocked the daemon with the *wrong* shapes. Write contract tests that exercise CLI parsing against the **real router responses** via ASGITransport — `tests/e2e/test_cli_api_contract.py`, one test per command, asserting the CLI prints the actual data.

### S14 — DB/migrations production-ready (H18)
- Lifespan: for Postgres, run alembic programmatically (or document + enforce a `gludd db upgrade` step that exists); after SQLite `create_all`, `alembic stamp head`. Fix migration 002's wrong-table constraint drop. `alembic.ini` must take the URL from the composed config, not a hardcoded `test.db`.
- Tests: fresh SQLite via lifespan is stamped; migration chain 001→004 applies cleanly on a blank SQLite file; 002's downgrade/upgrade reversible.

### S15 — Compute lifecycle fixed (C5 remainder + M2)
- `DeploymentManager` becomes a lifespan singleton with a **persistent** state dir per deployment (`~/.local/share/general-ludd/deployments/<id>/`); `destroy(instance_id)` runs in that instance's dir with its saved config. Deployments persist (DB or JSON) and `/api/deployments` lists them (kills the hardcoded `[]`). Enforce `max_cost_usd`/`timeout_minutes` with a reaper phase that destroys expired deployments. `_inject_auth_env` raises on unresolvable alias (M3).
- Tests (mock terraform subprocess): deploy then destroy reuses the same state dir; destroy after "restart" (new manager instance) still finds it; expired deployment reaped; missing auth alias raises before terraform runs.

### S16 — Honest degradation everywhere (M4 + pattern)
- `/admin/slurm/jobs` and every `except Exception: return <empty/ok>` handler must distinguish backend error (5xx with message) from genuinely-empty. Sweep `routers/` for the pattern.
- Tests: backend raising → error status, not empty success.

### S17 — Worktree + hooks + reload de-theatered (M7, H14)
- Worktree: create the `worktree_monitor` subsystem key in `_get_or_create_extended_subsystems`; persist scan results; wire `todo_creator` to `TodoRepository.create` so abandoned-worktree todos actually exist; optional periodic scan via loop phase.
- Reload: `_reload_models` actually re-reads profiles and updates the gateway; `ReloadManager` reports only what it did; EventLoop's `_on_config_reloaded` re-reads from the source, not from itself; delete `WorkerBroadcaster` or implement worker registration + real endpoints. Hooks: `HookSystem` subscribes to the event bus it's given.
- Tests per item; e.g. edit a model profile on disk → reload → gateway resolves the new value.

### S18 — Quality gate honesty (H16)
- `verify_task_completion`: unknown criteria → `met=False, reason="unverifiable"`. `check_coverage` refuses stale `coverage.xml` (mtime older than the newest source file → FAIL with "run make test first"). `REPO_ROOT` from config/cwd, not `__file__` parents. Preflight FAIL at daemon startup is reflected in `/healthz` as `degraded` (see also C6/S19). Fix or delete `DogfoodValidator`'s fabricated constants.
- Tests: unknown criterion fails; stale xml fails; healthz degraded on preflight failure.

### S19 — Startup failures surface (C6) + multi-worker safety (M8, M9)
- Split the lifespan try/except: each subsystem failure logged at ERROR with the subsystem name; a failed EventLoop/DB sets `app.state._degraded = reason` and `/healthz` reports it.
- Document and enforce single-EventLoop: only one worker runs the loop (e.g. gate on an env var gunicorn sets, or a DB advisory/lease lock); other workers serve HTTP only.
- Run `runner.run_playbook` via `asyncio.to_thread` so playbook execution stops blocking the HTTP server; shutdown waits for the in-flight tick (bounded) before cancelling.
- Tests: forced DB failure → healthz degraded; two EventLoop instances over one SQLite + lease lock → only one claims; tick with a slow (mocked) playbook doesn't block a concurrent HTTP request.

### S20 — Small honesty fixes
- M1: register an ansible callback to collect real events; propagate.
- M6: playbook refresh targets the EventLoop's runner (share the instance via app.state).
- M10: integrity HMAC requires `GL_INTEGRITY_KEY` (no hardcoded default); persist approvals; emit an event/todo on detected change.
- M12: daemon passes `queues` into the EventLoop config; dispatch consults `pid_outputs` to cap per-tick claims (simple cap is fine); feed real `active_jobs`.
- M13: every `general-ludd.yml` section either gets a consumer (most land in S3/S6/S8/S12) or is **deleted from the shipped file** — no documentation fiction.
- M15: `_fetch_remote_digest` returns a real digest or raises `NotImplementedError` — never random bytes.

---

## 6. Phase 3 — Recommended features (high-value additions)

Do these only after Phases 1–2. Each is grounded in code that already half-exists. Same TDD + `make qa` discipline. Ordered by value.

### F1 — Pull-request delivery
**Why:** G6 lands commits on branches; a human still has to find them. PRs are the natural delivery unit and the repo's stated purpose is autonomous engineering work.
**What:** on a `complete` decision with `git_automation.push: true`, push the branch and open a PR via `gh pr create` (subprocess; config: base branch, draft flag, label). PR URL stored in the todo's audit trail and shown by `gludd status <todo_id>`.
**Tests:** mock the `gh` subprocess; assert invocation args, URL persisted, no-PR-without-push, failure → audit event not crash. Prove: `make test-unit TESTFILE='tests/unit/test_pr_delivery.py'` + `make qa`.

### F2 — MCP tools in model calls (true agentic execution)
**Why:** S11 connects servers, but the model still can't use tools — `ModelGateway` has no tools parameter. This is the gap between "LLM returns a diff" and "agent that can read files, search, and browse while working."
**What:** extend `ModelGateway.call_model` with `tools: list[MCPTool]` → convert `input_schema` to the provider's function-calling format (LangChain `bind_tools`); implement the tool-call loop in `ExecutionEngine` (model requests tool → `MCPClient.call_tool` → result appended → continue, with an iteration cap and per-tool timeout).
**Tests:** mocked model emitting a tool call + fake MCP server → loop executes the tool, feeds the result back, terminates; iteration cap enforced; tool error surfaced to the model not swallowed. Live check via `make test-live-zai` (GLM 4.x/5.x function calling is supported but less turnkey than Claude — validate the format empirically).

### F3 — GitHub issues → todos ingestion
**Why:** todos currently appear only via manual CLI. Watching a repo's issue tracker makes the system useful on real projects with zero ceremony.
**What:** `gludd project watch <project_id> --github owner/repo --label gludd`; a loop phase polls (or a webhook endpoint receives) labeled issues → creates todos (title/body/work_type inferred, project-scoped, dedup by issue id); on completion, F1's PR links back to the issue.
**Tests:** mocked GitHub API → issue becomes a todo once (idempotent); label filter respected; closed issue → todo cancelled.

### F4 — Run-history and artifact API ("what did the agent do?")
**Why:** today there is no way to see what happened on a job: events are `[]`, artifacts sit in temp dirs, logs are scattered. Operators need a flight recorder.
**What:** persist per-job records (job id, todo, phase timeline, model calls with token counts, test output, commit SHA, decision) — most rows already exist (TaskReturn, TaskDecision, audit events, benchmark results); add `GET /api/todos/{id}/history` joining them, plus `gludd history <todo_id>` rendering the timeline. Store engine artifacts (model raw output, diff, test log) in the filestore keyed by job id.
**Tests:** after a mocked e2e run, history endpoint returns the full ordered timeline; artifacts retrievable via filestore endpoints.

### F5 — Per-todo cost ceilings + global daily budget with kill switch
**Why:** S6 wires the guard; this makes it operational. An autonomous agent without hard spend control is not deployable.
**What:** `budget:` config gains `per_todo_usd`, `daily_usd`; the engine checks before each model call; breach → todo `BLOCKED` with audit reason; daily breach → loop pauses dispatch and `/healthz` reports `budget_exhausted`; `gludd budget` shows spend vs limits (real data via S7).
**Tests:** per-todo breach blocks that todo only; daily breach pauses dispatch; counters reset at day boundary (injected clock).

### F6 — Live model failover chain
**Why:** the system depends on one provider (Z.AI GLM); a 429/outage currently means every job fails (or worse, pre-G5 silently passes).
**What:** `model_routing.yml` gains an ordered `fallback_chain` per route; the gateway retries the next profile on provider errors (429/5xx/timeout) with `tenacity` backoff (already a dependency); failovers recorded in metrics + benchmark results so the AdaptiveRouter learns real reliability.
**Tests:** first profile raises 429 → second used; chain exhausted → explicit failure; failover event recorded.

### F7 — TUI/CLI operational dashboard backed by real data
**Why:** the TUI exists and renders well, but most panels show the in-memory/no-op state. After Phases 1–2 the real data exists; surface it.
**What:** TUI panels for: live tick metrics (claims/dispatches/reconciles per tick), todo pipeline by status (DB-backed), recent decisions, spend vs budget, leaderboard (now non-empty via S2). Reuse the existing table-builder/factory machinery.
**Tests:** builder tests with seeded DB fixtures (the repo's existing TUI test patterns apply).

### F8 — Scheduled self-improvement with human gate
**Why:** S3 makes self-improve real; ungated, an agent that files work for itself can runaway-loop.
**What:** self-improve todos enter status `MANUAL_HOLD` by default (config `self_improve.auto_queue: false`); `gludd approve <todo_id>` (and a TUI action) releases them; cap open self-improve todos (default 10).
**Tests:** generated todos are MANUAL_HOLD; approve transitions to QUEUED; cap respected.

---

## 7. Phase 4 — Honesty and hygiene (final)

1. **Rewrite `SESSION.md`** to describe the true state. Remove "ALL GAPS CLOSED." List what now works (proved by `tests/integration/test_full_pipeline_e2e.py`) and what remains.
2. **Coverage gate.** `pyproject.toml` sets `fail_under = 10` while claiming 95%. Set `fail_under` to the real observed coverage minus a small margin. A gate of 10 hides regressions.
3. **Delete dead parallel paths** you replaced: stub playbooks, the in-memory todo store (if removed), `WorkerBroadcaster` (if not implemented), every now-unnecessary `# noqa: F401` import in `daemon.py`. After this, `run_completion_audit` should pass *because the code is wired*, not because imports game the counter.
4. **Docs:** update `docs/quickstart.md` / `configuration.md` / `dist/README.md` to match reality (config search path from G0, budget section, projects section, PR delivery flags).
5. **Run the full gate:** `make validate`. It must pass.

---

## 8. Definition of Done (apply to EVERY task)

A task is done only when ALL are true:

1. New tests were written **before** the fix and initially failed.
2. The new tests now pass: `make test-unit TESTFILE='<your test>'`.
3. The full suite is still green: `make test`.
4. Lint and types are clean: `make lint` and `make typecheck` (or just `make qa`).
5. Imports are healthy: `make healthcheck`.
6. You manually confirmed the described behavior actually happens (not just that a test asserts it).
7. Work is committed on its own branch with a descriptive `make git-commit MSG='...'`.

**Never** mark a task complete based on `SESSION.md`-style narration. The only evidence that counts is a green `make` target you actually ran and the observed behavior.

---

## 9. Repo-specific guardrails (things that will bite you)

- **Two todo stores exist** (in-memory list vs. DB) until G2 lands. Any feature that "works" through the in-memory list is not actually wired to the loop.
- **`# noqa: F401` imports in `daemon.py`** are the tell: imported to look wired, never instantiated — and they defeat the repo's own dead-code auditor. When you wire one, remove its `# noqa`.
- **Graceful degradation that always degrades:** many handlers `except Exception: return <empty/success>`, many subsystems "work when X is available" where X is never provided. Treat every such block as a probable hidden failure.
- **CLI tests that mock the daemon are how M5 happened.** Contract tests must go through the real routers (ASGITransport). Never assert against a hand-written fake response shape.
- **Backward compatibility:** the test suite injects live `AsyncSession`s and mocked gateways directly. Keep those injection points working; add the production path alongside, don't replace the test path.
- **Flush is not commit.** The existing repos `flush()`; durability requires the tick-level (or handler-level) `commit()` you add in G1/S1. When a test needs to prove persistence, read back through a **fresh session**.
- **Sync-in-async:** never call the ansible runner (or any subprocess) directly inside a loop phase; use `asyncio.to_thread` (S19).
- **GLM prompting for the execution engine (G4):** keep system prompts short and mechanical, pin output to a strict parseable format (unified diff or `{path,new_content}` blocks), instruct "respond and reason in English," and validate/parse output defensively — never assume valid structure. Budget for an output-repair retry (one reprompt with the parse error) before failing the job.
- **Live model is GLM 5.1**, reached via the Z.AI endpoint (`ZAI_MODEL=glm-5.1`, base `https://open.bigmodel.cn/api/paas/v4`) wired in `make test-live-zai` / `make test-zai-identity`. Use those targets to validate any real-model path; they skip without a key, which is fine in CI.

---

## 10. Task checklist (tick as you go)

```
Phase 0 — baseline
[ ] 0    BASELINE.md with real test/lint/type numbers, committed

Phase 1 — the spine
[ ] G0   daemon starts configured (env propagation + default config search)
[ ] G1   EventLoop session-per-tick + commit + crash-proof phases + death logging
[ ] G2   POST /api/todos persists to DB; all reads from DB
[ ] G3   runner resolves real playbooks; extravars reach runs; no-raise on unknown
[ ] G4   ExecutionEngine: real model call, parsed output, applied edits, tests run
[ ] G5   review via real ReturnReviewer; explicit failure; evidence-gated reconcile
[ ] G6   work lands in git: branch + commit + evidence SHA
[ ] G7   full-pipeline e2e test green (the proof)

Phase 2 — secondary gaps
[ ] S1   router DB sessions + benchmark repo commit/close
[ ] S2   AutoBenchmarkRecorder wired → AdaptiveRouter fed (self-optimizing loop)
[ ] S3   self-improve persists todos; interval configured
[ ] S4   worker endpoints real or 501
[ ] S5   stuck-task reaper + retry policy + real lease acquisition
[ ] S6   RunBudgetGuard constructed from config; spend recorded
[ ] S7   metrics collector fed by gateway
[ ] S8   projects: DB-backed, repo cloned, workspaces on add, one project per tick
[ ] S9   skills: recursive discovery, trigger_patterns preserved, JobSpec.skill_body
[ ] S10  prompts: production render regression, work_type mapping, autoescape off
[ ] S11  MCP: client constructed, servers started, registry populated, protocol fixes
[ ] S12  secrets: honest auto mode, vault round-trip, real health check
[ ] S13  CLI/API contract fixes + code-intelligence router (or remove commands)
[ ] S14  DB: alembic for Postgres, stamp after create_all, fix migration 002
[ ] S15  compute: persistent state dirs, working destroy, deployment reaper, listing
[ ] S16  honest degradation sweep (no empty-on-exception)
[ ] S17  worktree monitor real; reload de-theatered; hooks on the bus
[ ] S18  preflight honesty: unverifiable fails, stale coverage fails, healthz degraded
[ ] S19  startup failures surface; single-EventLoop enforcement; to_thread runner
[ ] S20  small honesty fixes (events, refresh target, HMAC key, PID consumption,
         config sections consumed-or-deleted, no random digests)

Phase 3 — features
[ ] F1   PR delivery via gh
[ ] F2   MCP tools in model calls (tool-call loop)
[ ] F3   GitHub issues → todos ingestion
[ ] F4   run-history + artifact API ("flight recorder")
[ ] F5   per-todo + daily budget with kill switch
[ ] F6   model failover chain
[ ] F7   TUI dashboard on real data
[ ] F8   gated self-improvement

Phase 4 — honesty
[ ] H    SESSION.md truthful, fail_under raised, dead paths + noqa imports removed,
         docs updated, make validate green
```

Start at Phase 0. Do not skip ahead. Prove every step with a `make` target.
