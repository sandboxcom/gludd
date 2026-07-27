"""Tests for analyze_code_paths role — YAML structure, script invocation, AST analysis."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile

import yaml
from pathlib import Path

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "analyze_code_paths"
SCRIPT = ROLE_DIR / "files" / "analyze_code_paths.py"
TASKS_YML = ROLE_DIR / "tasks" / "main.yml"
DEFAULTS_YML = ROLE_DIR / "defaults" / "main.yml"
VARS_YML = ROLE_DIR / "vars" / "main.yml"
META_YML = ROLE_DIR / "meta" / "main.yml"

SAMPLE_MODULE = """
import os
from pathlib import Path


def _helper(x: int) -> int:
    return x + 1


def public_func(x: int) -> int:
    result = _helper(x)
    return result * 2


class _PrivateClass:
    def internal(self) -> None:
        pass


class PublicWorker:
    def __init__(self, name: str) -> None:
        self.name = name

    def start(self) -> str:
        return f"started {self.name}"

    def stop(self) -> None:
        pass

    def _setup(self) -> None:
        pass


async def async_handler() -> dict:
    return {"status": "ok"}
"""


class TestRoleStructure:
    def test_task_file_is_valid_yaml(self):
        content = TASKS_YML.read_text(encoding="utf-8")
        assert content.strip()
        docs = list(yaml.safe_load_all(content))
        assert len(docs) >= 1

    def test_defaults_is_valid_yaml(self):
        data = yaml.safe_load(DEFAULTS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "target_module" in data
        assert "artifact_dir" in data

    def test_vars_is_valid_yaml(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "analyze_artifact_name" in data
        assert "analyze_script_module" in data

    def test_meta_is_valid_yaml(self):
        data = yaml.safe_load(META_YML.read_text(encoding="utf-8"))
        assert data["galaxy_info"]["role_name"] == "analyze_code_paths"

    def test_script_exists(self):
        assert SCRIPT.is_file()


class TestScriptInvocation:
    def test_help_output(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--target-module" in result.stdout
        assert "--output" in result.stdout
        assert "--ast-only" in result.stdout

    def test_file_not_found_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "out.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--target-module",
                    "/nonexistent/module.py",
                    "--output",
                    str(output_file),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode == 1
            data = json.loads(result.stdout)
            assert data["status"] == "failed"

    def test_end_to_end_produces_correct_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"

            stdout_data = json.loads(result.stdout)
            assert stdout_data["function_count"] >= 3
            assert stdout_data["class_count"] >= 2
            assert stdout_data["testable_path_count"] >= 2

            assert output_file.is_file()
            data = json.loads(output_file.read_text())
            assert data["status"] == "completed"
            assert data["module"] == "test_mod.py"

    def test_ast_only_uses_ast_parser(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--target-module",
                    str(sample_file),
                    "--output",
                    str(output_file),
                    "--ast-only",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0
            data = json.loads(output_file.read_text())
            assert data["parser"] == "ast"

    def test_syntax_error_file_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            broken_file = Path(tmpdir) / "broken.py"
            broken_file.write_text("def broken(::\n")
            output_file = Path(tmpdir) / "out.json"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(broken_file), "--output", str(output_file)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode == 1
            data = json.loads(result.stdout)
            assert data["status"] == "failed"
            assert "Syntax error" in data["error"]

    def test_public_functions_identified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            func_names = {f["name"] for f in data["source"]["functions"]}
            assert "public_func" in func_names
            assert "async_handler" in func_names
            assert "_helper" in func_names

    def test_private_class_flagged_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            classes = {c["name"]: c for c in data["source"]["classes"]}
            assert "_PrivateClass" in classes
            assert classes["_PrivateClass"]["is_public"] is False
            assert "PublicWorker" in classes
            assert classes["PublicWorker"]["is_public"] is True

    def test_class_methods_extracted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            worker = next(c for c in data["source"]["classes"] if c["name"] == "PublicWorker")
            method_names = {m["name"] for m in worker["methods"]}
            assert "__init__" in method_names
            assert "start" in method_names
            assert "stop" in method_names
            assert "_setup" in method_names

    def test_call_graph_extracted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            cg = data["call_graph"]
            assert isinstance(cg, dict)

    def test_testable_paths_only_public(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            targets = {p["target"] for p in data["testable_paths"]}
            assert "public_func" in targets
            assert "PublicWorker.start" in targets
            assert "_helper" not in targets
            assert "PublicWorker._setup" not in targets


class TestArtifactOutput:
    def test_has_all_required_top_level_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            required_keys = {
                "module",
                "path",
                "source",
                "imports",
                "call_graph",
                "testable_paths",
                "testable_path_count",
                "function_count",
                "class_count",
                "parser",
                "status",
            }
            assert required_keys <= set(data.keys())

    def test_source_symbols_have_correct_types(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            syms = data["source"]
            for f in syms.get("functions", []):
                assert isinstance(f["name"], str)
                assert isinstance(f["line_start"], int)
                assert isinstance(f["line_end"], int)
                assert isinstance(f["is_public"], bool)
            for c in syms.get("classes", []):
                assert isinstance(c["name"], str)
                assert isinstance(c["line_start"], int)
                assert isinstance(c["line_end"], int)
                assert isinstance(c["is_public"], bool)
                for m in c["methods"]:
                    assert isinstance(m["name"], str)
                    assert isinstance(m["line_start"], int)
                    assert isinstance(m["line_end"], int)
                    assert isinstance(m["is_public"], bool)

    def test_testable_paths_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            for tp in data.get("testable_paths", []):
                assert "target" in tp
                assert "type" in tp
                assert tp["type"] in ("function", "method")
                assert "line_range" in tp
                assert isinstance(tp["line_range"], list)
                assert len(tp["line_range"]) == 2
                assert "dependencies" in tp
                assert isinstance(tp["dependencies"], list)

    def test_line_end_after_line_start(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            syms = data["source"]
            for f in syms.get("functions", []):
                assert f["line_end"] >= f["line_start"]
            for c in syms.get("classes", []):
                assert c["line_end"] >= c["line_start"]
                for m in c["methods"]:
                    assert m["line_end"] >= m["line_start"]

    def test_empty_file_produces_empty_lists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "empty.py"
            sample_file.write_text("x = 1\ny = 2\n")
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            assert data["source"]["functions"] == []
            assert data["source"]["classes"] == []
            assert data["function_count"] == 0
            assert data["class_count"] == 0
            assert data["testable_path_count"] == 0

    def test_imports_are_extracted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "test_mod.py"
            sample_file.write_text(SAMPLE_MODULE)
            output_file = Path(tmpdir) / "module_symbols.json"

            subprocess.run(
                [sys.executable, str(SCRIPT), "--target-module", str(sample_file), "--output", str(output_file)],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(output_file.read_text())
            import_modules = {imp["module"] for imp in data.get("imports", [])}
            assert "os" in import_modules
            assert "pathlib" in import_modules
