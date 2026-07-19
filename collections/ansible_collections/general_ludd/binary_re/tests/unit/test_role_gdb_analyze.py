"""Tests for gdb_analyze role — YAML structure, script invocation, command generation."""

from __future__ import annotations

import json
import subprocess
import sys
import yaml
from pathlib import Path


COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "gdb_analyze"
GDB_SCRIPT = ROLE_DIR / "files" / "gdb_analyze.py"
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

    def test_vars_is_valid_yaml(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_meta_is_valid_yaml(self):
        data = yaml.safe_load(META_YML.read_text(encoding="utf-8"))
        assert data["galaxy_info"]["role_name"] == "gdb_analyze"

    def test_script_exists(self):
        assert GDB_SCRIPT.is_file()

    def test_subtask_files_exist(self):
        for name in (
            "breakpoint_analysis.yml",
            "stack_trace.yml",
            "register_dump.yml",
            "scripted_analysis.yml",
        ):
            assert (ROLE_DIR / "tasks" / name).is_file(), f"missing {name}"

    def test_defaults_define_required_vars(self):
        data = yaml.safe_load(DEFAULTS_YML.read_text(encoding="utf-8"))
        assert "gdb_path" in data
        assert "output_dir" in data
        assert "target_binary" in data

    def test_vars_define_script_path(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert "script_path" in data


class TestScriptInvocation:
    def test_help_output(self):
        result = subprocess.run(
            [sys.executable, str(GDB_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--target" in result.stdout
        assert "--mode" in result.stdout
        assert "--output" in result.stdout

    def test_invalid_mode(self):
        result = subprocess.run(
            [
                sys.executable, str(GDB_SCRIPT),
                "--target", "/bin/ls", "--mode", "bogus",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0

    def test_missing_target(self):
        result = subprocess.run(
            [sys.executable, str(GDB_SCRIPT), "--mode", "breakpoint"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0


class TestBreakpointMode:
    def test_generates_breakpoint_commands(self):
        result = subprocess.run(
            [
                sys.executable, str(GDB_SCRIPT),
                "--target", "/bin/ls", "--mode", "breakpoint",
                "--breakpoints", "main,exit",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["mode"] == "breakpoint"
        assert "commands" in output
        assert isinstance(output["commands"], list)
        assert any("break" in c for c in output["commands"])

    def test_breakpoint_includes_run(self):
        result = subprocess.run(
            [
                sys.executable, str(GDB_SCRIPT),
                "--target", "/bin/ls", "--mode", "breakpoint",
                "--breakpoints", "main",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert any("run" in c.lower() for c in output["commands"])


class TestStackTraceMode:
    def test_generates_bt_commands(self):
        result = subprocess.run(
            [
                sys.executable, str(GDB_SCRIPT),
                "--target", "/bin/ls", "--mode", "stack_trace",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "stack_trace"
        assert any("bt" in c or "backtrace" in c for c in output["commands"])


class TestRegisterDumpMode:
    def test_generates_info_registers(self):
        result = subprocess.run(
            [
                sys.executable, str(GDB_SCRIPT),
                "--target", "/bin/ls", "--mode", "register_dump",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "register_dump"
        joined = " ".join(output["commands"])
        assert "registers" in joined.lower()


class TestScriptedMode:
    def test_generates_python_api_script(self):
        result = subprocess.run(
            [
                sys.executable, str(GDB_SCRIPT),
                "--target", "/bin/ls", "--mode", "scripted",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["mode"] == "scripted"
        assert "script" in output
        assert "gdb" in output["script"].lower()


class TestArtifactFormat:
    def test_required_fields(self):
        result = subprocess.run(
            [
                sys.executable, str(GDB_SCRIPT),
                "--target", "/bin/ls", "--mode", "breakpoint",
                "--breakpoints", "main",
            ],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert "target" in output
        assert "mode" in output
        assert "commands" in output
        assert "backend" in output

    def test_output_to_file(self, tmp_path):
        out_file = tmp_path / "gdb_bp.json"
        result = subprocess.run(
            [
                sys.executable, str(GDB_SCRIPT),
                "--target", "/bin/ls", "--mode", "breakpoint",
                "--breakpoints", "main",
                "--output", str(out_file),
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        loaded = json.loads(out_file.read_text(encoding="utf-8"))
        assert loaded["mode"] == "breakpoint"
