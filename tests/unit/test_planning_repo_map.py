"""Unit + functional tests for src/general_ludd/planning/repo_map.py.

Covers:
  - CodeSymbol model: construction, validators, edge cases
  - RepoMap model: add_symbol, get_symbols_for_file, get_top_symbols,
    to_compact_string, to_dict, from_dict
  - RepoMapBuilder: parse_file (full parse), build_from_directory,
    _rank_symbols, _walk_node, _walk_block, _extract_import
  - Cross-file symbol resolution via get_symbols_for_file
  - Cycle detection is structural (tree-sitter produces a tree, not a graph),
    so tested via deeply-nested decorated definitions and recursive-looking code
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from general_ludd.planning.repo_map import KIND_PRIORITY, CodeSymbol, RepoMap, RepoMapBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sym(
    name: str = "foo",
    kind: str = "function",
    file_path: str = "a.py",
    line_start: int = 0,
    line_end: int = 5,
    parent: str | None = None,
) -> CodeSymbol:
    return CodeSymbol(
        name=name,
        kind=kind,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        parent=parent,
    )


# ---------------------------------------------------------------------------
# CodeSymbol
# ---------------------------------------------------------------------------


class TestCodeSymbolConstruction:
    def test_basic_creation(self):
        s = _sym("MyClass", "class", "foo.py", 0, 10)
        assert s.name == "MyClass"
        assert s.kind == "class"
        assert s.file_path == "foo.py"
        assert s.line_start == 0
        assert s.line_end == 10
        assert s.parent is None

    def test_with_parent(self):
        s = _sym("my_method", "method", "foo.py", 5, 8, parent="MyClass")
        assert s.parent == "MyClass"

    def test_name_stripped(self):
        s = CodeSymbol(name="  bar  ", kind="function", file_path="x.py",
                       line_start=0, line_end=1)
        assert s.name == "bar"

    def test_file_path_stripped(self):
        s = CodeSymbol(name="bar", kind="function", file_path="  x.py  ",
                       line_start=0, line_end=1)
        assert s.file_path == "x.py"

    def test_same_line_start_end(self):
        s = _sym(line_start=3, line_end=3)
        assert s.line_start == s.line_end == 3


class TestCodeSymbolValidation:
    def test_empty_name_raises(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="", kind="function", file_path="a.py",
                       line_start=0, line_end=1)

    def test_whitespace_only_name_raises(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="   ", kind="function", file_path="a.py",
                       line_start=0, line_end=1)

    def test_empty_file_path_raises(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="foo", kind="function", file_path="",
                       line_start=0, line_end=1)

    def test_negative_line_start_raises(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="foo", kind="function", file_path="a.py",
                       line_start=-1, line_end=1)

    def test_line_end_less_than_line_start_raises(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="foo", kind="function", file_path="a.py",
                       line_start=5, line_end=3)

    def test_zero_line_start_allowed(self):
        s = _sym(line_start=0, line_end=0)
        assert s.line_start == 0


# ---------------------------------------------------------------------------
# RepoMap
# ---------------------------------------------------------------------------


class TestRepoMapAddAndQuery:
    def test_empty_map(self):
        rm = RepoMap()
        assert rm.symbols == []
        assert rm.file_count == 0
        assert rm.total_lines == 0

    def test_add_symbol(self):
        rm = RepoMap()
        s = _sym("func_a", "function", "a.py")
        rm.add_symbol(s)
        assert len(rm.symbols) == 1
        assert rm.symbols[0] is s

    def test_get_symbols_for_file(self):
        rm = RepoMap()
        rm.add_symbol(_sym("func_a", "function", "a.py", 0, 2))
        rm.add_symbol(_sym("func_b", "function", "b.py", 0, 3))
        rm.add_symbol(_sym("func_c", "function", "a.py", 5, 8))
        result = rm.get_symbols_for_file("a.py")
        assert len(result) == 2
        assert all(s.file_path == "a.py" for s in result)

    def test_get_symbols_for_file_unknown(self):
        rm = RepoMap()
        rm.add_symbol(_sym("func_a", "function", "a.py"))
        assert rm.get_symbols_for_file("z.py") == []

    def test_get_top_symbols_default_n(self):
        rm = RepoMap()
        for i in range(10):
            rm.add_symbol(_sym(f"func_{i}", "function", "a.py", i, i + 1))
        top = rm.get_top_symbols()
        assert len(top) == 10

    def test_get_top_symbols_limited_n(self):
        rm = RepoMap()
        for i in range(20):
            rm.add_symbol(_sym(f"func_{i}", "function", "a.py", i, i + 1))
        top = rm.get_top_symbols(n=5)
        assert len(top) == 5

    def test_get_top_symbols_empty(self):
        rm = RepoMap()
        assert rm.get_top_symbols() == []

    def test_get_top_symbols_prefers_classes_over_functions(self):
        rm = RepoMap()
        rm.add_symbol(_sym("my_func", "function", "a.py", 0, 1))
        rm.add_symbol(_sym("MyClass", "class", "a.py", 2, 20))
        top = rm.get_top_symbols(n=2)
        assert top[0].kind == "class"


class TestRepoMapToCompactString:
    def test_empty_returns_empty_string(self):
        rm = RepoMap()
        assert rm.to_compact_string() == ""

    def test_single_function(self):
        rm = RepoMap()
        rm.add_symbol(_sym("do_thing", "function", "a.py", 0, 5))
        s = rm.to_compact_string()
        assert "a.py:" in s
        assert "function do_thing()" in s
        assert "L0-5" in s

    def test_class_symbol(self):
        rm = RepoMap()
        rm.add_symbol(_sym("MyClass", "class", "a.py", 0, 20))
        s = rm.to_compact_string()
        assert "class MyClass" in s

    def test_method_under_class(self):
        rm = RepoMap()
        rm.add_symbol(_sym("MyClass", "class", "a.py", 0, 20))
        rm.add_symbol(_sym("my_method", "method", "a.py", 1, 5, parent="MyClass"))
        s = rm.to_compact_string()
        assert "method my_method()" in s

    def test_variable_symbol(self):
        rm = RepoMap()
        rm.add_symbol(_sym("MY_CONST", "variable", "a.py", 0, 0))
        s = rm.to_compact_string()
        assert "variable MY_CONST" in s

    def test_import_symbol(self):
        rm = RepoMap()
        rm.add_symbol(_sym("os", "import", "a.py", 0, 0))
        s = rm.to_compact_string()
        assert "import os" in s

    def test_unknown_kind_falls_through(self):
        rm = RepoMap()
        rm.add_symbol(_sym("weird", "something_else", "a.py", 0, 1))
        s = rm.to_compact_string()
        assert "something_else weird" in s

    def test_multiple_files_sorted(self):
        rm = RepoMap()
        rm.add_symbol(_sym("z_func", "function", "z.py", 0, 1))
        rm.add_symbol(_sym("a_func", "function", "a.py", 0, 1))
        lines = rm.to_compact_string().split("\n")
        file_headers = [ln for ln in lines if ln.endswith(":") and not ln.startswith(" ")]
        assert file_headers[0].startswith("a.py")
        assert file_headers[1].startswith("z.py")

    def test_symbols_sorted_by_line_within_file(self):
        rm = RepoMap()
        rm.add_symbol(_sym("later", "function", "a.py", 10, 15))
        rm.add_symbol(_sym("earlier", "function", "a.py", 1, 5))
        s = rm.to_compact_string()
        idx_earlier = s.index("earlier")
        idx_later = s.index("later")
        assert idx_earlier < idx_later

    def test_methods_nested_under_class(self):
        rm = RepoMap()
        rm.add_symbol(_sym("MyClass", "class", "a.py", 0, 20))
        rm.add_symbol(_sym("helper", "method", "a.py", 2, 5, parent="MyClass"))
        rm.add_symbol(_sym("orphan_method", "method", "a.py", 7, 9, parent="OtherClass"))
        s = rm.to_compact_string()
        # helper is under MyClass, orphan_method's parent class is not in symbols
        assert "method helper()" in s
        # orphan_method should NOT appear (its parent "OtherClass" is not a top-level sym)
        assert "orphan_method" not in s


class TestRepoMapSerialisation:
    def test_to_dict_round_trip(self):
        rm = RepoMap(file_count=2, total_lines=100)
        rm.add_symbol(_sym("MyClass", "class", "a.py", 0, 10))
        rm.add_symbol(_sym("my_method", "method", "a.py", 1, 5, parent="MyClass"))
        d = rm.to_dict()
        assert d["file_count"] == 2
        assert d["total_lines"] == 100
        assert len(d["symbols"]) == 2

    def test_from_dict_reconstructs(self):
        original = RepoMap(file_count=3, total_lines=50)
        original.add_symbol(_sym("func_x", "function", "b.py", 0, 4))
        d = original.to_dict()
        restored = RepoMap.from_dict(d)
        assert restored.file_count == 3
        assert restored.total_lines == 50
        assert len(restored.symbols) == 1
        assert restored.symbols[0].name == "func_x"

    def test_from_dict_defaults_missing_fields(self):
        d: dict = {"symbols": []}
        rm = RepoMap.from_dict(d)
        assert rm.file_count == 0
        assert rm.total_lines == 0

    def test_from_dict_empty_symbols(self):
        rm = RepoMap.from_dict({"symbols": [], "file_count": 0, "total_lines": 0})
        assert rm.symbols == []


# ---------------------------------------------------------------------------
# RepoMapBuilder._rank_symbols
# ---------------------------------------------------------------------------


class TestRankSymbols:
    def setup_method(self):
        self.builder = RepoMapBuilder()

    def test_class_ranked_before_function(self):
        syms = [
            _sym("func_a", "function", "a.py", 0, 1),
            _sym("MyClass", "class", "a.py", 2, 20),
        ]
        ranked = self.builder._rank_symbols(syms, 2)
        assert ranked[0].kind == "class"

    def test_function_ranked_before_import(self):
        syms = [
            _sym("os", "import", "a.py", 0, 0),
            _sym("func_a", "function", "a.py", 2, 5),
        ]
        ranked = self.builder._rank_symbols(syms, 2)
        assert ranked[0].kind == "function"

    def test_repeated_name_ranked_higher(self):
        # func_b appears in two files — higher frequency wins
        syms = [
            _sym("func_a", "function", "a.py", 0, 1),
            _sym("func_b", "function", "a.py", 2, 3),
            _sym("func_b", "function", "b.py", 0, 1),
        ]
        ranked = self.builder._rank_symbols(syms, 3)
        names = [s.name for s in ranked]
        assert names[0] == "func_b"

    def test_longer_symbol_ranked_higher_among_same_kind_same_freq(self):
        syms = [
            _sym("short_fn", "function", "a.py", 0, 1),
            _sym("long_fn", "function", "a.py", 0, 20),
        ]
        ranked = self.builder._rank_symbols(syms, 2)
        assert ranked[0].name == "long_fn"

    def test_limit_n_respected(self):
        syms = [_sym(f"f{i}", "function", "a.py", i, i + 1) for i in range(20)]
        ranked = self.builder._rank_symbols(syms, 5)
        assert len(ranked) == 5

    def test_empty_input(self):
        assert self.builder._rank_symbols([], 10) == []

    def test_unknown_kind_gets_lowest_priority(self):
        syms = [
            _sym("weird", "unknown_kind", "a.py", 0, 1),
            _sym("func", "function", "a.py", 2, 3),
        ]
        ranked = self.builder._rank_symbols(syms, 2)
        assert ranked[0].kind == "function"


# ---------------------------------------------------------------------------
# KIND_PRIORITY constant
# ---------------------------------------------------------------------------


class TestKindPriority:
    def test_class_has_lowest_number(self):
        assert KIND_PRIORITY["class"] == 0

    def test_function_and_method_same(self):
        assert KIND_PRIORITY["function"] == KIND_PRIORITY["method"]

    def test_import_last(self):
        assert KIND_PRIORITY["import"] > KIND_PRIORITY["function"]


# ---------------------------------------------------------------------------
# RepoMapBuilder.parse_file — full parse
# ---------------------------------------------------------------------------


SIMPLE_PYTHON = """\
import os
from pathlib import Path

