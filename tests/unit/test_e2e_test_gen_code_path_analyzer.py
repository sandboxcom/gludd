"""Self-tests for analyze_code_paths files/ script — dogfooding.

Tests that the standalone ast-based analyzer correctly extracts symbols
when run on ITSELF. Uses subprocess invocation with --target-module
and --output args matching the script's CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ANALYZER_SCRIPT = Path(__file__).resolve().parent.parent.parent / (
    "collections/ansible_collections/general_ludd/e2e_test_gen/"
    "roles/analyze_code_paths/files/analyze_code_paths.py"
)


def _run_analyzer(target: str, output: str, *, ast_only: bool = True) -> dict:
    cmd = [sys.executable, str(ANALYZER_SCRIPT),
           "--target-module", target,
           "--output", output]
    if ast_only:
        cmd.append("--ast-only")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        pytest.fail(f"analyzer failed (rc={result.returncode}):\n{result.stderr}")
    with open(output) as f:
        return json.load(f)


def _get_source_symbols(data: dict) -> dict:
    return data["source"]


def _get_functions(data: dict) -> list[dict]:
    return _get_source_symbols(data)["functions"]


def _get_classes(data: dict) -> list[dict]:
    return _get_source_symbols(data)["classes"]


class TestDogfoodSelfAnalysis:
    def test_analyzes_own_script(self, tmp_path: Path) -> None:
        out = str(tmp_path / "result.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        assert data["status"] == "completed"
        assert data["parser"] == "ast"
        assert data["module"] == "analyze_code_paths.py"

    def test_finds_own_functions(self, tmp_path: Path) -> None:
        out = str(tmp_path / "funcs.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        func_names = {f["name"] for f in _get_functions(data)}
        assert func_names == {"main", "_extract_calls", "_call_name"}

    def test_finds_own_classes(self, tmp_path: Path) -> None:
        out = str(tmp_path / "classes.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        class_names = {c["name"] for c in _get_classes(data)}
        assert "_FunctionInfo" in class_names
        assert "_ClassInfo" in class_names
        assert "_ImportInfo" in class_names
        assert "_CodePathAnalyzerAST" in class_names

    def test_finds_own_methods(self, tmp_path: Path) -> None:
        out = str(tmp_path / "methods.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        analyzer_cls = next(c for c in _get_classes(data) if c["name"] == "_CodePathAnalyzerAST")
        method_names = {m["name"] for m in analyzer_cls["methods"]}
        assert "analyze" in method_names
        assert "to_dict" in method_names
        assert "call_graph" in method_names
        assert "testable_paths" in method_names
        assert "_walk" in method_names
        assert "_handle_function" in method_names
        assert "_handle_class" in method_names

    def test_public_private_classification_self(self, tmp_path: Path) -> None:
        out = str(tmp_path / "vis.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        funcs_by_name = {f["name"]: f for f in _get_functions(data)}
        assert funcs_by_name["main"]["is_public"] is True
        assert funcs_by_name["_extract_calls"]["is_public"] is False
        assert funcs_by_name["_call_name"]["is_public"] is False

    def test_private_class_is_flagged(self, tmp_path: Path) -> None:
        sample = tmp_path / "_sample_for_test.py"
        sample.write_text("class _Hidden:\n    def secret(self):\n        pass\n")
        out = str(tmp_path / "sample.json")
        data = _run_analyzer(str(sample), out)
        classes = _get_classes(data)
        cls = next(c for c in classes if c["name"] == "_Hidden")
        assert cls["is_public"] is False

    def test_async_function_detected(self, tmp_path: Path) -> None:
        sample = tmp_path / "_async_sample.py"
        sample.write_text("async def fetch_data():\n    return 42\n")
        out = str(tmp_path / "async.json")
        data = _run_analyzer(str(sample), out)
        func_names = {f["name"] for f in _get_functions(data)}
        assert "fetch_data" in func_names

    def test_empty_file_produces_empty_lists(self, tmp_path: Path) -> None:
        sample = tmp_path / "_empty_sample.py"
        sample.write_text("x = 1\ny = 2\n")
        out = str(tmp_path / "empty.json")
        data = _run_analyzer(str(sample), out)
        assert _get_functions(data) == []
        assert _get_classes(data) == []


class TestArtifactOutput:
    def test_writes_json_artifact(self, tmp_path: Path) -> None:
        out = str(tmp_path / "result.json")
        _run_analyzer(str(ANALYZER_SCRIPT), out)
        assert Path(out).exists()
        with open(out) as f:
            loaded = json.load(f)
        assert "source" in loaded
        assert "module" in loaded


class TestSchemaShape:
    def test_output_has_required_top_keys(self, tmp_path: Path) -> None:
        out = str(tmp_path / "schema.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        for key in ("module", "path", "source", "imports", "call_graph",
                     "testable_paths", "testable_path_count",
                     "function_count", "class_count", "parser", "status"):
            assert key in data, f"missing top-level key: {key}"

    def test_source_symbols_have_correct_types(self, tmp_path: Path) -> None:
        out = str(tmp_path / "types.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        syms = _get_source_symbols(data)
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

    def test_line_end_after_line_start(self, tmp_path: Path) -> None:
        out = str(tmp_path / "lines.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        syms = _get_source_symbols(data)
        for f in syms.get("functions", []):
            assert f["line_end"] >= f["line_start"], f"function {f['name']}: end < start"
        for c in syms.get("classes", []):
            assert c["line_end"] >= c["line_start"], f"class {c['name']}: end < start"
            for m in c["methods"]:
                assert m["line_end"] >= m["line_start"], f"method {c['name']}.{m['name']}: end < start"

    def test_testable_paths_are_public(self, tmp_path: Path) -> None:
        out = str(tmp_path / "paths.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        for tp in data.get("testable_paths", []):
            assert tp["type"] in ("function", "method")
            assert len(tp["line_range"]) == 2

    def test_call_graph_is_dict(self, tmp_path: Path) -> None:
        out = str(tmp_path / "calls.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        assert isinstance(data.get("call_graph"), dict)

    def test_imports_are_extracted(self, tmp_path: Path) -> None:
        out = str(tmp_path / "imports.json")
        data = _run_analyzer(str(ANALYZER_SCRIPT), out)
        # Script imports argparse, ast, json, sys, etc.
        import_modules = {imp["module"] for imp in data.get("imports", [])}
        assert "argparse" in import_modules
        assert "ast" in import_modules
        assert "json" in import_modules
        assert "pathlib" in import_modules


class TestCliErrors:
    def test_missing_file_exits_nonzero(self, tmp_path: Path) -> None:
        out = str(tmp_path / "none.json")
        result = subprocess.run(
            [sys.executable, str(ANALYZER_SCRIPT),
             "--target-module", "/nonexistent/path.py",
             "--output", out, "--ast-only"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0

    def test_non_python_file_errors(self, tmp_path: Path) -> None:
        sample = tmp_path / "_not_python.txt"
        sample.write_text("this is not python code {{{")
        out = str(tmp_path / "bad.json")
        result = subprocess.run(
            [sys.executable, str(ANALYZER_SCRIPT),
             "--target-module", str(sample),
             "--output", out, "--ast-only"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0

    def test_missing_required_args(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ANALYZER_SCRIPT)],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0


class TestDogfoodOnTestFile:
    def test_analyzes_this_test_file(self, tmp_path: Path) -> None:
        out = str(tmp_path / "test_dogfood.json")
        data = _run_analyzer(__file__, out)
        assert data["status"] == "completed"
        func_names = {f["name"] for f in _get_functions(data)}
        assert "_run_analyzer" in func_names
        assert "_get_source_symbols" in func_names
        assert "_get_functions" in func_names

    def test_own_tests_are_public(self, tmp_path: Path) -> None:
        out = str(tmp_path / "test_own.json")
        data = _run_analyzer(__file__, out)
        private = [f["name"] for f in _get_functions(data) if not f["is_public"]]
        assert "_run_analyzer" in private
        assert "_get_source_symbols" in private
        assert "_get_functions" in private
        assert "_get_classes" in private
