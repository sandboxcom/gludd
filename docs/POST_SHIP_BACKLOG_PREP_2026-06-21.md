# Post-Ship Backlog — Verified Prep (2026-06-21)

Four parallel prep agents verified the post-ship backlog against the CURRENT working
tree (`test/coverage-recovered`). All findings are paste-ready; **re-pin every line
number with a Read at apply time** (daemon.py drifts most). Nothing below was applied —
this is prep only. Apply OFF FRESH MASTER after v0.1.0-alpha.3 ships.

Source: completion-integrity working tree owns daemon.py / models/gateway.py /
models/timeout_detector.py / config/user_config.py edits (uncommitted). The C2
install-project fix + worker-flake `_reset_runner` fixture are CI/test-infra only —
zero conflict with any source fix below.

---

## TASK #3 — Security-P1 (`fix/security-p1` @ ~b362e4c)

~60% of NEW_FINDINGS_2026-06-16 P1s are ALREADY fixed on the working tree. Do NOT
plain `git cherry-pick b362e4c` — cherry-pick `--no-commit` and trim to a PARTIAL hunk set.

**Genuinely-OPEN must-land items:**
1. `agents/registry.py:21-22` — `register()` unsealed (no `_sealed` flag/raise); `default_registry()` (L63-128) does NOT `seal()`. Apply b362e4c's +7-line seal hunk (file untouched by completion-integrity → clean).
2. `daemon.py:763` — bare `AgentRegistry()` not `default_registry()`. **Consequence: the `can_invoke` permission gate (dispatcher.py:74, already wired) checks an EMPTY registry in the daemon today → the 4 default agents are never registered.** Apply b362e4c's swap (re-pin L763).
3. `events/hooks.py:100,200` — no `is_safe_fetch_url()` / `follow_redirects=False`. Apply b362e4c hooks hunk PARTIALLY (only SSRF + no-redirect lines; retry-clamp + `_redact_payload` already present — skip to avoid dup).
4. `models/gateway.py:715-740` — `call_model_with_fallback` no health gate before `_try_call_model` (calls at L723,734) + budget not threaded (no `estimated_cost`/`budget_remaining`). NOT in b362e4c — author fresh (runbook PR-5). Drift: finding's L663-688 → now 715-740.
5. `db/models.py:191` — `TaskDecisionModel.return_id` missing `ForeignKey("task_returns.return_id", ondelete="CASCADE")` + unique. (PR-2/PR-7; see TASK #5.)
6. `daemon.py:1134` — `_is_public` still `path.startswith("/docs")` → `/docs_evil` bypass (P2, PR-8).

**DROP from b362e4c:** the daemon.py health_tracker hunk — completion-integrity already
added `ModelHealthTracker()` at daemon.py:599-601 + `health_tracker=` into ModelGateway
ctor L614. b362e4c's hunk hard-conflicts; completion-integrity supersedes it.

**GO** — partial cherry-pick. Land registry seal + daemon registry-swap first (security-critical).

---

## TASK #4 — Audit-HIGH (12 defects, ALL confirmed-present, none already fixed)

### Tier 1 — independent, any order
- **D1 / CA-DB1** `db/repository.py:603`: `details=details,` → `details=details or "{}",` (NOT NULL col, explicit None overrides default → NULL on PG).
- **D2 / CA-DB2** `db/repository.py:893-902`: `.contains(task_type)` substring false-positives + `task_type="%"` returns all. Replace with JSON-membership filter in Python. ⚠ confirm `"[]"` match-all profile semantics at apply.
- **D3 / CA-Dispatcher** `agents/dispatcher.py:50-55`: `_get_semaphore` check-and-set not atomic → doubles concurrency. Use `self._semaphores.setdefault(agent_name, asyncio.Semaphore(limit))` (no await between if/write → setdefault closes it).
- **D4 / CA-Connectors** `connectors/registry.py:185-187`: `getattr(mod, class_name)` unvalidated. Require `class_name.endswith("Source")` else ValueError (matches `_single_source_class` convention).
- **D5 / CA-E5** `self_update/applier.py:95-102`: `_first_protected` substring-only → `./secrets/../allowed` bypass. Use `Path(path).resolve().as_posix().lower()`. Fix false comment L35-37.
- **D6 / CA-R2** `routers/integrity.py:87-112,177-183`: unconfined `repo_root`/`path`. Run through existing `_confine_scan_paths(app, [raw])[0]` (raises 422) before use; confine `repo_root` vs `_scan_roots(app)`.
- **D7 / CA-validation** `validation/runner.py:92`: unconfined subprocess `cwd` (conftest code-exec at L118). `self.worktree_path = confine_worktree_path(worktree_path)` (lazy import from `worktree/core.py:64`; verify no circular import).
- **D8 / CA-M1** `mcp/transport.py:28-37,143`: dual `_NPM_FAMILY_LAUNCHERS` def → `_REMOTE_FETCH_LAUNCHERS` (L37) built from FIRST def (no bunx) → bunx skips pin gate. Edit 1: add `"bunx"` to `_MCP_EXEC_ALLOWLIST`, delete first def + the L37 build. Edit 2: after L143 keep single def (with bunx) + `_REMOTE_FETCH_LAUNCHERS = _NPM_FAMILY_LAUNCHERS | _UVX_FAMILY_LAUNCHERS`. Apply atomically; smoke `import general_ludd.mcp.transport`.

### Tier 2 — ordered
- **D9 / CA-DB3** `db/models.py:165,191`: add FK `todos.todo_id` (SET NULL) + `task_returns.return_id` (CASCADE). Migration-002 must land first (batch_alter_table + orphan precheck).
- **D10 / CA-D2** `daemon.py:846-858`: `await asyncio.to_thread(model_gateway.call_model_with_retry, ...)` (sync time.sleep blocks loop). After PR-1; re-pin if completion-integrity moved `_gateway_executor`.
- **D12 / P2#7** `dispatch/dynamic_dispatcher.py:32`: `UNRESTRICTED_ROLE = "__unrestricted__"` → `object()` identity sentinel. Grep all literals + ensure `is` comparisons. **Before D11.**
- **D11 / CA-D1** `daemon.py:1375-1396`: `run_until_complete` in running uvicorn loop. Make `_lazy_mcp_handler`/`_lazy_role_handler` async + `return await h(...)`; `routers/dispatch.py:116` must await. After D12.

**Already-fixed (do NOT re-apply):** CA-M3 (tool_loop content), CA-D3 (worker to_thread), P2#18 (build_secrets_resolver http reject). P2#6 (_is_public) partial — only startswith→frozenset remains.

---

## TASK #5 — Migration-002 SQLite batch-wrapper + alembic drift

**Branch nuance:** migrations 002-005 EXIST on `integration/alpha3-rc` (chain at ORM
parity) but NOT on `test/coverage-recovered` (only 001 + ORM-ahead). Post-ship: cherry-pick
002-005 from the ship branch — do NOT re-author. Net-new fixes:

1. `alembic/versions/002_add_projects_and_project_id.py:61-62` (upgrade) + `74-75` (downgrade): bare `op.drop_constraint`/`op.create_unique_constraint` on `variable_namespaces` fail on SQLite. Wrap in `with op.batch_alter_table("variable_namespaces", recreate="always") as batch_op:`. (`render_as_batch=True` in env.py does NOT fix standalone drop_constraint.) variable_namespaces has no named indexes in 001 → nothing extra to re-emit.
2. `task_decisions.return_id` FK (same as D9/security#5): add via `batch_alter_table("task_decisions", recreate="auto")` + orphan precheck `DELETE FROM task_decisions WHERE return_id NOT IN (SELECT return_id FROM task_returns)`; ORM `db/models.py:191` add ForeignKey.

New test: `tests/unit/test_migration_002_batch.py` — upgrade 002 / upgrade head / downgrade 001 / roundtrip on SQLite (uses alembic command API + tmp sqlite url).

---

## TASK #8 — GLM-guide backlog (guide-3 overwhelmingly DONE)

Cheapest real wins first (3 small independent commits):
1. **W4.5** remove unused `langchain`/`langchain-openai`/`langgraph` from `pyproject.toml:30-36` (imported nowhere in src/; W6.8 kept in-house ToolCallLoop). `make relock` + gate.
2. **W5.3-CVE** `TASKS.md:252-253` literally `- [ ]` unticked (the promised follow-up commit never landed; adjudications real in SECURITY.md). Tick with evidence/commit hash.
3. **W1.6 item 5** `scripts/run_gate.sh:161` runs pytest with NO `--cov` → coverage floor never binds in the gate (only `make test`/CI carry it). Add `--cov=general_ludd --cov-report=xml`.
4. **Dogfood (medium)** `scripts/dogfood.py:190` monkeypatches `loop._dispatch_execute_job` — inject a mock gateway into the REAL dispatch path instead (add `gateway=` seam to EventLoop).

**FALSE/stale doc ticks flagged:** W5.3-CVE checkboxes unticked; W1.6 ticked but coverage sub-item unmet; `docs/history-scrub.md` "completed" section stale (scrub never needed — key never in history). **W5.1** (operator key rotation) = DEFER, operator-only, NOT a ship blocker.

**Confirmed genuinely DONE (do not redo):** W1.1-W1.7, W5.4 (mypy 0), W2/W3/W6/W7-W16, ratchet down to 9 (all honest flaky/PTY/FSEvents quarantines).
