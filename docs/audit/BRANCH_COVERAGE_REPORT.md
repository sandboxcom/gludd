# Branch Coverage Report

**Generated:** 2026-07-27
**Branch:** development
**HEAD:** `b3878d2c`
**Coverage data source:** `.coverage.audit.16981` (partial run, `make audit-coverage` timed out at 180s)

## Aggregate Branch Coverage

| Metric | Value |
|--------|-------|
| Aggregate line coverage | 19.6% |
| **Aggregate branch coverage** | **4.9%** |
| Total branches | 27,438 |
| Covered branches | 1,340 |
| Files analyzed | 784 |
| Files below 75% branch threshold | 695 |

> Coverage data is from a partial shard run. Full `make audit-coverage` requires >180s.
> Target threshold: 85% aggregate, 75% per-file (`E2E_COVERAGE_AUDIT_CONTRACT`).

## Branch Coverage E2E Test Suite (5 files, 137 tests)

Task S53.41. Covers scripts `audit_coverage.py`, `parse_branch_coverage.py`, `generate_coverage_report.py`.

| # | Test file | Tests | Category |
|---|-----------|-------|----------|
| 1 | `tests/e2e/test_branch_coverage_reporting_e2e.py` | 15 | JSON report structure: e2e_branch_totals, per-file percentages, missing_branches, metadata |
| 2 | `tests/e2e/test_branch_coverage_e2e.py` | 27 | Branch data parsing, aggregate thresholds, per-file edges, missing branch identification, progress sidecar, coverage env, `run_pytest_coverage` |
| 3 | `tests/e2e/test_branch_coverage_thresholds_e2e.py` | 18 | Threshold enforcement, error messages, `fail_under` mechanism, `--cov-fail-under=0` per-shard |
| 4 | `tests/unit/test_branch_coverage_contract.py` | 40 | Structural contracts: pyproject.toml, audit_coverage.py flags, Makefile targets, `E2E_COVERAGE_AUDIT_CONTRACT.md` |
| 5 | `tests/unit/test_branch_coverage_analyzer.py` | 37 | AST-based branch counting: if/elif/else, for/while, try/except/finally, with, ternary, comprehensions, short-circuit boolops, match/case, CodePathAnalyzer |
| **Total** | | **137** | |

### Test Breakdown

**`test_branch_coverage_reporting_e2e.py` (15)**
- TestBranchReportStructure (5): e2e_branch_totals, e2e_branch_coverage, per-file branch %, line+branch, per-file thresholds
- TestPerFileBranchPercentage (3): exact fraction rounding, weighted aggregate, per_file_dict
- TestMissingBranchesActionable (4): line ranges, relative-path keying, sorted output, contexts
- TestBranchReportMetadata (3): generated_at, files_under_threshold, shards/failed_shards

**`test_branch_coverage_e2e.py` (27)**
- TestParseBranchCoverageBasic (7): branch totals, full coverage, zero coverage, missing_branches key, legacy fallback, per-file %, passed field
- TestBranchAggregateThresholds (5): above/below/exactly 85%, per-file at/below 75%
- TestPerFileBranchEdges (2): zero stmts with branches, zero/zero skipped
- TestMissingBranchesIdentification (4): single/multiple/none/multi-source
- TestBranchCoverageProgressSidecar (4): snapshot counts, complete, error field, atomic writes
- TestCoverageEnvironment (2): audit flag, override caller
- TestCoverageRunPytestCoverage (3): --cov-branch, isolated .coverage file, shard failure

**`test_branch_coverage_thresholds_e2e.py` (18)**
- TestAggregateBelowThreshold (4): <85% fails, >85% passes, per-file above but aggregate below, empty files excluded
- TestPerFileBelowThreshold (5): <75% fails, =75% passes, >75% passes, one-below-fails, custom threshold=60
- TestThresholdErrorMessages (4): exit 1, exit 0, exit 2 on missing JSON, sorted output
- TestFailUnderMechanism (5): pyproject fail_under=85, show_missing, --cov-fail-under=0, per-shard collection, main exits 1

**`test_branch_coverage_contract.py` (40)**
- TestPyprojectCoverageConfig (8): run/report sections, source, fail_under, show_missing, omit tests, pytest-cov, branch not default
- TestAuditCoverageScriptContract (13): script exists, --cov-branch, --cov-fail-under=0, --cov-context=test, --cov-append, parse_coverage_json, run_pytest_coverage, per_file_threshold, returns int, e2e_branch_totals, threshold=85, per-file=75, progress sidecar
- TestMakefileCoverageContract (8): audit-coverage, gate-audit, coverage-json targets, project python, THRESHOLD?=85, gate-audit runs both, phony, help
- TestContractDocConsistency (11): doc exists, 85%/75%, e2e_branch_totals, e2e_branch_coverage, audit-coverage, progress.json, shard handling, thresholds match, branch definition

**`test_branch_coverage_analyzer.py` (37)**
- TestBasicBranchCounting (11): if, if/else, if/elif/else, for, for/else, while, try/except, multi-handler, try/finally, try/except/else/finally, with
- TestEdgeCaseBranches (10): ternary, list/dict/set comprehension, genexpr, and/or short-circuit, chained and, mixed
- TestNestedBranches (7): nested if, if-in-for, if-in-while, try-in-if, for-in-for, 5-deep, comprehension inside function
- TestUncoveredBranchDetection (6): fully/partially covered, match/case, empty func, pure return
- TestCodePathAnalyzerIntegration (4): extracts functions, classes+methods, decorated functions, empty without tree-sitter

## Coverage Infrastructure

| Script | Purpose |
|--------|---------|
| `scripts/audit_coverage.py` | Sharded pytest runner with --cov-branch, --cov-fail-under=0 per shard, parse_coverage_json(), progress sidecar |
| `scripts/parse_branch_coverage.py` | Parse coverage-branch.json, report per-file + aggregate branch stats |
| `scripts/generate_coverage_report.py` | Generate coverage-branch.json from .coverage.audit.* data, produce branch-coverage-report.json |

## Key Make Targets

| Target | Description |
|--------|-------------|
| `make audit-coverage` | Full coverage audit: pytest --cov-branch on sharded src, 85% aggregate + 75% per-file |
| `make coverage-report-from-data` | Parse existing .coverage.audit.* data (no new pytest run) |
| `make coverage-branch-stats` | Read .gate-logs/coverage-branch.json |
| `make gate-audit` | Full gate + coverage audit |

## Thresholds

- Aggregate branch coverage target: **85%** (`E2E_COVERAGE_AUDIT_CONTRACT`, `pyproject.toml` `fail_under = 85`)
- Per-file branch coverage target: **75%** (`audit_coverage.py` `per_file_threshold = 75.0`)
- Shard-level: `--cov-fail-under=0` (collection only; aggregate audit controls pass/fail)
