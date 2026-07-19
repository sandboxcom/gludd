# generate_scenarios — map code paths to E2E test scenarios

Matches public functions/classes from `ModuleSymbols` against the scenario
catalog (CRUD, auth, timeout, concurrent, daemon restart) using keyword
heuristics. Produces `GeneratedScenario` records with step sequences and
coverage targets.

If `symbols_artifact` is not provided, runs `analyze_code_paths` first on
`target_module` to produce the symbols.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `symbols_artifact` | `""` | Path to existing module_symbols.json (takes priority) |
| `target_module` | `""` | Path to Python module (used if no symbols_artifact) |
| `artifact_dir` | `/tmp/gludd-e2e-test-gen` | Output directory |

## Artifact

`scenarios.json`:
```json
[
  {
    "name": "crud_lifecycle",
    "description": "Create, read, update, delete a resource through the API",
    "steps": [
      {"action": "POST", "target": "/api/resource", "expected_result": "201 Created", "assertions": [...]}
    ],
    "coverage_targets": ["create_resource", "delete_resource"]
  }
]
```
