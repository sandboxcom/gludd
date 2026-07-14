# verify_coverage — run pytest-cov and verify code path coverage

Runs `pytest --cov` on generated E2E tests against the target source module,
checks coverage against the configured threshold (default 85%), and verifies
that tests hit expected code paths. Produces a coverage report artifact.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `test_output_dir` | `tests/e2e/generated` | Directory with generated test files |
| `source_module` | `""` | Source module to measure coverage against |
| `test_generation_summary` | `""` | Path to test_generation_summary.json (for coverage_targets) |
| `coverage_threshold` | `85` | Minimum coverage percentage to pass |
| `pytest_timeout` | `300` | Timeout in seconds for pytest run |

## Artifact

`coverage_report.json`:
```json
{
  "module": "src/example.py",
  "coverage_percent": 87.3,
  "threshold": 85,
  "verdict": "pass",
  "symbol_level": {
    "create_resource": {"hit": true, "lines_hit": 12, "lines_total": 14},
    "delete_resource": {"hit": false, "lines_hit": 0, "lines_total": 8}
  },
  "overall": {"statements": 142, "missing": 18, "branches": 24, "partial": 3}
}
```
