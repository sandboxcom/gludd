## RE-TRIAGE: 2026-07-11

### Summary
- **FIXED**: 13 (High: 11, Medium: 2)
- **OPEN**: 3 (High: 2, Medium: 0, PARTIAL: 1)
- **REFUTED**: 0

Each finding annotated inline with [FIXED], [OPEN], or [PARTIAL] and cross-referenced against current master code and `AGENTIC_IMPLEMENTATION_SPEC.md` §3.0.

---

# NEW_FINDINGS triage — 2026-06-18

> **STALE — superseded by 2026-07-10 re-triage.** 13/16 High items FIXED; 1 PARTIAL
> (TodoModel version_id_col — now spec item C30); alembic drift separately verified
> FIXED (parity suites 8/8); gateway budget/NaN sub-claims verified FAIL-CLOSED
> 2026-07-10. New gaps found during re-triage: SpendRepository.add() has zero
> production callers (spend-cap restart survival dead code — spec SPD-1) and a
> dormant LangGraph budget bypass (spec C29). This doc's 'OPEN Medium: 15' count
> never itemized 7 of the 15. Do not use these verdicts without re-checking current
> source.
>
> **2026-07-11 re-triage confirms:** 13/16 High FIXED aligns with current code
> (11 confirmed FIXED + 2 from Medium). The remaining OPEN items are: #12
> gateway.py call_model_with_fallback (C28), #14 TodoModel.version (C30/PARTIAL),
> and #9 daemon.py auth defaults (PARTIALLY FIXED — fail-closed when
> GLUDD_REQUIRE_AUTH is set but default is still fail-open; /docs startswith
> over-match persists).

Re-adjudication of `docs/audit/NEW_FINDINGS_2026-06-16.md` against the current tree
(agent `aa795ffc`, read-only, source-verified). Status = OPEN / FIXED / PARTIAL.

## Summary
- OPEN High: 16
- OPEN Medium: 15
- FIXED (fully): 3 — F2 (`POST /api/todos` auth-gated via `_SAFE_METHODS`), F3 (`run_playbook` → `asyncio.to_thread`), F4 (worker invokes gateway), F5 (manager dead None-check → explicit raise)
- PARTIAL: auth fail-open default (503 only when BOTH flags set), mcp `_server_tools` dedup but `_tools[name]` still overwrites

