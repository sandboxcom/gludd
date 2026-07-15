# verify_coverage — run pytest-cov and verify code path coverage

Runs `pytest --cov` on generated E2E tests against the target source module,
checks coverage against the configured threshold (default 85%), and verifies
that tests hit expected code paths. When `scenarios_file` and/or `symbols_file`
are supplied, the role additionally cross-references declared coverage targets
against measured coverage and emits a structured gap report identifying
uncovered symbols, partial symbols, unresolved targets, and suggested
follow-on scenarios. Produces a coverage report artifact.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `test_output_dir` | `tests/e2e/generated` | Directory with generated test files |
| `source_module` | `""` | Source module to measure coverage against |
| `test_generation_summary` | `""` | Path to test_generation_summary.json (for coverage_targets) |
| `coverage_threshold` | `85` | Minimum coverage percentage to pass |
| `pytest_timeout` | `300` | Timeout in seconds for pytest run |
| `scenarios_file` | `""` | Optional path to `validated_scenarios.json`; when set, passes `--scenarios-file` so coverage targets are cross-referenced against measured hits |
| `symbols_file` | `""` | Optional path to `module_symbols.json`; when set, passes `--symbols-file` so per-symbol coverage (missing / partial / covered) is classified |
| `artifact_dir` | `/tmp/gludd-e2e-test-gen` | Directory for emitted artifacts |
| `coverage_artifact_name` | (role default) | Filename of the emitted coverage report |
| `test_file_prefix` | `test_e2e_generated_` | Glob prefix used to discover generated test files |
| `verify_coverage_script` | (role default) | Path to the `verify_coverage.py` helper |
| `enable_git_push` | `false` | Reserved — controls whether artifacts are pushed |

The `--scenarios-file` and `--symbols-file` flags are passed to the script
**only** when the corresponding variable is a non-empty string. Omitting both
produces the legacy coverage-only report (no `gap_report` cross-reference).

## Artifact

`coverage_report.json` (top-level shape):

```json
{
  "module": "src/example.py",
  "test_output_dir": "tests/e2e/generated",
  "coverage_percent": 87.3,
  "threshold": 85,
  "verdict": "pass",
  "verdict_reason": "coverage meets threshold",
  "pytest_exit_code": 0,
  "coverage_targets": ["create_resource", "delete_resource"],
  "status": "completed",
  "gap_report": {
    "overall_verdict": "meets_threshold",
    "coverage_gap_pp": 0.0,
    "missing_symbols": [],
    "partial_symbols": [],
    "unresolved_targets": [],
    "covered_targets": ["create_resource"],
    "uncovered_targets": ["delete_resource"],
    "suggested_scenarios": [
      {
        "target": "delete_resource",
        "rationale": "public symbol with 0% line coverage; generate a CRUD-delete scenario",
        "priority": "high"
      }
    ]
  }
}
```

### `gap_report` field reference

The `gap_report` block is emitted whenever `--scenarios-file` and/or
`--symbols-file` are supplied. When neither is supplied, the block is still
present but populated only from raw coverage data (no cross-reference).

| Field | Type | Description |
|-------|------|-------------|
| `overall_verdict` | `meets_threshold` \| `below_threshold` | Whether `coverage_percent` reached `threshold` |
| `coverage_gap_pp` | number | `threshold - coverage_percent`, clamped at 0 |
| `missing_symbols` | list[str] | Public symbols with **0%** line coverage (no executed lines in their range) |
| `partial_symbols` | list[str] | Public symbols with **some** executed and **some** missing lines in their range |
| `unresolved_targets` | list[str] | `coverage_targets` declared in the scenarios file that do not resolve to any known symbol |
| `covered_targets` | list[str] | Declared coverage targets whose underlying symbol was fully hit |
| `uncovered_targets` | list[str] | Declared coverage targets whose underlying symbol was missing or partial |
| `suggested_scenarios` | list[object] | One entry per uncovered target with `target`, `rationale`, and `priority` — feed these back into `generate_scenarios` to close the gap |

### `verdict` values

| Verdict | Meaning |
|---------|---------|
| `pass` | Coverage ≥ threshold |
| `fail` | Coverage < threshold — role fails the play |
| `skip` | No generated test files found under `test_output_dir` matching `test_file_prefix` |

## Chain usage

Typical pipeline wiring (see the collection-level README):

```yaml
- import_role: generate_scenarios      # emits module_symbols.json + scenarios.json
- import_role: validate_scenarios      # emits validated_scenarios.json
- import_role: write_e2e_tests         # emits tests/e2e/generated/test_e2e_generated_*.py
- import_role:
    name: general_ludd.e2e_test_gen.verify_coverage
  vars:
    scenarios_file: "{{ artifact_dir }}/validated_scenarios.json"
    symbols_file: "{{ artifact_dir }}/module_symbols.json"
```
