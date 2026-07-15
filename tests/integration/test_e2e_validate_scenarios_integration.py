"""Integration tests for the E2E validate_scenarios pipeline.

Covers: heuristic scoring, mock mode, daemon API calls (mocked), error handling,
threshold edge cases, and the end-to-end generate→validate→report workflow.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# Load the validate_scenarios module path
_VALIDATE_SCRIPT = (
    Path(__file__).parent.parent.parent
    / "collections/ansible_collections/general_ludd/e2e_test_gen"
    / "roles/validate_scenarios/files/validate_scenarios.py"
)


def _make_scenarios_json(scenarios: list[dict], module: str = "test_module", path: str = "src/test_module") -> str:
    return json.dumps({"module": module, "path": path, "scenarios": scenarios})


def _run_validate(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    output = tmp_path / "output.json"
    cmd = ["python3", str(_VALIDATE_SCRIPT), "--output", str(output), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output_path = output
    result.output_path = output_path  # type: ignore[attr-defined]
    return result


@pytest.fixture
def scenarios_file(tmp_path: Path) -> Path:
    filepath = tmp_path / "scenarios.json"
    return filepath


@pytest.fixture
def output_file(tmp_path: Path) -> Path:
    return tmp_path / "validated_output.json"


class TestHeuristicScoring:
    def test_crud_lifecycle_high_confidence(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{
            "name": "crud_lifecycle",
            "description": "create read update delete api lifecycle test",
            "coverage_targets": ["src/api.py", "src/models.py"],
        }])
        scenarios_file.write_text(scenarios)
        result = subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(output_file.read_text())
        assert data["valid_count"] == 1
        assert len(data["valid"]) == 1
        assert data["valid"][0]["confidence"] >= 0.7

    def test_auth_flow_high_confidence(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{
            "name": "auth_flow",
            "description": "login token auth session oauth flow test",
            "coverage_targets": ["src/auth.py"],
        }])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert data["valid"][0]["confidence"] >= 0.75

    def test_concurrent_edits_with_keywords(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{
            "name": "concurrent_edits",
            "description": "lock mutex atomic concurrent transaction test",
            "coverage_targets": [],
        }])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert data["valid"][0]["confidence"] >= 0.75

    def test_unknown_pattern_low_confidence(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{
            "name": "unknown_pattern",
            "description": "something completely novel never seen before",
            "coverage_targets": [],
        }])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert data["valid"][0]["confidence"] == 0.4


class TestThresholdEdgeCases:
    def test_everything_discarded_at_threshold_1(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([
            {"name": "crud_lifecycle", "description": "create read update delete", "coverage_targets": []},
            {"name": "auth_flow", "description": "login token auth session", "coverage_targets": []},
        ])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock", "--confidence-threshold", "1.0"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert data["valid_count"] == 0
        assert data["discarded_count"] == 2

    def test_everything_passes_at_threshold_zero(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([
            {"name": "unknown_1", "description": "foo bar baz", "coverage_targets": []},
            {"name": "unknown_2", "description": "biz buz", "coverage_targets": []},
        ])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock", "--confidence-threshold", "0.0"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert data["valid_count"] == 2

    def test_boundary_at_default_threshold(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([
            {"name": "crud_lifecycle", "description": "create read update delete", "coverage_targets": []},
            {"name": "unknown_pattern", "description": "nothing known here", "coverage_targets": []},
        ])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert data["valid_count"] == 1
        assert data["discarded_count"] == 1
        assert data["discarded"][0]["name"] == "unknown_pattern"


class TestOutputStructure:
    def test_valid_output_has_all_fields(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{
            "name": "daemon_restart",
            "description": "init startup shutdown restart reload",
            "coverage_targets": [],
        }])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert "module" in data
        assert "path" in data
        assert "valid" in data
        assert "discarded" in data
        assert "valid_count" in data
        assert "discarded_count" in data
        assert "research_queries" in data
        assert "confidence_threshold" in data
        assert "status" in data
        assert data["status"] == "completed"

    def test_each_valid_entry_has_confidence_and_source_urls(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([
            {"name": "timeout_handling", "description": "timeout retry backoff circuit test", "coverage_targets": []},
            {"name": "daemon_restart", "description": "startup shutdown restart reload", "coverage_targets": []},
        ])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        for entry in data["valid"]:
            assert "confidence" in entry
            assert isinstance(entry["confidence"], float)
            assert "source_urls" in entry
            assert isinstance(entry["source_urls"], list)

    def test_discarded_entries_have_reason(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{
            "name": "unknown_pattern", "description": "mystery", "coverage_targets": [],
        }])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        for entry in data["discarded"]:
            assert "reason" in entry
            assert "confidence" in str(entry["reason"]).lower() or entry["name"] in data["discarded"][0]["name"]


class TestResearchQueries:
    def test_queries_generated_per_scenario(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([
            {"name": "crud_lifecycle", "description": "CRUD ops", "coverage_targets": ["api"]},
            {"name": "auth_flow", "description": "login flow", "coverage_targets": ["auth"]},
        ])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert len(data["research_queries"]) == 2
        for q in data["research_queries"]:
            assert "how is" in q.lower()
            assert "tested in production" in q.lower()

    def test_coverage_targets_in_queries(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{
            "name": "crud_lifecycle",
            "description": "test",
            "coverage_targets": ["src/api.py", "src/models.py"],
        }])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        assert "src/api.py src/models.py" in data["research_queries"][0]


class TestErrorHandling:
    def test_missing_scenarios_file(self, output_file: Path):
        result = subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", "/nonexistent/path.json",
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0

    def test_malformed_scenarios_json(self, scenarios_file: Path, output_file: Path):
        scenarios_file.write_text("not valid json {{{")
        result = subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0

    def test_empty_scenarios_list(self, scenarios_file: Path, output_file: Path):
        scenarios = json.dumps({"module": "test", "path": "", "scenarios": []})
        scenarios_file.write_text(scenarios)
        result = subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(output_file.read_text())
        assert data["valid_count"] == 0
        assert data["discarded_count"] == 0

    def test_missing_required_args(self):
        result = subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0

    def test_daemon_unreachable_graceful_fallback(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{"name": "crud_lifecycle", "description": "test", "coverage_targets": []}])
        scenarios_file.write_text(scenarios)
        result = subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock", "--daemon-url", "http://127.0.0.1:19999"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(output_file.read_text())
        assert data["valid_count"] == 1

    def test_partial_rounding_confidence_values(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([{
            "name": "concurrent_edits",
            "description": "lock mutex atomic concurrent transaction",
            "coverage_targets": [],
        }])
        scenarios_file.write_text(scenarios)
        subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(output_file.read_text())
        conf = data["valid"][0]["confidence"]
        assert conf == round(conf, 2)


class TestFullPipelineIntegration:
    def test_generate_validate_report_end_to_end(self, scenarios_file: Path, output_file: Path):
        scenarios = _make_scenarios_json([
            {"name": "crud_lifecycle", "description": "create read update delete api test",
             "coverage_targets": ["src/api.py"]},
            {"name": "auth_flow", "description": "login token auth session oauth test",
             "coverage_targets": ["src/auth.py"]},
            {"name": "timeout_handling", "description": "timeout retry backoff circuit test",
             "coverage_targets": ["src/client.py"]},
            {"name": "unknown_pattern", "description": "novel testing pattern",
             "coverage_targets": []},
            {"name": "daemon_restart", "description": "init startup shutdown restart reload test",
             "coverage_targets": ["src/daemon.py"]},
        ])
        scenarios_file.write_text(scenarios)

        result = subprocess.run(
            ["python3", str(_VALIDATE_SCRIPT), "--scenarios-file", str(scenarios_file),
             "--output", str(output_file), "--mock", "--confidence-threshold", "0.5"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0

        data = json.loads(output_file.read_text())
        assert data["status"] == "completed"
        assert data["valid_count"] + data["discarded_count"] == 5

        stdout = json.loads(result.stdout.strip())
        assert stdout["valid_count"] == data["valid_count"]
        assert stdout["discarded_count"] == data["discarded_count"]
        assert str(output_file) in stdout["output"]

        for entry in data["valid"]:
            assert entry["confidence"] >= 0.5

        for entry in data["discarded"]:
            assert entry["confidence"] < 0.5