MY_VAR = 42

class Greeter:
    def greet(self, name: str) -> str:
        return f"Hello {name}"

    def farewell(self) -> str:
        return "Bye"

def standalone():
    pass
"""


class TestParseFileFullParse:
    def setup_method(self):
        self.builder = RepoMapBuilder()

    def test_parse_imports(self):
        syms = self.builder.parse_file("test.py", SIMPLE_PYTHON)
        import_names = sorted(s.name for s in syms if s.kind == "import")
        assert "os" in import_names

    def test_parse_class(self):
        syms = self.builder.parse_file("test.py", SIMPLE_PYTHON)
        classes = [s for s in syms if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "Greeter"

    def test_parse_methods_under_class(self):
        syms = self.builder.parse_file("test.py", SIMPLE_PYTHON)
        methods = sorted(s.name for s in syms if s.kind == "method")
        assert "greet" in methods
        assert "farewell" in methods

    def test_methods_have_parent_class(self):
        syms = self.builder.parse_file("test.py", SIMPLE_PYTHON)
        methods = [s for s in syms if s.kind == "method"]
        assert all(s.parent == "Greeter" for s in methods)

    def test_parse_standalone_function(self):
        syms = self.builder.parse_file("test.py", SIMPLE_PYTHON)
        fns = [s for s in syms if s.kind == "function"]
        assert any(s.name == "standalone" for s in fns)

    def test_standalone_function_no_parent(self):
        syms = self.builder.parse_file("test.py", SIMPLE_PYTHON)
        fn = next(s for s in syms if s.kind == "function" and s.name == "standalone")
        assert fn.parent is None

    def test_line_numbers_are_non_negative(self):
        syms = self.builder.parse_file("test.py", SIMPLE_PYTHON)
        for s in syms:
            assert s.line_start >= 0
            assert s.line_end >= s.line_start

    def test_empty_file_returns_empty(self):
        syms = self.builder.parse_file("empty.py", "")
        assert syms == []

    def test_file_path_recorded_on_symbols(self):
        syms = self.builder.parse_file("subdir/module.py", SIMPLE_PYTHON)
        assert all(s.file_path == "subdir/module.py" for s in syms)


DECORATED_CLASS = """\
import functools

