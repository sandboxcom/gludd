# write_e2e_tests — generate pytest test files from validated scenarios

Reads validated scenarios and emits pytest test files using project fixtures
(TestClient, _run_cli, tmp_path) with proper AAA structure and coverage
markers. Each scenario becomes a test function; each step within a scenario
maps to assertions.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `validated_scenarios_artifact` | `""` | Path to validated_scenarios.json |
| `artifact_dir` | `/tmp/gludd-e2e-test-gen` | Input directory (expects validated_scenarios.json) |
| `output_dir` | `tests/e2e/generated` | Where test files are written |
| `test_client_fixture` | `TestClient` | HTTP client fixture name |
| `cli_runner_fixture` | `_run_cli` | CLI runner fixture name |
| `tmp_path_fixture` | `tmp_path` | Temp directory fixture name |

## Output

- `test_e2e_generated_<scenario_name>.py` — one test file per scenario
- `generated_tests.json` — manifest of all generated test files

## Test structure

Generated tests follow the project's TDD conventions:
```python
import pytest
from tests.conftest import TestClient, _run_cli

def test_crud_lifecycle_create(client: TestClient):
    """Create a resource through the API — should return 201."""
    response = client.post("/api/resource", json={"name": "test"})
    assert response.status_code == 201
```
