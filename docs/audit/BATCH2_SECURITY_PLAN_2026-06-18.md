# Batch-2 security — dispatch plan (2026-06-18)

Precise patch specs produced by read-only agent `aa37bd7a` (full spec in its task output).
Sequencing is driven by wave3 merge-conflict risk.

## Dispatch NOW (off master — LOW conflict, no overlap with b4f9084/wave3)
- **F1 + F2 — `mcp/registry.py` + `mcp/client.py`** (agent): composite `(server_id, name)` key so colliding
  tool names don't silently clobber routing; `call_tool` rejects a tool not registered to that server.
  Grep `get_tool(` call sites first; back-compat name-only lookup retained.
- **F3 + F4 — `agents/dispatcher.py` + `agents/tool_adapter.py`** (agent): `dispatch_one` honors
  `config.enabled` + `registry.can_invoke(invoker, target)` (adds `AgentTask.invoker: str|None=None`,
  None=skip for back-compat); `list_agent_tools`/`get_agent_as_tool` filter by invoker when supplied.

## DEFER until AFTER wave3 ships (must apply on top of b4f9084, not master)
- **F6a — `routers/todos.py` `/api/status` infra leak** (strip `db_url`/`config_dir`/`config_files`/
  `filestore_root`/`db_engine`): **OVERLAPS** b4f9084 mypy fix (same `db_url` lines) → conflict if done now.
- **F6b — `GET /api/todos` no LIMIT** (+ `TodoRepository.list_all` limit/offset): touches `db/repository.py`
  (MEDIUM conflict, wave3 db lane).
- **F5b — `daemon.py` `_is_public` `startswith("/docs")` over-match** (→ `== "/docs" or startswith("/docs/")`):
  HIGH conflict (daemon.py wave3-hot), but mechanically trivial — do right after wave3.
- **F5a — `daemon.py` auth fail-OPEN default**: BEHAVIOR-CHANGING. Flipping to fail-closed breaks the
  intentional `test_default_no_psk_keeps_admin_open` redteam test (which documents the open default as
  back-compat). Proposed: default fail-closed + opt-out `GLUDD_ALLOW_NOAUTH=1`, update that test to set it.
  Treat as a deliberate, reviewed change — NOT a parallel burst. Confirm intent before flipping the default.

## Already done (batch-1, landed + verifying)
F: cosign repr `269571f`, secrets/manager leak+traversal `14910bd`, spend_limiter cap-evasion `4b33cc0`,
events/hooks webhook exfil+async+clamp `0eef1f6`. Adversarial verify in flight (`ad45933f`).
