# test_matrix

Builds a dimensions×cases coverage matrix from a test inventory. Identifies gaps (empty cells). Uses gludd_facts history work_types when dimensions/cases not provided.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `test_dir` | `tests` | Test inventory directory |
| `dimensions` | `[]` | Coverage dimensions (e.g. unit, integration, e2e). Derived from facts if empty. |
| `cases` | `[]` | Test cases/work-types. Derived from gludd_facts todos if empty. |
| `min_coverage_pct` | `70.0` | Minimum coverage % for 'pass' verdict |
| `enable_pytest_collect` | `false` | Run real `pytest --collect-only` (gated) |
| `pytest_collect_override` | `""` | Canned pytest collect output (for testing) |
| `artifact_dir` | `/tmp/gludd-test-matrix` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`test_matrix.json`:
```json
{
  "role": "test_matrix",
  "status": "completed",
  "dimensions": ["unit", "integration"],
  "cases": ["auth", "api"],
  "matrix": [{"dimension": "unit", "case": "auth", "covered": true, "test_ref": "test_unit_auth"}],
  "coverage_pct": 75.0,
  "covered_count": 3,
  "total_count": 4,
  "gaps": [{"dimension": "integration", "case": "db", "covered": false, "test_ref": ""}],
  "verdict": "warn"
}
```

## Verdict

- `pass` — coverage >= min_coverage_pct
- `warn` — coverage >= 70% of threshold
- `fail` — below 70% of threshold
