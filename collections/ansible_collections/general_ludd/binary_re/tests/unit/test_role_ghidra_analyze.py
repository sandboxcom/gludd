"""Tests for ghidra_analyze role — YAML structure, analyzeHeadless invocation generation."""

from __future__ import annotations

import json
import subprocess
import sys
import yaml
from pathlib import Path


COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "ghidra_analyze"
GHIDRA_SCRIPT = ROLE_DIR / "files" / "ghidra_analyze.py"
TASKS_YML = ROLE_DIR / "tasks" / "main.yml"
DEFAULTS_YML = ROLE_DIR / "defaults" / "main.yml"
VARS_YML = ROLE_DIR / "vars" / "main.yml"
META_YML = ROLE_DIR / "meta" / "main.yml"


class TestRoleStructure:
    def test_task_file_is_valid_yaml(self):
        docs = list(yaml.safe_load_all(TASKS_YML.read_text(encoding="utf-8")))
        assert len(docs) >= 1

    def test_defaults_is_valid_yaml(self):
        assert isinstance(yaml.safe_load(DEFAULTS_YML.read_text(encoding="utf-8")), dict)

    def test_vars_is_valid_yaml(self):
        assert isinstance(yaml.safe_load(VARS_YML.read_text(encoding="utf-8")), dict)

    def test_meta_is_valid_yaml(self):
        data = yaml.safe_load(META_YML.read_text(encoding="utf-8"))
        assert data["galaxy_info"]["role_name"] == "ghidra_analyze"

    def test_script_exists(self):
        assert GHIDRA_SCRIPT.is_file()

    def test_subtask_files_exist(self):
        for name in ("headless_analysis.yml", "scripted_export.yml", "function_signature.yml"):
            assert (ROLE_DIR / "tasks" / name).is_file(), f"missing {name}"

    def test_defaults_define_required_vars(self):
        data = yaml.safe_load(DEFAULTS_YML.read_text(encoding="utf-8"))
        assert "ghidra_path" in data
        assert "output_dir" in data
        assert "target_binary" in data

    def test_vars_define_script_path(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert "script_path" in data


class TestScriptInvocation:
    def test_help_output(self):
        result = subprocess.run(
            [sys.executable, str(GHIDRA_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--target" in result.stdout
        assert "--mode" in result.stdout

    def test_invalid_mode(self):
        result = subprocess.run(
            [sys.executable, str(GHIDRA_SCRIPT), "--target", "/bin/ls", "--mode", "bogus"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0

    def test_missing_target(self):
        result = subprocess.run(
            [sys.executable, str(GHIDRA_SCRIPT), "--mode", "headless_analysis"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0


class TestHeadlessAnalysisMode:
    def test_generates_analyze_headless_command(self):
        result = subprocess.run(
            [
                sys.executable, str(GHIDRA_SCRIPT),
                "--target", "/bin/ls", "--mode", "headless_analysis",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "headless_analysis"
        assert "invocation" in output
        assert "analyzeHeadless" in output["invocation"]

    def test_uses_project_dir(self):
        result = subprocess.run(
            [
                sys.executable, str(GHIDRA_SCRIPT),
                "--target", "/bin/ls", "--mode", "headless_analysis",
                "--project-dir", "/tmp/gludproj",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert "/tmp/gludproj" in output["invocation"]


class TestScriptedExportMode:
    def test_generates_postscript(self):
        result = subprocess.run(
            [
                sys.executable, str(GHIDRA_SCRIPT),
                "--target", "/bin/ls", "--mode", "scripted_export",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "scripted_export"
        assert "postscript" in output
        assert "decompile" in output["postscript"].lower() or "export" in output["postscript"].lower()


class TestFunctionSignatureMode:
    def test_generates_signature_query(self):
        result = subprocess.run(
            [
                sys.executable, str(GHIDRA_SCRIPT),
                "--target", "/bin/ls", "--mode", "function_signature",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "function_signature"
        assert "postscript" in output
        assert "function" in output["postscript"].lower()


class TestArtifactFormat:
    def test_required_fields(self):
        result = subprocess.run(
            [
                sys.executable, str(GHIDRA_SCRIPT),
                "--target", "/bin/ls", "--mode", "headless_analysis",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert "target" in output
        assert "mode" in output
        assert "backend" in output

    def test_output_to_file(self, tmp_path):
        out_file = tmp_path / "ghidra.json"
        result = subprocess.run(
            [
                sys.executable, str(GHIDRA_SCRIPT),
                "--target", "/bin/ls", "--mode", "headless_analysis",
                "--output", str(out_file),
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        loaded = json.loads(out_file.read_text(encoding="utf-8"))
        assert loaded["mode"] == "headless_analysis"