def my_decorator(cls):
    return cls

@my_decorator
class Decorated:
    @staticmethod
    def static_method():
        pass

    @classmethod
    def class_method(cls):
        pass
"""


class TestParseFileDecorated:
    def setup_method(self):
        self.builder = RepoMapBuilder()

    def test_decorated_class_parsed(self):
        syms = self.builder.parse_file("dec.py", DECORATED_CLASS)
        classes = [s for s in syms if s.kind == "class"]
        assert any(s.name == "Decorated" for s in classes)

    def test_decorated_methods_parsed(self):
        syms = self.builder.parse_file("dec.py", DECORATED_CLASS)
        methods = [s for s in syms if s.kind == "method"]
        method_names = sorted(s.name for s in methods)
        assert "static_method" in method_names
        assert "class_method" in method_names

    def test_decorated_methods_have_parent(self):
        syms = self.builder.parse_file("dec.py", DECORATED_CLASS)
        methods = [s for s in syms if s.kind == "method"]
        assert all(s.parent == "Decorated" for s in methods)


IMPORT_VARIANTS = """\
import os
import os.path
from pathlib import Path
from sys import argv, exit as sys_exit
from os import *
"""


class TestParseFileImports:
    def setup_method(self):
        self.builder = RepoMapBuilder()

    def test_plain_import(self):
        syms = self.builder.parse_file("imp.py", IMPORT_VARIANTS)
        import_names = [s.name for s in syms if s.kind == "import"]
        assert "os" in import_names

    def test_dotted_import(self):
        syms = self.builder.parse_file("imp.py", IMPORT_VARIANTS)
        import_names = [s.name for s in syms if s.kind == "import"]
        assert "os.path" in import_names

    def test_from_import(self):
        syms = self.builder.parse_file("imp.py", IMPORT_VARIANTS)
        import_names = [s.name for s in syms if s.kind == "import"]
        # from pathlib import Path -> should capture 'pathlib' or 'Path'
        assert any(n in import_names for n in ("pathlib", "Path"))

    def test_wildcard_import(self):
        # "from os import *" — the parser captures the module name "os"
        # (wildcard_import falls through to the last_identifier logic which
        # picks up the dotted_name "os" before it hits the wildcard node)
        syms = self.builder.parse_file("imp.py", IMPORT_VARIANTS)
        import_names = [s.name for s in syms if s.kind == "import"]
        # The from-import produces at least one symbol; "os" is captured
        assert any(n in import_names for n in ("os", "*"))


ALIASED_IMPORT = """\
import numpy as np
"""


class TestParseFileAliasedImport:
    def setup_method(self):
        self.builder = RepoMapBuilder()

    def test_aliased_import_extracts_original_name(self):
        syms = self.builder.parse_file("alias.py", ALIASED_IMPORT)
        import_names = [s.name for s in syms if s.kind == "import"]
        assert "numpy" in import_names


MULTIPLE_CLASSES = """\
class Alpha:
    def alpha_method(self):
        pass

