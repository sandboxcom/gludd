"""Tests for radare2_analyze role — YAML structure, r2 command generation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "radare2_analyze"
R2_SCRIPT = ROLE_DIR / "files" / "radare2_analyze.py"
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
        assert data["galaxy_info"]["role_name"] == "radare2_analyze"

    def test_script_exists(self):
        assert R2_SCRIPT.is_file()

    def test_subtask_files_exist(self):
        for name in ("disassembly.yml", "entropy_scan.yml", "string_search.yml", "cfg_analysis.yml"):
            assert (ROLE_DIR / "tasks" / name).is_file(), f"missing {name}"

    def test_defaults_define_required_vars(self):
        data = yaml.safe_load(DEFAULTS_YML.read_text(encoding="utf-8"))
        assert "r2_path" in data
        assert "output_dir" in data
        assert "target_binary" in data

    def test_vars_define_script_path(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert "script_path" in data


class TestScriptInvocation:
    def test_help_output(self):
        result = subprocess.run(
            [sys.executable, str(R2_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--target" in result.stdout
        assert "--mode" in result.stdout

    def test_invalid_mode(self):
        result = subprocess.run(
            [sys.executable, str(R2_SCRIPT), "--target", "/bin/ls", "--mode", "bogus"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0

    def test_missing_target(self):
        result = subprocess.run(
            [sys.executable, str(R2_SCRIPT), "--mode", "disassembly"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0


class TestDisassemblyMode:
    def test_generates_disasm_commands(self):
        result = subprocess.run(
            [
                sys.executable, str(R2_SCRIPT),
                "--target", "/bin/ls", "--mode", "disassembly",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "disassembly"
        assert "commands" in output
        joined = " ".join(output["commands"])
        assert "aa" in joined or "pdf" in joined or "pd" in joined


class TestEntropyScanMode:
    def test_generates_entropy_commands(self):
        result = subprocess.run(
            [
                sys.executable, str(R2_SCRIPT),
                "--target", "/bin/ls", "--mode", "entropy_scan",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "entropy_scan"
        joined = " ".join(output["commands"])
        assert "entropy" in joined.lower() or "p=e" in joined


class TestStringSearchMode:
    def test_generates_strings_commands(self):
        result = subprocess.run(
            [
                sys.executable, str(R2_SCRIPT),
                "--target", "/bin/ls", "--mode", "string_search",
                "--string-regex", "password|secret",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "string_search"
        joined = " ".join(output["commands"])
        assert "iz" in joined or "/" in joined


class TestCFGMode:
    def test_generates_cfg_commands(self):
        result = subprocess.run(
            [
                sys.executable, str(R2_SCRIPT),
                "--target", "/bin/ls", "--mode", "cfg_analysis",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "cfg_analysis"
        joined = " ".join(output["commands"])
        assert "agf" in joined or "cfg" in joined.lower() or "graph" in joined.lower()


class TestArtifactFormat:
    def test_required_fields(self):
        result = subprocess.run(
            [
                sys.executable, str(R2_SCRIPT),
                "--target", "/bin/ls", "--mode", "disassembly",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert "target" in output
        assert "mode" in output
        assert "commands" in output
        assert "backend" in output

    def test_output_to_file(self, tmp_path):
        out_file = tmp_path / "r2.json"
        result = subprocess.run(
            [
                sys.executable, str(R2_SCRIPT),
                "--target", "/bin/ls", "--mode", "disassembly",
                "--output", str(out_file),
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        loaded = json.loads(out_file.read_text(encoding="utf-8"))
        assert loaded["mode"] == "disassembly"
