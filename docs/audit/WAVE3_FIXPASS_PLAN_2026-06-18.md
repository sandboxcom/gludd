# Wave3 assembled-tip FIX PASS — deterministic blueprint (2026-06-18)

Apply ALL of these on the assembled tip (a81faad7 output = b4f9084 + 16 content-verified
commits, Makefile reconciled), as ONE fix pass, THEN gate once (SOLO — drain agents first;
the gate OOM-killed last time under 13 concurrent agents).

## Fixes (all behavior/security, each with tests)
1. **todos.py `api_status`** — restore the separate fallbacks the b4f9084 mypy fix collapsed:
   `db_engine` → `str(getattr(app.state,"_db_engine",None))` ("None" when absent);
   `db_url` → masked URL else "sqlite". (Keeps mypy happy; un-breaks any status test.)
   NOTE: batch-3 F6a will later REMOVE both keys entirely (post-ship) — but for THIS tip, restore them.
2. **cli test mocks (be6b3ce)** — repoint every CLI test patch of `httpx.post`/`.get`/`.delete`
   to `general_ludd.cli.httpx.request` (or patch `general_ludd.cli._http_call`). Files: test_cli.py,
   test_new_cli_commands.py, test_audit_gap_fixes.py, test_tui_extracted_builders.py + e2e cli.
   Decide HOLE1: keep richer `Error: {code} {resp.text}` and update the 3 handlers' (models remove/
   hooks list/hooks delete) test expectations. Mock shape: obj with `.status_code`, `.json()`, `.text`.
3. **mcp (7aaff07)** — per a7ebeea spec: (a) test_mcp_transport.py::test_mcp_client_facade_call_tool —
   `registry.register_tool("srv", MCPTool(name="read_file"))` before construction; (b) registry.get_tool
   name-only → return unique match or None (None on ambiguous, never first-wins) + tool_loop
   `_resolve_server_id` ambiguous-name error branch + new test_tool_loop_routing.py; (c) tool_names() →
   `sorted(set(n for (_,n) in self._tools))` + fix test_tool_names_no_duplicates_across_servers.
4. **budget try_charge (re-apply 3684cfe module, do NOT merge it — conflicts engine.py)** — per a8c2492:
   create `budget_guard_check.py` (budget_pre_check: None→None; check_all_limits/try_charge → deny on
   non-dict/!allowed/raise; unknown→deny); replace inline blocks in reviewer.py `_call_model` +
   job_invocation.py with `denial = budget_pre_check(guard); if denial: <fail-closed return>`; LEAVE
   engine.py (already fail-closed for both). Add try_charge-only denied/allowed regression tests.

## Also fold in
5. **ef1649d** (gate-safe floor + BLOCKING hook) is a SUPERSET of 4c60a1a — merge it onto the tip IN
   PLACE OF / on top of 4c60a1a. Pending hook safety-verify a9d334fa (must be SAFE-TO-ACTIVATE before it
   lands in the live tree — a fail-closed-on-uncertainty hook would deadlock turns).

## Then
- `make lint-fix && lint`, `make test-count` (collection clean), commit-bootstrap the fixed tip.
- DRAIN all subagents, then run the gate SOLO on the fixed tip (main-thread bg, stable parent).
- On green: ff master → fixed tip; verify `master == <tip>` by content (ssrf/cli/budget/mcp/agents/
  security/tooling all present). Ship once.

## Deferred to post-ship (tracked, NOT in this tip)
- batch-3 F5b (daemon+worker `/docs` over-match), F6a (status leak strip), F6b (todos limit) — clean on top.
- F5a (auth fail-open default flip) — NEEDS EXPLICIT GO (breaks ~100 tests across 28 files).
- budget_guard_check engine.py delegation (DRY) — optional consistency pass.
