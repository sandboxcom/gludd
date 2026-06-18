# NEW_FINDINGS triage — 2026-06-18

Re-adjudication of `docs/audit/NEW_FINDINGS_2026-06-16.md` against the current tree
(agent `aa795ffc`, read-only, source-verified). Status = OPEN / FIXED / PARTIAL.

## Summary
- OPEN High: 16
- OPEN Medium: 15
- FIXED (fully): 3 — F2 (`POST /api/todos` auth-gated via `_SAFE_METHODS`), F3 (`run_playbook` → `asyncio.to_thread`), F4 (worker invokes gateway), F5 (manager dead None-check → explicit raise)
- PARTIAL: auth fail-open default (503 only when BOTH flags set), mcp `_server_tools` dedup but `_tools[name]` still overwrites

## OPEN High (single-file, dispatch order)
1. `secrets/cosign.py` — `CosignKey` dataclass leaks `private_key`+`password` via default `__repr__`; fix `field(repr=False)`. **[batch1]**
2. `events/hooks.py` — (a) full payload forwarded to webhook = credential exfil → key allowlist; (b) sync `httpx.post` in async path → freeze; (c) `retry_count` unclamped → loop DoS. **[batch1]**
3. `secrets/manager.py` — `resolve()` leaks secret material via `str(exc)` in log+exception → use `type(exc).__name__`; `register_alias` path traversal → validate path/mount allowlist. **[batch1]**
4. `controllers/spend_limiter.py` — `restore()` accepts negative/NaN cost → cap evasion; drop `c<0`/non-finite/future-ts. **[batch1]**
5. `agents/dispatcher.py` — `dispatch_one` never calls `can_invoke` (permission matrix dead) + no `config.enabled` check. **[batch2]**
6. `agents/tool_adapter.py` — `list_agent_tools`/`get_agent_as_tool` advertise all agents, no invoker filter. **[batch2]**
7. `mcp/registry.py` — colliding tool name silently overwrites routing (`_tools[name]`). **[batch2]**
8. `mcp/client.py` — `call_tool` forwards any tool name to any server, no registry check. **[batch2]**
9. `daemon.py` — auth fail-OPEN when PSK unset & `GLUDD_REQUIRE_AUTH` unset; `_is_public` `startswith("/docs")` over-matches. **[batch3 — sensitive, careful]**
10. `routers/todos.py` — `/api/status` leaks `db_url`/`config_dir`/`config_files`/`filestore_root` to unauth callers. **[batch2]**
11. `secrets/cosign.py` repr (dup of #1).
12. `models/gateway.py` — `call_model_with_fallback` ignores `is_healthy` (open circuit hammered); doesn't thread budget; `_non_negative_float` accepts NaN/inf. **[DEFER — gateway.py in wave3 flight]**
13. `worker/app.py` — no workspace cleanup on failure (`try/finally rmtree`). **[batch3]**
14. `db/models.py` — `TaskDecisionModel.return_id` no FK/unique; `TodoModel.version` not wired as `version_id_col` (optimistic lock no-op). **[batch3]**
15. Alembic migration drift — 16+ ORM tables vs 9-table initial migration; `alembic.ini` hardcodes sqlite. **[batch3 — structural]**

## OPEN Medium (batch2/3)
`agents/registry.py` unsealed register; `routers/dispatch.py` no tool_calls cap; `routers/todos.py` GET no LIMIT;
`daemon.py` `/docs` startswith; `connectors/normalize.py` `_config_family` no allowlist **[DEFER — normalize in flight]**;
`db/repository.py` unbounded scans **[DEFER — repository in flight]**; `ansible/runner.py` duplicate job_id overwrite;
`events/hooks.py` retry clamp (folded into #2).

## DEFERRED (wave3 integration is still touching these files — fix after consolidation)
`models/gateway.py`, `connectors/normalize.py`, `connectors/registry.py`, `db/repository.py`, `daemon.py` budget paths.
