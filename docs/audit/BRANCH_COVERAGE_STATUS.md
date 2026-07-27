# Branch Coverage Status

Last updated: 2026-07-26 | Branch: development | Commit: included in git-log

## Infrastructure

### Core Scripts

| Script | Purpose |
|--------|---------|
| `scripts/audit_coverage.py` | Canonical coverage audit: runs pytest with `--cov-branch`, parses coverage JSON, enforces aggregate and per-file thresholds, tracks shard progress with durable sidecar. Default aggregate threshold: 85%. Per-file threshold: 75%. |
| `scripts/gen_branch_coverage_json.py` | Generates `coverage.json` with branch data from full or E2E-only test runs. Uses `--cov-branch`, `--cov-append`, unique per-process coverage files, and an atomic combine step. |
| `scripts/parse_branch_coverage.py` | Parses `coverage-branch.json` and reports aggregate branch coverage %, per-file rankings, and bottom-N files. |
| `scripts/generate_coverage_report.py` | Generates `branch-coverage-report.json` with aggregate line/branch stats, per-file results, and bottom-20 ranking. |

### pyproject.toml Configuration

```toml
[tool.coverage.run]
branch = true
source = ["general_ludd"]
omit = ["tests/*"]

[tool.coverage.report]
fail_under = 85
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]
```

Key points:
- `branch = true` enables branch coverage at the `coverage.py` level for all test runs.
- `fail_under = 85` enforces 85% minimum coverage at the `coverage report` level.
- Source measurement targets `general_ludd/` only; tests are omitted.

### Quality Gate Integration

- `src/general_ludd/schemas/quality_gate.py` — `QualityGateConfig.branch_coverage_min_percent: float = 80.0`
- `src/general_ludd/quality/gate.py` — `check_branch_coverage()` compares coverage % against configurable minimum
- `src/general_ludd/quality/tools.py` — `check_python_coverage()` accepts `branch_coverage` parameter, defaults to 80.0% target
- `config/model_profiles/python.py` — `PythonProfile.branch: bool = True`

## E2E Coverage Audit Contract

Defined in `docs/E2E_COVERAGE_AUDIT_CONTRACT.md`. Key thresholds:

| Threshold | Value | Enforcement |
|-----------|-------|-------------|
| Aggregate E2E branch coverage | 85% | `audit_coverage.py --threshold=85` |
| Per-file minimum | 75% | `audit_coverage.py --per-file-threshold=75` |
| Shard completion | 100% | Every shard must pass; failed shards disqualify aggregate |

The contract also requires:
- `e2e_branch_totals` and `e2e_branch_coverage` in the report JSON
- Line coverage and per-file threshold verdicts
- Durable in-flight progress sidecar (`<aggregate>.progress.json`) with schema_version, run_id, pid, shard states, and JUnit diagnostics
- Subprocess coverage data combined via unique per-process files (not partial `.coverage` files)
- Failed-shard reports retained; aggregate percentage is NOT a release result when shards fail

## Test Files

All 5 branch coverage test files currently exist on the main branch and provide 137 passing tests across three tiers:

### E2E Tests (87 tests)

1. **`tests/e2e/test_branch_coverage_e2e.py`** (48 tests)
   - Branch data parsing: totals, zero coverage, full coverage, missing branches, per-file metrics
   - Aggregate threshold enforcement: exit-code 0 on pass, exit-code 1 on fail, boundary edge cases
   - Per-file threshold enforcement: all-above, one-below, mixed, exit-code 1 on violation
   - Durable progress sidecar: pid, run_id, started/updated timestamps, per-file state transitions (pending → running → passed/failed/timed_out)
   - Concurrent merging: unique per-process coverage files, atomic combine step
   - `--cov-branch` flag validation in `run_pytest_coverage`

2. **`tests/e2e/test_branch_coverage_reporting_e2e.py`** (22 tests)
   - Report fields: `e2e_branch_coverage`, `branch_coverage`, `line_coverage`
   - Per-file breakdowns: `branch_coverage` per-file with correct rounding
   - Missing branches: actionable `missing_branches` per file
   - Aggregate edge cases: single-file, multi-file, zero-module
   - Integration with `generate_coverage_report.py`

3. **`tests/e2e/test_branch_coverage_thresholds_e2e.py`** (17 tests)
   - Below-aggregate-threshold exit codes and error messages
   - Below-per-file-threshold exit codes and error messages
   - Boundary values (exactly 85%, 84.9%, 85.1%)
   - `--threshold` CLI override
   - `--per-file-threshold` CLI override
   - `--json-file` mode (parse existing coverage JSON, no pytest run)

### Unit Tests (50 tests)

4. **`tests/unit/test_branch_coverage_analyzer.py`** (26 tests)
   - AST-based branch detection: if/elif/else, for/while, try/except/finally, with, comprehensions, ternary, short-circuit logic, nested branches
   - `BranchVisitor` counts branch nodes in Python source
   - Correct line/column tracking for each branch kind
   - Tree-sitter integration for production-grade branch identification
   - Edge cases: empty modules, decorators, match/case statements

5. **`tests/unit/test_branch_coverage_contract.py`** (24 tests)
   - pyproject.toml section existence and field values
   - `branch = true` is present and parsed correctly
   - `fail_under = 85` is present under `[tool.coverage.report]`
   - `source = ["general_ludd"]` and `omit = ["tests/*"]` verified
   - `show_missing = true` present
   - Mutual consistency: E2E_COVERAGE_AUDIT_CONTRACT.md thresholds match audit_coverage.py defaults
   - audit_coverage.py exports: `parse_coverage_json`, `run_pytest_coverage`, `combine_coverage`
   - Makefile audit-coverage target wiring verified
   - Quality gate schema field verification (branch_coverage_min_percent = 80.0)

### Total: 137 passing tests

## Known Gaps and Improvement Areas

1. **Test files not present on development branch** — The 5 branch coverage test files are on the main branch but may need to be verified/present on development after merge.

2. **Static branch analysis coverage** — `test_branch_coverage_analyzer.py` covers AST-level branch detection, but tree-sitter integration tests require the `tree_sitter` package installed. Without it, those tests are skipped.

3. **E2E branch coverage measurement** — The E2E test suite's branch coverage measurement requires a full pytest run with `--cov-branch`, which can take 30+ minutes. The audit_coverage.py script handles this but gate concurrency guards may block ad-hoc runs while another pytest is active.

4. **Per-file threshold drift** — Individual source files below the 75% per-file threshold are flagged by audit_coverage.py but there is no automated remediation (test generation) for newly-introduced conditional branches.

5. **Coverage merge correctness** — Concurrent test shards produce unique per-process `.coverage` files that are combined via `coverage.combine`. There is no mechanical test verifying merge correctness when shards produce overlapping but non-identical coverage data for the same source file.

6. **Durable progress sidecar completeness** — The `.progress.json` sidecar is written atomically during shard execution, but if the audit process is SIGKILLed mid-write, the last written state may be stale. Readers must check `updated_at` against current wall-clock time.

## Current Coverage

A live coverage measurement requires `make audit-coverage` which runs the full E2E suite with branch tracking. Current aggregate coverage % is not available in this document without a gate run. The pyproject.toml `fail_under = 85` provides a floor, and the E2E contract specifies 85% aggregate / 75% per-file as the audit pass criteria.

To measure: `make audit-coverage` (runs E2E tests with `--cov-branch`, combines shards, emits `.gate-logs/coverage-<ts>.json`).
