# Branch Coverage Summary Report

**Generated:** 2026-07-26 | **Branch:** development | **HEAD:** 3b32ff62
**Source:** `make coverage-branch-stats` (parsed from exist coverage data)

## Aggregate Branch Coverage

| Metric | Value |
|--------|-------|
| **Aggregate branch coverage** | **16.9%** (4,649 / 27,432) |
| Aggregate line coverage | 35.5% (31,992 / 90,209) |
| Files with branch data | 784 |
| Files below 75% branch threshold | 655 |
| Shortfall to 85% target | 68.1 pp (~18,668 more branches needed) |

**Status:** CRITICAL — aggregate branch coverage far below the 85% pyproject.toml target.

## Branch Coverage Test Files & Pass Counts

All 5 branch coverage test files (137 tests total across three tiers):

### E2E Tests (87 tests)

| File | Tests |
|------|-------|
| `tests/e2e/test_branch_coverage_e2e.py` | 48 tests — branch data parsing, aggregate/per-file thresholds, progress sidecar, concurrent merging, `--cov-branch` flag |
| `tests/e2e/test_branch_coverage_reporting_e2e.py` | 22 tests — report fields, per-file breakdowns, missing branches, edge cases |
| `tests/e2e/test_branch_coverage_thresholds_e2e.py` | 17 tests — threshold exit codes, boundary values, CLI overrides, json-file mode |

### Unit Tests (50 tests)

| File | Tests |
|------|-------|
| `tests/unit/test_branch_coverage_analyzer.py` | 26 tests — AST branch detection (if/for/while/try/comprehensions/ternary/match), BranchVisitor, line/column tracking |
| `tests/unit/test_branch_coverage_contract.py` | 24 tests — pyproject.toml config, coverage thresholds, audit_coverage.py exports, Makefile wiring, quality gate schema |

### Total: 137 passing tests

Additionally, 53 coverage-related test files (branch + general coverage) span `tests/unit/`, `tests/e2e/`, and `tests/integration/`.

Branch-specific feature tests:
- `tests/unit/test_event_loop_branches.py`
- `tests/unit/test_cli_branches.py`
- `tests/unit/test_scheduler_self_update_branch.py`
- `tests/unit/test_ag9_checkpoint_branching.py`
- `tests/unit/test_git_automation_feature_branch.py`
- `tests/unit/test_repository_branches.py`
- `tests/unit/test_require_ci_green_detect_branch.py`
- `tests/e2e/test_memory_system_branches_e2e.py`

## Known Gaps

1. **Aggregate coverage critically low (16.9% vs 85% target).** The shortfall is ~18,668 uncovered branches across 655 files below the 75% per-file threshold.

2. **`make audit-coverage` times out** (~2 min timeout). The full E2E suite with `--cov-branch` runs shards in parallel and exceeds the timeout before completion. A background gate is required for full measurement.

3. **449 source files in `coverage_gaps_baseline.json` are allowlisted** (zero or near-zero coverage with no test file). These are predominantly connectors, routers, and infrastructure modules.

4. **Static branch analysis vs runtime gap.** `test_branch_coverage_analyzer.py` tests AST-level branch detection but tree-sitter integration tests may skip if `tree_sitter` is not installed.

5. **No automated remediation.** Files below the 75% per-file threshold are flagged but no test generation is triggered. New conditional branches in previously-covered files silently reduce coverage.

6. **Merge correctness not mechanically verified.** Concurrent shards combine via `coverage.combine` but there is no test proving correctness when overlapping coverage data exists.

7. **Progress sidecar durability.** `.progress.json` is written atomically during shards, but SIGKILL mid-write leaves stale state.

## Infrastructure Summary

| Component | Location | Purpose |
|-----------|----------|---------|
| Coverage audit | `scripts/audit_coverage.py` | Full pytest + `--cov-branch`, aggregate/per-file thresholds, progress sidecar |
| Branch JSON gen | `scripts/gen_branch_coverage_json.py` | Generates coverage JSON with branch data |
| Branch parser | `scripts/parse_branch_coverage.py` | Parses `coverage-branch.json` → aggregate % + per-file ranking |
| Report generator | `scripts/generate_coverage_report.py` | Structured report JSON with line/branch stats |
| Quality gate | `src/general_ludd/quality/gate.py` | `check_branch_coverage()` vs configurable minimum |
| Schema minimum | `src/general_ludd/schemas/quality_gate.py` | `branch_coverage_min_percent: float = 80.0` |
| pyproject.toml | `[tool.coverage.run]` | `branch = true`, `fail_under = 85` |
| Gaps baseline | `config/coverage_gaps_baseline.json` | 449 allowed zero-coverage source files |
| `make coverage-branch-stats` | Makefile | Quick aggregate stats from existing coverage data |
