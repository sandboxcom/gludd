"""Unit tests for analyze_code_paths role script.

Tests the AST-based Python source analyzer that extracts functions, classes,
imports, call graphs, and testable code paths for E2E test generation.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = (
    ROOT
    / "collections/ansible_collections/general_ludd/e2e_test_gen"
    / "roles/analyze_code_paths/files/analyze_code_paths.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("analyze_code_paths", str(SCRIPT_PATH))
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


SAMPLE_MODULE = """
import os
import sys
from pathlib import Path

a_global = 1


def helper(x: int) -> int:
    return x + 1


def public_func(x: int) -> int:
    result = helper(x)
    path = Path(".")
    os.getcwd()
    return result * 2


class _PrivateClass:
    def internal(self) -> None:
        pass


class PublicWorker:
    def __init__(self, name: str) -> None:
        self.name = name

    def start(self) -> str:
        self._setup()
        return f"started {self.name}"

    def stop(self) -> None:
        pass

    def _setup(self) -> None:
        helper(1)


async def async_handler() -> dict:
    return {"status": "ok"}
"""


@pytest.fixture
def sample_py(tmp_path: Path) -> Path:
    p = tmp_path / "sample_module.py"
    p.write_text(SAMPLE_MODULE)
    return p


@pytest.fixture
def mod():
    return _load_module()


class TestImportInfo:
    def test_to_dict_simple(self, mod):
        info = mod._ImportInfo(module="os", names=["os"])
        assert info.to_dict() == {"module": "os", "names": ["os"]}

    def test_to_dict_from_import(self, mod):
        info = mod._ImportInfo(module="pathlib", names=["Path", "PurePath"])
        assert info.to_dict() == {"module": "pathlib", "names": ["Path", "PurePath"]}


class TestFunctionInfo:
    def test_to_dict(self, mod):
        fn = mod._FunctionInfo(name="do_work", line_start=10, line_end=25, is_public=True)
        d = fn.to_dict()
        assert d["name"] == "do_work"
        assert d["calls"] == []

    def test_to_dict_with_calls(self, mod):
        fn = mod._FunctionInfo(
            name="process", line_start=5, line_end=15, is_public=True, calls=["helper", "validate"]
        )
        d = fn.to_dict()
        assert d["calls"] == ["helper", "validate"]


class TestClassInfo:
    def test_to_dict(self, mod):
        cls = mod._ClassInfo(name="Worker", line_start=30, line_end=60, is_public=True)
        d = cls.to_dict()
        assert d["name"] == "Worker"
        assert d["methods"] == []

    def test_to_dict_with_methods(self, mod):
        m1 = mod._FunctionInfo(name="start", line_start=35, line_end=40, is_public=True)
        cls = mod._ClassInfo(name="Worker", line_start=30, line_end=60, is_public=True, methods=[m1])
        d = cls.to_dict()
        assert len(d["methods"]) == 1
        assert d["methods"][0]["name"] == "start"


class TestExtractCalls:
    def test_calls_from_simple_function(self, mod):
        import ast

        code = "def f(): foo(); bar()"
        tree = ast.parse(code)
        fn_def = tree.body[0]
        assert isinstance(fn_def, ast.FunctionDef)
        calls = mod._extract_calls(fn_def)
        assert sorted(calls) == ["bar", "foo"]

    def test_attribute_calls(self, mod):
        import ast

        code = "def f(): self.validate(); logger.info()"
        tree = ast.parse(code)
        fn_def = tree.body[0]
        assert isinstance(fn_def, ast.FunctionDef)
        calls = mod._extract_calls(fn_def)
        assert "self.validate" in calls
        assert "logger.info" in calls

    def test_empty_function(self, mod):
        import ast

        code = "def f(): pass"
        tree = ast.parse(code)
        fn_def = tree.body[0]
        assert isinstance(fn_def, ast.FunctionDef)
        assert mod._extract_calls(fn_def) == []


class TestCallName:
    def test_simple_name(self, mod):
        import ast

        expr = ast.parse("foo").body[0].value
        assert isinstance(expr, ast.Name)
        assert mod._call_name(expr) == "foo"

    def test_attribute_chain(self, mod):
        import ast

        stmt = ast.parse("x = self.logger.info()").body[0]
        assert isinstance(stmt, ast.Assign)
        assert isinstance(stmt.value, ast.Call)
        assert mod._call_name(stmt.value.func) == "self.logger.info"

    def test_nested_call(self, mod):
        import ast

        expr = ast.parse("os.path.join()").body[0].value
        assert isinstance(expr, ast.Call)
        assert mod._call_name(expr.func) == "os.path.join"


class TestCodePathAnalyzerAST:
    def test_extracts_functions(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        names = {f.name for f in ana.functions}
        assert "helper" in names
        assert "public_func" in names
        assert "async_handler" in names

    def test_public_private_classification(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        assert any(f.name == "public_func" and f.is_public for f in ana.functions)
        assert any(f.name == "helper" and f.is_public for f in ana.functions)
        _priv = next((c for c in ana.classes if c.name == "_PrivateClass"), None)
        assert _priv is not None
        assert not _priv.is_public

    def test_extracts_classes(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        class_names = {c.name for c in ana.classes}
        assert "_PrivateClass" in class_names
        assert "PublicWorker" in class_names

    def test_class_methods(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        worker = next(c for c in ana.classes if c.name == "PublicWorker")
        method_names = {m.name for m in worker.methods}
        assert "__init__" in method_names
        assert "start" in method_names
        assert "stop" in method_names
        assert "_setup" in method_names

    def test_extracts_imports(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        module_names = {i.module for i in ana.imports}
        assert "os" in module_names
        assert "sys" in module_names
        assert "pathlib" in module_names

    def test_call_graph(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        cg = ana.call_graph()
        assert "public_func" in cg
        assert "helper" in cg["public_func"]
        assert "PublicWorker.start" in cg
        assert "self._setup" in cg["PublicWorker.start"]

    def test_testable_paths_only_public(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        paths = ana.testable_paths()
        targets = {p["target"] for p in paths}
        assert "public_func" in targets
        assert "helper" in targets
        assert "PublicWorker.start" in targets
        assert "PublicWorker.stop" in targets
        assert "PublicWorker._setup" not in targets
        assert "PublicWorker.__init__" not in targets

    def test_testable_paths_structure(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        paths = ana.testable_paths()
        for p in paths:
            assert "target" in p
            assert "type" in p
            assert p["type"] in ("function", "method")
            assert "line_range" in p
            assert isinstance(p["line_range"], list)
            assert len(p["line_range"]) == 2
            assert "dependencies" in p
            assert isinstance(p["dependencies"], list)

    def test_to_dict_full(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(SAMPLE_MODULE)
        d = ana.to_dict()
        assert "functions" in d
        assert "classes" in d
        assert "imports" in d
        assert "call_graph" in d
        assert "testable_paths" in d
        assert len(d["functions"]) > 0
        assert len(d["classes"]) > 0

    def test_empty_source(self, mod):
        ana = mod._CodePathAnalyzerAST()
        ana.analyze("")
        assert ana.functions == []
        assert ana.classes == []
        assert ana.imports == []
        assert ana.call_graph() == {}

    def test_syntax_error_raises(self, mod):
        ana = mod._CodePathAnalyzerAST()
        with pytest.raises(SyntaxError):
            ana.analyze("def broken(::")

    def test_decorated_functions(self, mod):
        source = """
