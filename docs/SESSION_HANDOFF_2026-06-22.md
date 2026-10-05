# Session Handoff — 2026-06-22

**Branch:** `fix/self-update-sec`
**Date:** 2026-06-22
**CI / PR:** PR #2 on `integration/alpha3-rc`, CI run `27919075093` — status unconfirmed at handoff; verify before shipping.

---

## 1. What Landed This Session

All commits are on `fix/self-update-sec`.

### Completion-Integrity

- `c48a138` — port rules-engine W4c: rules now evaluate live `claimed_todos` (not config stale list); daemon swapped to `default_registry()` so `can_invoke` permission gate checks the real 4-agent registry (not empty); gateway/reviewer/router completion fixes; 12 lint findings fixed; mypy clean.
- `9c4708e` — land verified sonnet-agent work: worker `/jobs/execute` tool-call detection warn-block (+6 tests), `budget_guard→ModelGateway` wiring test, AST-based `self-improve-interval` test (de-vacuoused), 3 strengthened completion-integrity tests. 38/38 affected tests pass, lint clean.

### Security

- `70b363b` — wire operator-configured `budget_guard` into `ModelGateway`; it was constructed from config but never passed to the gateway ctor, leaving the spend ceiling silently inert. `test_daemon` 31/31.
- `6413acf` — SSRF guard on webhook registration: `HookSystem.register_webhook` now rejects RFC-1918/loopback/link-local/metadata/bad-scheme URLs via the canonical `general_ludd.security.ssrf` predicate; `_fire_webhook` sets `follow_redirects=False`. Adds `tests/unit/test_hooks_ssrf.py` (16 tests). `test_events` 66, `test_hooks_security` 17, lint+mypy clean.

### Hooks / Orchestration

- `5ca810d` — close no-wait stop-hook free-pass: replace unconditional `stop_hook_active` escape with bounded consecutive-block counter (fails open only after 25 consecutive blocks); adds slipped phrasings ("your call", "want me to push", "commit or hold"). `test_no_wait_hook` 11/11.
- `613cf1e` — no-wait stop-hook round 2: also block turn-ends that DESCRIBE the next step instead of executing it ("next step / requires a PR / I have not pushed / outward action I have not taken"). Adds 4 cases + control proving done-reports still pass. `test-no-wait-hook` 15/15.
- `0e12358` — fix model-ratio enforcer: omitting `model:` field was counted as sonnet (inherited parent opus, but hook recorded it as sonnet and never gated it). Now only explicit `model==sonnet` counts; empty/absent is non-sonnet and gated. Genuine parse failures still fail open. `test-model-ratio-hook` all pass.
- `0dd90f3` — advisory stop-hooks + liveness counts workflow subagents: hooks no longer hard-block the workflow main loop; liveness detection now counts `agent-*.jsonl` workflow subagents so the orchestrator is never trapped waiting on itself.
- `8b2923e` — pin workflow-liveness globs to verified `agent-*.jsonl` layout; add `discover` Makefile target.

### E2E Harnesses

- `27e0dab` — add four z.ai e2e harnesses: self-improve, AB testing, local-model, suitability (in `tests/e2e/`).

---

## 2. Orchestration Fixes — Detail

**Advisory stop-hooks (`0dd90f3`):** The stop-hook was previously hard-blocking, which trapped the main orchestration loop when workflow subagents were running. Hooks are now advisory; the main loop checks liveness via `agent-*.jsonl` globs and counts running workflow agents so it never stalls waiting for itself to stop.

**Model-ratio enforcer fix (`0e12358`):** The pre-tool hook enforcing the 2:1 sonnet deployment ratio had a logic bug — omitting `model:` in an agent dispatch inherits the parent model (opus), but the hook counted it as sonnet, making the ratio appear met while every un-annotated agent actually ran on opus. Fix: only an explicit `model: sonnet` satisfies the sonnet count.

**No-wait hook hardened (`5ca810d`, `613cf1e`):** Two rounds closed loopholes where the orchestrator could end a turn without doing work — first by replacing the unconditional escape hatch with a capped counter, then by pattern-matching status-report phrasings that described future actions rather than completing them.

---

## 3. CI / PR State

- **PR #2** targets `integration/alpha3-rc` (alpha.3 ship branch — CI/infra fixes only).
- **CI run `27919075093`** — greenness unconfirmed at handoff. Verify before running ship sequence.
- Ship sequence (paste-ready) is in memory `gludd-ship-https-target`:
  ```text
  make require-ci-green SHA=<full-SHA>
  make check-readme-status TAG=v0.1.0-alpha.3
  make ship-https SHA=<full-SHA> TAG=v0.1.0-alpha.3 MSG='v0.1.0-alpha.3 — third alpha'
  ```
