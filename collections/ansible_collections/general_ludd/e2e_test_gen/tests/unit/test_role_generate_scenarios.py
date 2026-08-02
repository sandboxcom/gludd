"""Tests for generate_scenarios role — YAML structure, script invocation, ScenarioGenerator integration."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "generate_scenarios"
SCRIPT = ROLE_DIR / "files" / "generate_scenarios.py"
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
        assert "symbols_artifact" in data
        assert "target_module" in data
        assert "artifact_dir" in data

    def test_vars_is_valid_yaml(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "scenarios_artifact_name" in data
        assert "generate_script" in data

    def test_meta_is_valid_yaml(self):
        data = yaml.safe_load(META_YML.read_text(encoding="utf-8"))
        assert data["galaxy_info"]["role_name"] == "generate_scenarios"

    def test_script_exists(self):
        assert SCRIPT.is_file()


class TestScriptInvocation:
    def test_help_output(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--symbols-file" in result.stdout
        assert "--target-module" in result.stdout
        assert "--output" in result.stdout

    def test_with_symbols_file_creates_output(self):
        module_symbols = {
            "name": "test_module",
            "path": "/fake/test_module.py",
            "functions": [
                {"name": "create_user", "line_start": 10, "line_end": 15, "is_public": True},
                {"name": "delete_user", "line_start": 20, "line_end": 25, "is_public": True},
                {"name": "_internal", "line_start": 5, "line_end": 8, "is_public": False},
            ],
            "classes": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            symbols_file = Path(tmpdir) / "module_symbols.json"
            output_file = Path(tmpdir) / "scenarios.json"

            symbols_file.write_text(json.dumps(module_symbols))

            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--symbols-file", str(symbols_file),
                    "--output", str(output_file),
                ],
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert output_file.is_file()

            data = json.loads(output_file.read_text())
            assert data["status"] == "completed"
            assert data["scenario_count"] > 0
            assert "scenarios" in data
            assert "crud_lifecycle" in [s["name"] for s in data["scenarios"]]

    def test_with_target_module_creates_output(self):
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--target-module", str(SCRIPT),
                "--output", "/tmp/gludd-e2e-test-gen/test_scenarios.json",
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        result_json = json.loads(result.stdout)
        assert result_json["scenario_count"] >= 0

    def test_output_scenarios_have_required_fields(self):
        module_symbols = {
            "name": "test_module",
            "path": "/fake/test_module.py",
            "functions": [
                {"name": "login_user", "line_start": 10, "line_end": 15, "is_public": True},
            ],
            "classes": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            symbols_file = Path(tmpdir) / "module_symbols.json"
            output_file = Path(tmpdir) / "scenarios.json"

            symbols_file.write_text(json.dumps(module_symbols))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--symbols-file", str(symbols_file), "--output", str(output_file)],
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0

            data = json.loads(output_file.read_text())
            for scenario in data["scenarios"]:
                assert "name" in scenario
                assert "description" in scenario
                assert "steps" in scenario
                assert "coverage_targets" in scenario
                for step in scenario["steps"]:
                    assert "action" in step
                    assert "target" in step
                    assert "expected_result" in step
                    assert "assertions" in step

    def test_private_functions_are_excluded(self):
        module_symbols = {
            "name": "test_module",
            "path": "/fake/test_module.py",
            "functions": [
                {"name": "_private_func", "line_start": 1, "line_end": 5, "is_public": False},
            ],
            "classes": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            symbols_file = Path(tmpdir) / "module_symbols.json"
            output_file = Path(tmpdir) / "scenarios.json"

            symbols_file.write_text(json.dumps(module_symbols))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--symbols-file", str(symbols_file), "--output", str(output_file)],
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0

            data = json.loads(output_file.read_text())
            assert data["scenario_count"] == 0
