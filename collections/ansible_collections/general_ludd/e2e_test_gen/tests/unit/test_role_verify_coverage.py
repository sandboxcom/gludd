"""Tests for verify_coverage role — YAML structure, script invocation, coverage report generation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "verify_coverage"
SCRIPT = ROLE_DIR / "files" / "verify_coverage.py"
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
        assert "test_output_dir" in data
        assert "source_module" in data
        assert "coverage_threshold" in data
        assert "pytest_timeout" in data

    def test_vars_is_valid_yaml(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "coverage_artifact_name" in data
        assert "verify_coverage_script" in data

    def test_meta_is_valid_yaml(self):
        data = yaml.safe_load(META_YML.read_text(encoding="utf-8"))
        assert data["galaxy_info"]["role_name"] == "verify_coverage"

    def test_script_exists(self):
        assert SCRIPT.is_file()


class TestScriptInvocation:
    def test_help_output(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--test-dir" in result.stdout
        assert "--source-module" in result.stdout
        assert "--output" in result.stdout
        assert "--threshold" in result.stdout

    def test_no_test_files_produces_skip_verdict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "no_tests_here"
            test_dir.mkdir()
            output_file = Path(tmpdir) / "coverage_report.json"

            source_module = Path(tmpdir) / "dummy_source.py"
            source_module.write_text("def foo(): pass\n")

            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--test-dir", str(test_dir),
                    "--source-module", str(source_module),
                    "--output", str(output_file),
                    "--threshold", "85",
                    "--timeout", "30",
                ],
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert output_file.is_file()

            data = json.loads(output_file.read_text())
            assert data["verdict"] == "skip"

    def test_with_valid_tests_runs_and_reports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "generated_tests"
            test_dir.mkdir()
            output_file = Path(tmpdir) / "coverage_report.json"

            source_module = Path(tmpdir) / "adder.py"
            source_module.write_text("def add(a, b):\n    return a + b\n")

            test_file = test_dir / "test_e2e_generated_adder.py"
            test_file.write_text(f"""import sys
from pathlib import Path
sys.path.insert(0, "{tmpdir}")
from adder import add

def test_add_positive():
    assert add(2, 3) == 5

def test_add_negative():
    assert add(-1, 1) == 0
""")

            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--test-dir", str(test_dir),
                    "--source-module", str(source_module),
                    "--output", str(output_file),
                    "--threshold", "50",
                    "--timeout", "30",
                ],
                capture_output=True, text=True, timeout=60,
            )
            assert output_file.is_file(), f"stderr: {result.stderr}"

            data = json.loads(output_file.read_text())
            assert "verdict" in data
            assert "coverage_percent" in data
            assert "pytest_exit_code" in data
            assert data["status"] == "completed"

    def test_output_report_has_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "generated_tests"
            test_dir.mkdir()
            output_file = Path(tmpdir) / "coverage_report.json"

            source_module = Path(tmpdir) / "simple.py"
            source_module.write_text("def bar(): return 42\n")

            test_file = test_dir / "test_e2e_generated_simple.py"
            test_file.write_text(f"""import sys
sys.path.insert(0, "{tmpdir}")
from simple import bar
def test_bar():
    assert bar() == 42
""")

            subprocess.run(
                [sys.executable, str(SCRIPT), "--test-dir", str(test_dir), "--source-module", str(source_module), "--output", str(output_file), "--threshold", "50", "--timeout", "30"],
                capture_output=True, timeout=60,
            )

            data = json.loads(output_file.read_text())
            expected_fields = ["module", "test_output_dir", "coverage_percent", "threshold", "verdict", "verdict_reason", "pytest_exit_code", "coverage_targets", "status"]
            for field in expected_fields:
                assert field in data, f"Missing field: {field}"
