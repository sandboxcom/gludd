# Remaining Work — 2026-06-22

Single consolidated punch-list. Branch: `fix/self-update-sec`. Ship target: `v0.1.0-alpha.3`.

Sources consolidated: `make git-log`, `docs/SESSION_HANDOFF_2026-06-22.md`,
`docs/SESSION_HANDOFF_2026-06-21.md`, `docs/POST_SHIP_BACKLOG_PREP_2026-06-21.md`,
`docs/ci-live-failure-detection.md`, `AGENTS.md` guardrail sections.

---

## 1. Ship-blocking (DO FIRST)

Goal: get `fix/self-update-sec` green, then merge to master via **PR #2 → `integration/alpha3-rc`**.

- The CI-fix clusters are landing now as cherry-picks onto `fix/self-update-sec`
  (recent `make git-log`): `386d8ee` (11 security-hardening test failures),
  `c9ca19a` (mcp collision guard + registry/security-gate alignment),
  `390dcc1` (FK parent-row seeding after D9), `e1fa4a4` (local-inference test flag +
  optional psycopg2 import guard), `06c1c9d`/`324704f` (await async
  `DynamicDispatcher.dispatch` call sites), `d8f2d53` (auth alias, todos db_engine
  mask, loop import, stale-test updates).
- Once integration is complete, push **ONE candidate SHA** to the PR branch.
- Watch via `make ci-poll` + `make ci-annotations` (per-shard `::error::` failures
  surface in minutes — see `docs/ci-live-failure-detection.md`; logs API is 404
  mid-run, annotations are the only live structured signal, poll ~30s).
- **Require confirmed-green before merge.** Do NOT merge or tag on an unconfirmed run.
  Last referenced run `27919075093` greenness was UNCONFIRMED at handoff.
- Ship sequence (paste-ready, only after CI is confirmed green; from memory
  `gludd-ship-https-target`):
  ```text
  make require-ci-green SHA=<full-SHA>
  make check-readme-status TAG=v0.1.0-alpha.3
  make ship-https SHA=<full-SHA> TAG=v0.1.0-alpha.3 MSG='v0.1.0-alpha.3 — third alpha'
  ```
- NOTE: completion-integrity + security source fixes on `fix/self-update-sec` are
  **NOT** in the alpha.3 release — they land as post-ship PRs (§3).

---

## 2. Integration housekeeping

- **Worktree branches to integrate** — the CI-fix clusters above originate from
  `agent-*` / `wf_*` worktree branches under `.claude/worktrees/` (~340 worktrees:
  ~305 `agent-*`, ~45 `wf_*`). The integrator drains finished worktree commits onto
  the candidate branch one at a time, hot-file edits serialized (`daemon.py`,
  `loop.py`, `gateway.py`); gate green after each merge before the next lands
  (AGENTS.md "Pipeline Orchestration Model").
- **Worktree-venv cleanup needed** — `make disk` showed 5 worktrees still carry a
  ~317–348 MB `.venv` each. Run `make clean-worktree-venvs` to reclaim and avoid
  ENOSPC (cap simultaneous worktree agents at ~5–6).
- **Working-tree dirty markers** — `make git-status` shows unresolved-merge sentinels:
  `UU src/general_ludd/self_improve/harness.py`, `DU
  tests/unit/test_self_improve_slice.py`. Resolve these conflicts before any commit.
- **Orphaned async straggler — ALREADY FIXED.** `test_daemon_mcp_dispatch.py` lives at
  `tests/integration/test_daemon_mcp_dispatch.py` (not `tests/unit/`); all 3 async
  tests carry `@pytest.mark.asyncio` and every `dispatch(...)`/`dispatch_all(...)` is
  awaited (consistent with now-async `DynamicDispatcher.dispatch`). No action needed.

---

## 3. Post-ship backlog (with file anchors)

Apply off FRESH master after alpha.3 ships. Re-pin every line number with a Read at
apply time (`daemon.py` drifts most). Full prep: `docs/POST_SHIP_BACKLOG_PREP_2026-06-21.md`.

- **D9 FK migration deploy precheck** — migration 006 (FK chain) exists; needs an
  orphan-check before prod. `db/models.py:165,191` (todos.todo_id SET NULL +
  task_returns.return_id CASCADE). Migration must run orphan precheck
  (`DELETE FROM task_decisions WHERE return_id NOT IN (SELECT return_id FROM task_returns)`)
  inside `batch_alter_table` before adding the constraint. Cherry-pick migrations
  002–005 from `integration/alpha3-rc` (don't re-author; SQLite needs
  `batch_alter_table(recreate=...)` on `variable_namespaces` per Migration-002 task).
- **Worker full tool-dispatch wiring** — currently warn-block only. `worker/app.py:99-107`
  never wires `dispatcher=` into the EventLoop; mirror daemon's
  `build_event_loop_mcp_dispatcher()`. (Warn-block + tests landed `9c4708e`.)
- **Self-improve full wiring** — config-tier slice landed; code-tier rotation deferred.
  `_phase_self_improve` still does static gap-analysis only; the
  generation→`UpdateApplier.apply`+`SafeWriter`+`set_code_target`+`auto_queue:true`
  path is unwired. (Large.) Resolve the `harness.py`/`test_self_improve_slice.py`
  merge conflict first (§2).
