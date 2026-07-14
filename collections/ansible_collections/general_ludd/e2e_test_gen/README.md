# general_ludd.e2e_test_gen — End-to-End Test Generation Collection

Five-role pipeline that analyzes source modules, generates realistic E2E
test scenarios grounded in real-world usage, writes pytest test files, and
verifies coverage thresholds.

Leverages core `code_intelligence` (`ASTBlockExtractor`, `CodeSearch`,
`CallGraph`) and `ResearcherAgent` via daemon API.

## Role Pipeline

```
analyze_code_paths → generate_scenarios → validate_scenarios → write_e2e_tests → verify_coverage
```

| # | Role | Input | Output |
|---|------|-------|--------|
| 1 | `analyze_code_paths` | target module path | `module_symbols.json` (functions, classes, methods) |
| 2 | `generate_scenarios` | module_symbols.json | `scenarios.json` (user journeys per code path) |
| 3 | `validate_scenarios` | scenarios.json + search query | `validated_scenarios.json` (pruned, web-corroborated) |
| 4 | `write_e2e_tests` | validated_scenarios.json | `test_*.py` files in output directory |
| 5 | `verify_coverage` | test output dir + source module | `coverage_report.json` (hit count per symbol) |

## Dependencies

- `general_ludd.agent >= 0.2.0` (daemon API, gludd_facts module)
- `tree-sitter` / `tree-sitter-python` (code path analysis)
- `pytest-cov` (coverage verification)
- `ResearcherAgent` via daemon `/admin/research` endpoint