class Beta:
    def beta_method(self):
        pass
"""


class TestParseFileMultipleClasses:
    def setup_method(self):
        self.builder = RepoMapBuilder()

    def test_both_classes_parsed(self):
        syms = self.builder.parse_file("multi.py", MULTIPLE_CLASSES)
        class_names = sorted(s.name for s in syms if s.kind == "class")
        assert class_names == ["Alpha", "Beta"]

    def test_methods_assigned_to_correct_parents(self):
        syms = self.builder.parse_file("multi.py", MULTIPLE_CLASSES)
        alpha_methods = [s for s in syms if s.kind == "method" and s.parent == "Alpha"]
        beta_methods = [s for s in syms if s.kind == "method" and s.parent == "Beta"]
        assert len(alpha_methods) == 1
        assert alpha_methods[0].name == "alpha_method"
        assert len(beta_methods) == 1
        assert beta_methods[0].name == "beta_method"


# ---------------------------------------------------------------------------
# RepoMapBuilder.build_from_directory
# ---------------------------------------------------------------------------


class TestBuildFromDirectory:
    def _make_tree(self, files: dict[str, str]) -> str:
        """Write files dict to a temp dir and return the temp dir path."""
        tmpdir = tempfile.mkdtemp()
        for rel, content in files.items():
            path = Path(tmpdir) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return tmpdir

    def test_basic_build(self):
        tmpdir = self._make_tree({"a.py": SIMPLE_PYTHON})
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir)
        assert rm.file_count == 1
        assert rm.total_lines > 0
        assert len(rm.symbols) > 0

    def test_multiple_files_counted(self):
        tmpdir = self._make_tree({
            "a.py": "def fa(): pass\n",
            "b.py": "def fb(): pass\n",
        })
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir)
        assert rm.file_count == 2

    def test_symbols_from_all_files(self):
        tmpdir = self._make_tree({
            "a.py": "def fa(): pass\n",
            "b.py": "def fb(): pass\n",
        })
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir)
        names = sorted(s.name for s in rm.symbols if s.kind == "function")
        assert "fa" in names
        assert "fb" in names

    def test_total_lines_accumulated(self):
        tmpdir = self._make_tree({
            "a.py": "x = 1\ny = 2\n",
            "b.py": "z = 3\n",
        })
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir)
        assert rm.total_lines >= 3

    def test_empty_directory(self):
        tmpdir = self._make_tree({})
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir)
        assert rm.file_count == 0
        assert rm.symbols == []

    def test_max_files_limit(self):
        files = {f"module_{i}.py": f"def f{i}(): pass\n" for i in range(10)}
        tmpdir = self._make_tree(files)
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir, max_files=3)
        assert rm.file_count == 3

    def test_non_python_files_ignored(self):
        tmpdir = self._make_tree({
            "script.py": "def myfunc(): pass\n",
            "readme.md": "# hello\n",
            "data.json": '{"key": "value"}\n',
        })
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir)
        assert rm.file_count == 1

    def test_subdirectories_recursed(self):
        tmpdir = self._make_tree({
            "pkg/__init__.py": "",
            "pkg/sub.py": "def deep_func(): pass\n",
        })
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir)
        assert rm.file_count == 2
        fn_names = [s.name for s in rm.symbols if s.kind == "function"]
        assert "deep_func" in fn_names

    def test_file_paths_are_relative(self):
        tmpdir = self._make_tree({"sub/module.py": "def fn(): pass\n"})
        builder = RepoMapBuilder()
        rm = builder.build_from_directory(tmpdir)
        file_paths = {s.file_path for s in rm.symbols}
        # All paths should be relative (not start with /)
        assert all(not p.startswith("/") for p in file_paths)


# ---------------------------------------------------------------------------
# Cross-file symbol resolution
# ---------------------------------------------------------------------------


class TestCrossFileSymbolResolution:
    """Verify that get_symbols_for_file correctly isolates per-file symbols."""

    def test_two_files_same_function_name(self):
        """Same function name in two files resolves independently."""
        builder = RepoMapBuilder()
        syms_a = builder.parse_file("a.py", "def compute(): pass\n")
        syms_b = builder.parse_file("b.py", "def compute(): pass\n")
        rm = RepoMap()
        for s in syms_a + syms_b:
            rm.add_symbol(s)
        a_syms = rm.get_symbols_for_file("a.py")
        b_syms = rm.get_symbols_for_file("b.py")
        assert len(a_syms) == 1
        assert len(b_syms) == 1
        assert a_syms[0].file_path == "a.py"
        assert b_syms[0].file_path == "b.py"

    def test_get_top_symbols_across_files_ranks_by_frequency(self):
        """A symbol appearing in 3 files should outrank one in 1 file."""
        builder = RepoMapBuilder()
        code = "def shared_name(): pass\n"
        other = "def unique_fn(): pass\n"
        rm = RepoMap()
        for fname in ["a.py", "b.py", "c.py"]:
            for s in builder.parse_file(fname, code):
                rm.add_symbol(s)
        for s in builder.parse_file("d.py", other):
            rm.add_symbol(s)
        top = rm.get_top_symbols(n=4)
        assert top[0].name == "shared_name"

    def test_from_dict_preserves_file_path_isolation(self):
        builder = RepoMapBuilder()
        rm = RepoMap(file_count=2, total_lines=10)
        for s in builder.parse_file("a.py", "def fa(): pass\n"):
            rm.add_symbol(s)
        for s in builder.parse_file("b.py", "def fb(): pass\n"):
            rm.add_symbol(s)
        restored = RepoMap.from_dict(rm.to_dict())
        assert len(restored.get_symbols_for_file("a.py")) == 1
        assert len(restored.get_symbols_for_file("b.py")) == 1


# ---------------------------------------------------------------------------
# Cycle / deep nesting (structural, tree-sitter produces acyclic tree)
# ---------------------------------------------------------------------------


RECURSIVE_CODE = """\
def factorial(n: int) -> int:
    if n <= 1:
        return 1
    return n * factorial(n - 1)