- **Security-P2 / P1 remaining** (partial cherry-pick from `b362e4c` — `--no-commit`,
  trim hunks; ~60% already fixed on working tree):
  - `agents/registry.py:21-22` — `register()` unsealed, `default_registry()` (L63-128)
    never `seal()`s. Apply b362e4c's +7-line seal hunk (file untouched → clean).
  - `daemon.py:763` — bare `AgentRegistry()` → `default_registry()` (claimed fixed in
    `c48a138`; VERIFY). Empty registry means the `can_invoke` gate (`dispatcher.py:74`)
    checks no agents.
  - `events/hooks.py:100,200` — apply SSRF + `follow_redirects=False` lines PARTIALLY
    (retry-clamp + `_redact_payload` already present). (SSRF guard already landed
    `6413acf` for `HookSystem.register_webhook` + `tests/unit/test_hooks_ssrf.py`;
    confirm `_fire_webhook` path covered.)
  - `models/gateway.py:715-740` — `call_model_with_fallback` no health gate before
    `_try_call_model` + budget not threaded. Author fresh (runbook PR-5).
  - `daemon.py:1134` — `_is_public` `path.startswith("/docs")` → frozenset
    (`/docs_evil` bypass).
  - **DROP** b362e4c's daemon `health_tracker` hunk — already wired at
    `daemon.py:599-614` (completion-integrity supersedes; hard-conflicts).
  - Audit-HIGH D1–D8 (independent, any order): `db/repository.py:603` NULL col,
    `:893-902` substring filter; `agents/dispatcher.py:50-55` semaphore race
    (`setdefault`); `connectors/registry.py:185-187` class validation;
    `self_update/applier.py:95-102` path-traversal; `routers/integrity.py:87-112,177-183`
    unconfined paths; `validation/runner.py:92` unconfined cwd; `mcp/transport.py:28-37,143`
    bunx dual-def. Deferred: D12 `dispatch/dynamic_dispatcher.py:32` `UNRESTRICTED_ROLE`
    string→`object()` sentinel (before D11); D11 `daemon.py:1375-1396`
    `run_until_complete` inside running uvicorn loop.
- **gate(3.12) timeout** — mitigated by sharding (`b99f205` shards the test job into
  parallel shards; failures surface per-shard in minutes). Watch the 3.12 worker xdist
  flake: if a re-run reddens only on `test_worker_redacts_secret_aliases_in_logs`,
  apply the `_reset_runner` autouse fixture from
  `tests/unit/test_worker_d09_d10_d35.py` and re-push.
- **CI-env C2: editable-install for TUI httpx e2e** — gludd project not installed into
  CI `.venv` → `ModuleNotFoundError: No module named 'general_ludd'` from bare
  `.venv/bin/python3`. Fix: drop `--no-sync`/`--no-install-project` OR add
  `uv pip install -e .` so `general_ludd` + `httpx` import from site-packages and the
  TUI httpx e2e tests pass. (Verified locally fresh-venv; confirm landed on the ship
  candidate.)

---

## 4. Orchestration / infra DONE this session (do NOT re-do)

All on `fix/self-update-sec` (per `docs/SESSION_HANDOFF_2026-06-22.md`):

- **Advisory hooks + workflow-aware liveness** — `0dd90f3` (stop-hooks advisory by
  default; liveness counts `agent-*.jsonl` workflow subagents), `8b2923e` (pin liveness
  globs to `agent-*.jsonl`; add `discover` target).
- **Sonnet-ratio fix** — `0e12358` (omitting `model:` inherits opus and is now correctly
  counted as non-sonnet/gated by `model_utilization_pretool.sh`).
- **Force-delegate hook** — `GLUDD_FORCE_DELEGATE=1` opt-in grind guard
  (`force_delegate_pretool.sh`): denies targeted mutations below `CLAUDE_AGENT_FLOOR`,
  bounded escape after `GLUDD_FORCE_DELEGATE_MAXBLOCK` (default 4).
- **No-wait stop-hook hardening** — `5ca810d` (bounded consecutive-block counter, fails
  open after 25; +slipped phrasings), `613cf1e` (block turn-ends describing next step;
  15/15).
- **CI poller + annotations + sharding + concurrency auto-cancel** — `b99f205` (shard
  test job into parallel shards); `make ci-poll` / `make ci-annotations` consume live
  `::error::` annotations (research: `docs/ci-live-failure-detection.md`).
- **Codified guardrails in AGENTS.md** (3-layer: prompt + hook + config/test):
  - **Pipeline Orchestration Model** — keep the pipeline primed, bias to disjoint/
    new-file work, one continuous integrator, bound by hot-file concurrency + worktree disk.
  - **Constraints Are To Engineer Around** — a naked "can't" is a bug; pair every
    constraint with a workaround or a dispatched research task (`a1c160a`).
  - **Codify Improvements** — codify a better way of working in-session across the three
    layers (AGENTS.md / `.claude/hooks/` / memory).
  - **Keep Opus Lean** — delegate heavy reading/editing/testing to `model:'sonnet'`
    subagents; terse main thread; subagents return terse summaries + file pointers.
