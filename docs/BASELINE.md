# Baseline — 2026-06-10 (updated 2026-06-11 V0.1 batch1)

## Test Suite — Current (post V0.1 batch1: 42 failures fixed)

| Metric | Count |
|--------|-------|
| Collected | 5,654 |
| Passed | 5,530 |
| Failed | 94 |
| Skipped | 30 |
| Warnings | 18 |
| Duration | ~177s |

### V0.1 batch1 fixes (2026-06-11, commit b09e4ce)

Fixed 42 failures — mostly regressions from API changes post-R2:

- **zai-skip (2)**: `_get_llm_for_profile` renamed; test rewritten to verify fallback chain (R0.6 proof test now passes)
- **benchmark repo (16)**: `record_result` now takes `data: dict` not kwargs; `get_aggregate_scores` filters success=True; `get_best_for_task` sorts by composite score; `list_for_task_type` includes general-purpose profiles; `_execute_with_session` commits + expunges
- **variable repo (5)**: `set_var` updates existing vars instead of always inserting; `load_vars_for_project` prioritizes project vars over global; `create_namespace` signature matched
- **sprint1 daemon (3)**: Updated `record_result` calls to dict API; fixed error message regex
- **langgraph gateway (8)**: `find_spec` catches `ValueError`; tests use proper `ModuleSpec` mock
- **db models (8)**: Added `BACKLOG → QUEUED` to VALID_TRANSITIONS; added `get_by_id` to TaskReturnRepository, `get_by_name` and `list_enabled` to QueueRepository

### Remaining failures (94, pre-existing from original baseline ~80 + undiscovered ~14)

Groups: worker endpoints (5), secrets/OpenBao (8), daemon wiring (12), CLI (7), skills (3), scoring/router (3), guardrails (2), e2e/integration (14), ansible runner (1), MCP transport (1), pipeline/wiring (8), TUI (3), runtime/container (3), other (24). See `make test-failures` for full list.

## Test Suite — Previous (post R0.1-R0.4)

| Metric | Count |
|--------|-------|
| Collected | 5,606 |
| Passed | 5,460 |
| Failed | 116 |
| Skipped | 30 |
| Warnings | 17 |
| Duration | 174s |

## Test Suite — Previous (2026-06-10 validation pass)

| Metric | Count |
|--------|-------|
| Collected | 5,497 |
| Passed | 5,427 |
| Failed | 40 |
| Skipped | 30 |
| Warnings | 27 |
| Duration | 191s |

### Delta Analysis (from 40 → 115 failures)

**New failures introduced by R0.3 fixes (~20):**
- BenchmarkRepository tests (10): my _execute_with_session refactor broke the constructor pattern
- test_benchmark_repo_session_factory.py (1): my new test, likely needs fixture adjustment
- test_sprint1_daemon_wiring.py (3): BenchmarkRepository constructor change
- test_variable_repo.py (5): missing `project_id` arg to `load_vars_for_project`

**May be new or pre-existing (~20):**
- test_skills.py (3): Skill model field change (`category` added) — needs verification
- test_benchmark_repo.py: old BenchmarkRepository tests (10) broken by constructor change

**Pre-existing from original baseline (~40):**
- AdaptiveRouter (5)
- Secrets/OpenBao (7)
- Runtime relative paths (4)
- Bootstrap/filestore (3)
- TUI extracted builders (3)
- Guardrail self-tests (2, including make_test_passes)
- Model health (1)
- Worktree (1)
- Ansible endpoints (3)
- CLI (2)
- Other (9)

### Will be fixed in:
- R2.x: benchmark repo, variable repo, skills, and all claimed-done items re-proven
- R0.6: ZAI live test skips
- R0.7: port-8000 flake

## Lint

| Metric | Count |
|--------|-------|
| Errors | 0 |

## Typecheck (mypy strict)

| Metric | Count |
|--------|-------|
| Errors | 21 in 10 files |

**By file:**
- otel_bridge.py: 5 errors (missing opentelemetry stubs)
- cli.py: 6 errors (build_parser return type)
- dashboard_data.py: 1 error (Any return)
- metrics_exporter.py: 3 errors (assignment + type-arg)
- integrity/scanner.py: 1 error (Any return)
- planning/repo_map.py: 1 error (untyped def)
- execution/tool_loop.py: 1 error (Any return)
- db/session.py: 1 error (Any return)
- review/reviewer.py: 1 error (assignment)
- routers/projects.py: 1 error (Any return)

## Healthcheck

Worker app factory: OK
Event loop import: OK