from functools import wraps

def decorator(f):
    @wraps(f)
    def wrapper(*a, **kw):
        return f(*a, **kw)
    return wrapper

@decorator
def decorated_fn() -> str:
    return "hello"
"""
        ana = mod._CodePathAnalyzerAST()
        ana.analyze(source)
        names = {f.name for f in ana.functions}
        assert "decorator" in names
        assert "decorated_fn" in names


class TestCLI:
    def test_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--target-module" in result.stdout
        assert "--output" in result.stdout

    def test_file_not_found(self):
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH),
                "--target-module", "/nonexistent/module.py",
                "--output", "/tmp/out.json",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "failed"

    def test_end_to_end(self, tmp_path: Path):
        sample_file = tmp_path / "test_mod.py"
        sample_file.write_text(SAMPLE_MODULE)
        out_file = tmp_path / "analysis.json"

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--target-module", str(sample_file), "--output", str(out_file)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        report = json.loads(result.stdout)
        assert report["function_count"] >= 3
        assert report["class_count"] >= 2
        assert report["testable_path_count"] >= 3

        assert out_file.exists()
        with open(out_file) as f:
            data = json.load(f)
        assert data["status"] == "completed"
        assert data["module"] == "test_mod.py"

    def test_end_to_end_ast_only(self, tmp_path: Path):
        sample_file = tmp_path / "test_mod.py"
        sample_file.write_text(SAMPLE_MODULE)
        out_file = tmp_path / "analysis.json"

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--target-module", str(sample_file),
                "--output", str(out_file),
                "--ast-only",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        with open(out_file) as f:
            data = json.load(f)
        assert data["parser"] == "ast"

    def test_syntax_error_file(self, tmp_path: Path):
        broken = tmp_path / "broken.py"
        broken.write_text("def broken(::\n")
        out_file = tmp_path / "analysis.json"

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--target-module", str(broken), "--output", str(out_file)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "failed"
        assert "Syntax error" in data["error"]

    def test_correct_output_keys(self, tmp_path: Path):
        sample_file = tmp_path / "test_mod.py"
        sample_file.write_text(SAMPLE_MODULE)
        out_file = tmp_path / "analysis.json"

        subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--target-module", str(sample_file), "--output", str(out_file)],
            capture_output=True, text=True,
        )
        with open(out_file) as f:
            data = json.load(f)

        required_keys = {"module", "path", "source", "imports", "call_graph", "testable_paths",
                         "testable_path_count", "function_count", "class_count", "parser", "status"}
        assert required_keys <= set(data.keys())
