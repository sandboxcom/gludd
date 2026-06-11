# Baseline — 2026-06-10 (updated 2026-06-10)

## Test Suite — Current (post R0.1-R0.4)

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