class Node:
    def traverse(self, visited=None):
        if visited is None:
            visited = set()
        visited.add(self)
        for child in getattr(self, 'children', []):
            child.traverse(visited)
        return visited
"""


class TestCycleDetection:
    """tree-sitter produces an acyclic parse tree; we test that our walker
    handles 'recursive-looking' code without hanging or crashing."""

    def test_recursive_function_parsed_once(self):
        builder = RepoMapBuilder()
        syms = builder.parse_file("rec.py", RECURSIVE_CODE)
        fn_names = [s.name for s in syms if s.kind == "function"]
        # factorial should appear exactly once (not duplicated by recursion)
        assert fn_names.count("factorial") == 1

    def test_recursive_method_parsed_once(self):
        builder = RepoMapBuilder()
        syms = builder.parse_file("rec.py", RECURSIVE_CODE)
        method_names = [s.name for s in syms if s.kind == "method"]
        assert method_names.count("traverse") == 1

    def test_no_crash_on_deeply_nested_classes(self):
        """Deeply nested structure (classes within functions) should not crash."""
        code = """\
def outer():
    class Inner:
        def inner_method(self):
            pass
"""
        builder = RepoMapBuilder()
        # Should not raise; result may or may not include inner class
        syms = builder.parse_file("nested.py", code)
        assert isinstance(syms, list)

    def test_no_crash_on_multiple_decorators(self):
        code = """\
def dec1(f): return f
def dec2(f): return f

@dec1
@dec2
class Multi:
    @dec1
    @dec2
    def method(self):
        pass
"""
        builder = RepoMapBuilder()
        syms = builder.parse_file("multi_dec.py", code)
        assert isinstance(syms, list)
        classes = [s for s in syms if s.kind == "class"]
        assert any(s.name == "Multi" for s in classes)


# ---------------------------------------------------------------------------
# RepoMapBuilder constructor
# ---------------------------------------------------------------------------


class TestRepoMapBuilderConstructor:
    def test_default_language(self):
        builder = RepoMapBuilder()
        assert builder._language_name == "python"

    def test_parser_created(self):
        builder = RepoMapBuilder()
        assert builder._parser is not None

    def test_custom_language_name_stored(self):
        # We still use the Python parser internally, but the name attribute is stored
        builder = RepoMapBuilder(language="typescript")
        assert builder._language_name == "typescript"