- The completion-integrity / security fixes on `fix/self-update-sec` are NOT in the alpha.3 release. They land as post-ship PRs.

---

## 4. Remaining Backlog

### Immediate
- Confirm CI run `27919075093` green on `integration/alpha3-rc`; if red, diagnose (likely the worker xdist flake — apply `_reset_runner` fixture from `tests/unit/test_worker_d09_d10_d35.py` and re-push).
- Once confirmed green: run `ship-https` sequence, verify release artifact.

### Short-term (post-ship)
- **Worker tool dispatch** (small): `worker/app.py:99-107` never wires a `dispatcher=` into its EventLoop — mirror daemon's `build_event_loop_mcp_dispatcher()` wiring. Same gap as the daemon path, which was fixed in completion-integrity.
- **Security-P1 remaining** (partial cherry-pick from `b362e4c`):
  - `agents/registry.py:21-22` — `register()` unsealed; `default_registry()` does not call `seal()`.
  - `daemon.py:763` — bare `AgentRegistry()` → `default_registry()` swap (already fixed in `c48a138`; verify).
  - `models/gateway.py:715-740` — `call_model_with_fallback` missing health gate before `_try_call_model`.
  - `daemon.py:1134` — `_is_public` `path.startswith("/docs")` → frozenset to close `/docs_evil` bypass (P2).
  - DROP `b362e4c`'s daemon `health_tracker` hunk — completion-integrity already wired it at `daemon.py:599-614`.

### Medium
- **Audit-HIGH D1-D8** (tier-1, independent, any order): `db/repository.py` NULL col + substring filter, dispatcher semaphore race (`setdefault`), connectors class validation, `applier.py` path-traversal, `routers/integrity.py` unconfined paths, `validation/runner.py` unconfined cwd, `mcp/transport.py` bunx dual-def.
- **Migration-002**: SQLite `batch_alter_table` wrapper on `variable_namespaces` drop-constraint; `task_decisions.return_id` FK + orphan precheck; alembic drift. Cherry-pick 002-005 from `integration/alpha3-rc` — do not re-author.
- **Self-improve full wiring** (large): `_phase_self_improve` uses static gap-analysis only; generation→`UpdateApplier.apply` + `SafeWriter` + `set_code_target` + `auto_queue:true` all unwired. Files: `loop.py`, `daemon.py`, `harness.py`, `gate.py`, `applier.py`, `router.py`, `routers/self_improve.py`, `reload/*`.

### Deferred
- **D11** `daemon.py:1375-1396` — `run_until_complete` inside running uvicorn loop (latent; fires only when dispatcher path fully exercised). Make `_lazy_mcp_handler`/`_lazy_role_handler` async; `routers/dispatch.py:116` must await.
- **D12** `dispatch/dynamic_dispatcher.py:32` — `UNRESTRICTED_ROLE = "__unrestricted__"` string sentinel → `object()` identity. Grep all literals; ensure `is` comparisons. Must land before D11.
- W8.2-W15 molecule roles (real model invocation unproven beyond W8.1 HTTP path).
- `ExecutionEngine` dead-code removal (zero non-test imports; needs ~7 test file migrations).

---

## 5. Key File Locations

| File | Purpose |
|------|---------|
| `docs/SESSION_HANDOFF_2026-06-21.md` | Prior session handoff (completion-integrity audit detail) |
| `docs/SESSION_HANDOFF_2026-06-22.md` | This file |
| `docs/COMPLETION_INTEGRITY_VERDICT_2026-06-21.md` | ~37 functional / ~7 inert verdict + full fix table |
| `docs/COMPLETION_INTEGRITY_AUDIT.md` | Scorecard: 23 working / 12 inert / 6 vacuous / 4 bugs |
| `docs/AI_FEATURE_REMEDIATION_PLAN.md` | 9-item fix plan, severity-ordered |
| `docs/POST_SHIP_BACKLOG_PREP_2026-06-21.md` | Paste-ready prep for tasks #3/#4/#5/#8 |
| `tests/unit/test_hooks_ssrf.py` | SSRF guard tests (new, 16 tests) |
| `tests/e2e/` | New z.ai e2e harnesses (self-improve, AB, local-model, suitability) |
| `src/general_ludd/daemon.py` | budget_guard wiring, default_registry swap, health-gate wiring |
| `src/general_ludd/models/gateway.py` | SSRF + budget_guard ctor |
| `.claude/hooks/` | no-wait + model-ratio enforcer hooks (hardened this session) |
| `GLM_REMEDIATION_GUIDE_3.md` | Current binding work plan |
| `AGENTS.md` | Agent policy (TDD, completion, guardrail integrity) |