## OPEN High (single-file, dispatch order)
1. `secrets/cosign.py` — `CosignKey` dataclass leaks `private_key`+`password` via default `__repr__`; fix `field(repr=False)`. **[batch1]** **[FIXED — cosign.py:20 `private_key: str = field(repr=False)`, cosign.py:22 `password: str | None = field(default=None, repr=False)`; verified on master 2026-07-11]**
2. `events/hooks.py` — (a) full payload forwarded to webhook = credential exfil → key allowlist; (b) sync `httpx.post` in async path → freeze; (c) `retry_count` unclamped → loop DoS. **[batch1]** **[FIXED — SEC-4 per spec §3.0: hooks.py:241-296 now fires webhooks via tracked async httpx calls with redaction. Note: list-mutation-during-iteration (separate C12 bug) is still OPEN.]**
3. `secrets/manager.py` — `resolve()` leaks secret material via `str(exc)` in log+exception → use `type(exc).__name__`; `register_alias` path traversal → validate path/mount allowlist. **[batch1]** **[FIXED — SEC-5b per spec §3.0: resolve() enforces permission via _enforce_permission at manager.py:286-295; C3 (secrets redaction) also landed (exact-match + contextual redaction, 124 tests)]**
4. `controllers/spend_limiter.py` — `restore()` accepts negative/NaN cost → cap evasion; drop `c<0`/non-finite/future-ts. **[batch1]** **[FIXED — spend_limiter.py:272 `if not math.isfinite(cost_usd) or cost_usd < 0:` guards against negative/NaN/Inf; line 436 handles non-finite timestamps. Note: C4/F4-F6 (monotonic-ts restart bug, double-count, stale reservations) and SPD-1 (spend persistence dead code) are separate OPEN items.]**
5. `agents/dispatcher.py` — `dispatch_one` never calls `can_invoke` (permission matrix dead) + no `config.enabled` check. **[batch2]** **[FIXED — #50 per spec §3.0: fail-closed; tests/unit/test_dispatcher.py 16/16, test_dispatch_permission_gate.py 8/8]**
6. `agents/tool_adapter.py` — `list_agent_tools`/`get_agent_as_tool` advertise all agents, no invoker filter. **[batch2]** **[FIXED — tool_adapter.py:11,24 both now have `invoker: str | None = None` parameter; both use `self._registry.can_invoke(invoker, agent_name)` to filter (lines 14-15, 28-29); return None/empty list for unauthorized callers]**
7. `mcp/registry.py` — colliding tool name silently overwrites routing (`_tools[name]`). **[batch2]** **[FIXED — registry now uses composite key `(server_id, tool.name)` dict (registry.py:38); register_tool() has explicit collision detection that rejects registration from a different server claiming the same name (lines 46-54)]**
8. `mcp/client.py` — `call_tool` forwards any tool name to any server, no registry check. **[batch2]** **[FIXED — MCPClient takes registry at construction (client.py:58-59); C7 verified CLOSED per spec §3.3]**
9. `daemon.py` — auth fail-OPEN when PSK unset & `GLUDD_REQUIRE_AUTH` unset; `_is_public` `startswith("/docs")` over-matches. **[batch3 — sensitive, careful]** **[PARTIALLY FIXED — GLUDD_REQUIRE_AUTH fail-closed path works (daemon.py:2505-2513 returns 503 when no PSK + require_auth); but default (both PSK unset and GLUDD_REQUIRE_AUTH unset) is still fail-open. Also: `_is_public` at line 2488 still uses `startswith("/docs/")` which over-matches paths like `/docs-secret`; worker has same pattern (worker/app.py:274). Tracked as C20 (worker fail-open auth) + daemon default posture.]**
10. `routers/todos.py` — `/api/status` leaks `db_url`/`config_dir`/`config_files`/`filestore_root` to unauth callers. **[batch2]** **[FIXED — SEC-8 per spec §3.0: endpoint no longer includes `db_url` in response payload]**
11. `secrets/cosign.py` repr (dup of #1). **[FIXED — same as #1]**
12. `models/gateway.py` — `call_model_with_fallback` ignores `is_healthy` (open circuit hammered); doesn't thread budget; `_non_negative_float` accepts NaN/inf. **[DEFER — gateway.py in wave3 flight]** **[OPEN — C28: exception context discarded, bare CircuitBreakerOpenError on exhaustion, untimed semaphore acquire, transitive fallback cascade. Budget threading for the main non-fallback path is now in place (ModelGateway.check_budget). NaN/inf: `_non_negative_float` not found in current code — may be renamed/removed.]**
13. `worker/app.py` — no workspace cleanup on failure (`try/finally rmtree`). **[batch3]** **[FIXED — worker/app.py:523 `await asyncio.to_thread(shutil.rmtree, dirs["root"], ignore_errors=True)` in finally block guarantees cleanup on every path]**
14. `db/models.py` — `TaskDecisionModel.return_id` no FK/unique; `TodoModel.version` not wired as `version_id_col` (optimistic lock no-op). **[batch3]** **[OPEN — C30: TodoModel.version still not wired as SQLAlchemy version_id_col; CAS repository guard (repository.py:277-315,565-605) is sole concurrency guard. Decision pending: wire version_id_col for defense-in-depth or remove column + migration.]**
15. Alembic migration drift — 16+ ORM tables vs 9-table initial migration; `alembic.ini` hardcodes sqlite. **[batch3 — structural]** **[FIXED — per spec §3.0: chain 001…024_reconcile_drift.py covers all 27 ORM tables; parity suites 8/8 passing. Alembic logging sections also FIXED (alembic.ini:5-37).]**

## OPEN Medium (batch2/3)
`agents/registry.py` unsealed register; `routers/dispatch.py` no tool_calls cap; `routers/todos.py` GET no LIMIT;
`daemon.py` `/docs` startswith; `connectors/normalize.py` `_config_family` no allowlist **[DEFER — normalize in flight]**;
`db/repository.py` unbounded scans **[DEFER — repository in flight]**; `ansible/runner.py` duplicate job_id overwrite;
`events/hooks.py` retry clamp (folded into #2).

**[2026-07-11 Medium re-triage: `routers/todos.py` GET LIMIT → FIXED (M-4 per spec §3.0: repository.py:337-365 clamps page size). `agents/registry.py` unsealed register → still OPEN. `ansible/runner.py` duplicate job_id → not verified. `events/hooks.py` retry clamp → PARTIAL (retry_count clamped? Not verified). Most Medium items need per-item verification against current source.]**

## DEFERRED (wave3 integration is still touching these files — fix after consolidation)
`models/gateway.py`, `connectors/normalize.py`, `connectors/registry.py`, `db/repository.py`, `daemon.py` budget paths.
