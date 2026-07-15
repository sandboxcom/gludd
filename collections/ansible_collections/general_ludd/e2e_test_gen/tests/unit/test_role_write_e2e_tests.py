"""Tests for write_e2e_tests role — YAML structure, script invocation, test file generation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "write_e2e_tests"
SCRIPT = ROLE_DIR / "files" / "write_e2e_tests.py"
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
        assert "output_dir" in data
        assert "test_client_fixture" in data
        assert "test_file_prefix" in data

    def test_vars_is_valid_yaml(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "generated_tests_manifest" in data
        assert "write_tests_script" in data

    def test_meta_is_valid_yaml(self):
        data = yaml.safe_load(META_YML.read_text(encoding="utf-8"))
        assert data["galaxy_info"]["role_name"] == "write_e2e_tests"

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
        assert "--output-dir" in result.stdout
        assert "--test-file-prefix" in result.stdout

    def test_generates_test_files_from_scenarios(self):
        validated = {
            "module": "test",
            "path": "/fake/test.py",
            "valid": [
                {
                    "name": "crud_lifecycle",
                    "description": "CRUD test",
                    "steps": [
                        {"action": "POST", "target": "/api/resource", "expected_result": "201 Created", "assertions": ["status == 201"]},
                        {"action": "GET", "target": "/api/resource/<id>", "expected_result": "200 OK", "assertions": ["status == 200"]},
                    ],
                    "coverage_targets": ["create_user"],
                },
            ],
            "discarded": [],
            "valid_count": 1,
            "discarded_count": 0,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios_file = Path(tmpdir) / "validated_scenarios.json"
            output_dir = Path(tmpdir) / "generated_tests"
            manifest_file = Path(tmpdir) / "generated_tests.json"
            scenarios_file.write_text(json.dumps(validated))

            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--scenarios-file", str(scenarios_file),
                    "--output-dir", str(output_dir),
                    "--manifest", str(manifest_file),
                ],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert output_dir.is_dir()

            test_files = list(output_dir.glob("test_e2e_generated_*.py"))
            assert len(test_files) >= 1

            manifest = json.loads(manifest_file.read_text())
            assert manifest["scenario_count"] == 1
            assert len(manifest["test_files"]) >= 1

    def test_generated_test_file_is_syntactically_valid(self):
        validated = {
            "module": "test",
            "path": "/fake/test.py",
            "valid": [
                {
                    "name": "simple_test",
                    "description": "A simple test",
                    "steps": [
                        {"action": "Invoke", "target": "my_func", "expected_result": "returns value", "assertions": ["result is not None"]},
                    ],
                    "coverage_targets": ["my_func"],
                },
            ],
            "discarded": [],
            "valid_count": 1,
            "discarded_count": 0,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios_file = Path(tmpdir) / "validated_scenarios.json"
            output_dir = Path(tmpdir) / "generated_tests"
            manifest_file = Path(tmpdir) / "generated_tests.json"
            scenarios_file.write_text(json.dumps(validated))

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--scenarios-file", str(scenarios_file), "--output-dir", str(output_dir), "--manifest", str(manifest_file)],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0

            test_files = list(output_dir.glob("test_e2e_generated_*.py"))
            assert len(test_files) == 1

            content = test_files[0].read_text()
            assert "import pytest" in content
            assert "def test_" in content
            assert '"""' in content

    def test_multiple_scenarios_generate_multiple_files(self):
        validated = {
            "module": "test",
            "valid": [
                {
                    "name": "scenario_a",
                    "description": "First scenario",
                    "steps": [{"action": "run", "target": "a", "expected_result": "ok", "assertions": []}],
                    "coverage_targets": ["a"],
                },
                {
                    "name": "scenario_b",
                    "description": "Second scenario",
                    "steps": [{"action": "run", "target": "b", "expected_result": "ok", "assertions": []}],
                    "coverage_targets": ["b"],
                },
            ],
            "discarded": [],
            "valid_count": 2,
            "discarded_count": 0,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios_file = Path(tmpdir) / "validated_scenarios.json"
            output_dir = Path(tmpdir) / "generated_tests"
            manifest_file = Path(tmpdir) / "generated_tests.json"
            scenarios_file.write_text(json.dumps(validated))

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--scenarios-file", str(scenarios_file), "--output-dir", str(output_dir), "--manifest", str(manifest_file)],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0

            test_files = list(output_dir.glob("test_e2e_generated_*.py"))
            assert len(test_files) == 2

    def test_manifest_has_step_count(self):
        validated = {
            "module": "test",
            "valid": [
                {
                    "name": "multi_step",
                    "description": "Multi step scenario",
                    "steps": [
                        {"action": "a", "target": "x", "expected_result": "ok", "assertions": []},
                        {"action": "b", "target": "y", "expected_result": "ok", "assertions": []},
                        {"action": "c", "target": "z", "expected_result": "ok", "assertions": []},
                    ],
                    "coverage_targets": ["x", "y", "z"],
                },
            ],
            "discarded": [],
            "valid_count": 1,
            "discarded_count": 0,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios_file = Path(tmpdir) / "validated_scenarios.json"
            output_dir = Path(tmpdir) / "generated_tests"
            manifest_file = Path(tmpdir) / "generated_tests.json"
            scenarios_file.write_text(json.dumps(validated))
            subprocess.run(
                [sys.executable, str(SCRIPT), "--scenarios-file", str(scenarios_file), "--output-dir", str(output_dir), "--manifest", str(manifest_file)],
                capture_output=True, timeout=15,
            )

            manifest = json.loads(manifest_file.read_text())
            assert manifest["test_files"][0]["step_count"] == 3
