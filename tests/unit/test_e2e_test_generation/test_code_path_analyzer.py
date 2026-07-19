"""Unit tests for CodePathAnalyzer."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.agents.test_generation.code_path_analyzer import (
    ClassSymbol,
    CodePathAnalyzer,
    ModuleSymbols,
    Symbol,
)


@pytest.fixture
def sample_py_file() -> str:
    content = """\
def top_level_func():
    return 42

def _private_func():
    return "secret"

class PublicClass:
    def method_a(self):
        pass

    def _private_method(self):
        pass

class _PrivateClass:
    def inner_method(self):
        pass

async def async_func():
    await something()
"""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(content)
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def analyzer() -> CodePathAnalyzer:
    return CodePathAnalyzer()


class TestAnalyze:
    def test_extracts_functions_and_classes(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        assert isinstance(result, ModuleSymbols)
        assert result.name == sample_py_file

    def test_functions_extracted_correctly(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        func_names = {f.name for f in result.functions}
        assert "top_level_func" in func_names
        assert "_private_func" in func_names
        assert "async_func" in func_names

    def test_classes_extracted_correctly(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        class_names = {c.name for c in result.classes}
        assert "PublicClass" in class_names
        assert "_PrivateClass" in class_names

    def test_class_methods_extracted(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        public = next(c for c in result.classes if c.name == "PublicClass")
        method_names = {m.name for m in public.methods}
        assert "method_a" in method_names
        assert "_private_method" in method_names

    def test_private_inside_private_class(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        priv_class = next(c for c in result.classes if c.name == "_PrivateClass")
        assert len(priv_class.methods) == 1
        assert priv_class.methods[0].name == "inner_method"


class TestSymbolVisibility:
    def test_public_top_level_function(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        pub = next(f for f in result.functions if f.name == "top_level_func")
        assert pub.is_public is True

    def test_private_top_level_function(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        priv = next(f for f in result.functions if f.name == "_private_func")
        assert priv.is_public is False

    def test_public_class_is_public(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        pub = next(c for c in result.classes if c.name == "PublicClass")
        assert pub.is_public is True

    def test_private_class_is_not_public(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        pvt = next(c for c in result.classes if c.name == "_PrivateClass")
        assert pvt.is_public is False

    def test_public_method_is_public(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        pub = next(c for c in result.classes if c.name == "PublicClass")
        m = next(x for x in pub.methods if x.name == "method_a")
        assert m.is_public is True

    def test_private_method_is_not_public(self, analyzer: CodePathAnalyzer, sample_py_file: str) -> None:
        result = analyzer.analyze(sample_py_file)
        pub = next(c for c in result.classes if c.name == "PublicClass")
        m = next(x for x in pub.methods if x.name == "_private_method")
        assert m.is_public is False


class TestSymbolShape:
    def test_symbol_has_correct_fields(self) -> None:
        s = Symbol(name="foo", line_start=1, line_end=5, is_public=True)
        assert s.name == "foo"
        assert s.line_start == 1
        assert s.line_end == 5
        assert s.is_public is True

    def test_class_symbol_includes_methods(self) -> None:
        m1 = Symbol(name="bar", line_start=3, line_end=4, is_public=True)
        cs = ClassSymbol(name="Foo", line_start=1, line_end=10, is_public=True, methods=[m1])
        assert cs.methods == [m1]
        assert cs.name == "Foo"

    def test_empty_module(self, analyzer: CodePathAnalyzer, tmp_path: Path) -> None:
        empty = tmp_path / "empty.py"
        empty.write_text("x = 1\n")
        result = analyzer.analyze(str(empty))
        assert result.functions == []
        assert result.classes == []
