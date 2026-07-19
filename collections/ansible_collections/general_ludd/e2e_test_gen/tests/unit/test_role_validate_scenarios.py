"""Tests for validate_scenarios role — YAML structure, script invocation, heuristic confidence scoring."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "validate_scenarios"
SCRIPT = ROLE_DIR / "files" / "validate_scenarios.py"
TASKS_YML = ROLE_DIR / "tasks" / "main.yml"
DEFAULTS_YML = ROLE_DIR / "defaults" / "main.yml"
VARS_YML = ROLE_DIR / "vars" / "main.yml"
META_YML = ROLE_DIR / "meta" / "main.yml"


class TestRoleStructure:
    def test_task_file_is_valid_yaml(self):
        content = TASKS_YML.read_text(encoding="utf-8")
        assert content.strip()
        docs = list(yaml.safe_load_all(content))
        assert len(docs) >= 1

    def test_defaults_is_valid_yaml(self):
        data = yaml.safe_load(DEFAULTS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "scenarios_artifact" in data
        assert "confidence_threshold" in data
        assert "artifact_dir" in data

    def test_vars_is_valid_yaml(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "validated_artifact_name" in data
        assert "validate_script" in data

    def test_meta_is_valid_yaml(self):
        data = yaml.safe_load(META_YML.read_text(encoding="utf-8"))
        assert data["galaxy_info"]["role_name"] == "validate_scenarios"

    def test_script_exists(self):
        assert SCRIPT.is_file()


class TestScriptInvocation:
    def test_help_output(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--scenarios-file" in result.stdout
        assert "--output" in result.stdout
        assert "--confidence-threshold" in result.stdout

    def test_high_confidence_scenario_passes_threshold(self):
        scenarios = {
            "module": "test",
            "path": "/fake/test.py",
            "scenarios": [
                {
                    "name": "crud_lifecycle",
                    "description": "Create, read, update, delete API resources",
                    "steps": [
                        {"action": "POST", "target": "/api/resource", "expected_result": "201 Created", "assertions": []},
                    ],
                    "coverage_targets": ["create_user"],
                },
            ],
            "scenario_count": 1,
            "status": "completed",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios_file = Path(tmpdir) / "scenarios.json"
            output_file = Path(tmpdir) / "validated_scenarios.json"
            scenarios_file.write_text(json.dumps(scenarios))

            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--scenarios-file", str(scenarios_file),
                    "--output", str(output_file),
                    "--confidence-threshold", "0.4",
                ],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert output_file.is_file()

            data = json.loads(output_file.read_text())
            assert data["status"] == "completed"
            assert data["valid_count"] >= 1
            assert data["discarded_count"] == 0
            assert data["valid"][0]["confidence"] >= 0.4

    def test_low_confidence_scenario_below_threshold(self):
        scenarios = {
            "module": "test",
            "path": "/fake/test.py",
            "scenarios": [
                {
                    "name": "unknown_pattern",
                    "description": "some generic operation with no keyword matches",
                    "steps": [
                        {"action": "run", "target": "thing", "expected_result": "works", "assertions": []},
                    ],
                    "coverage_targets": ["some_func"],
                },
            ],
            "scenario_count": 1,
            "status": "completed",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios_file = Path(tmpdir) / "scenarios.json"
            output_file = Path(tmpdir) / "validated_scenarios.json"
            scenarios_file.write_text(json.dumps(scenarios))

            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--scenarios-file", str(scenarios_file),
                    "--output", str(output_file),
                    "--confidence-threshold", "0.5",
                ],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"

            data = json.loads(output_file.read_text())
            assert data["discarded_count"] >= 1

    def test_auth_flow_scenario_gets_high_confidence(self):
        scenarios = {
            "module": "test",
            "path": "/fake/test.py",
            "scenarios": [
                {
                    "name": "auth_flow",
                    "description": "Authenticate users with login token and session management",
                    "steps": [],
                    "coverage_targets": ["login", "token_refresh"],
                },
            ],
            "scenario_count": 1,
            "status": "completed",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios_file = Path(tmpdir) / "scenarios.json"
            output_file = Path(tmpdir) / "validated_scenarios.json"
            scenarios_file.write_text(json.dumps(scenarios))

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--scenarios-file", str(scenarios_file), "--output", str(output_file)],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0

            data = json.loads(output_file.read_text())
            assert data["valid_count"] == 1
            assert data["valid"][0]["confidence"] >= 0.75

    def test_output_has_required_fields(self):
        scenarios = {
            "module": "test",
            "path": "/fake/test.py",
            "scenarios": [
                {
                    "name": "crud_lifecycle",
                    "description": "Create, read, update, delete resources",
                    "steps": [],
                    "coverage_targets": ["create"],
                },
            ],
            "scenario_count": 1,
            "status": "completed",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios_file = Path(tmpdir) / "scenarios.json"
            output_file = Path(tmpdir) / "validated_scenarios.json"
            scenarios_file.write_text(json.dumps(scenarios))

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--scenarios-file", str(scenarios_file), "--output", str(output_file)],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0

            data = json.loads(output_file.read_text())
            for field in ["valid", "discarded", "valid_count", "discarded_count", "research_queries", "confidence_threshold", "status"]:
                assert field in data, f"Missing field: {field}"
