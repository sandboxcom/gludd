from __future__ import annotations

from agentic_harness.planning.repo_map import CodeSymbol, RepoMap, RepoMapBuilder


class TestCodeSymbol:
    def test_create_symbol_with_all_fields(self):
        sym = CodeSymbol(
            name="foo",
            kind="function",
            file_path="src/main.py",
            line_start=10,
            line_end=20,
            parent=None,
        )
        assert sym.name == "foo"
        assert sym.kind == "function"
        assert sym.file_path == "src/main.py"
        assert sym.line_start == 10
        assert sym.line_end == 20
        assert sym.parent is None

    def test_create_method_with_parent(self):
        sym = CodeSymbol(
            name="run",
            kind="method",
            file_path="src/app.py",
            line_start=15,
            line_end=30,
            parent="MyApp",
        )
        assert sym.parent == "MyApp"
        assert sym.kind == "method"

    def test_symbol_kinds(self):
        for kind in ("function", "class", "method", "variable", "import"):
            sym = CodeSymbol(
                name="x",
                kind=kind,
                file_path="a.py",
                line_start=1,
                line_end=1,
            )
            assert sym.kind == kind


class TestRepoMap:
    def _make_symbols(self):
        return [
            CodeSymbol(
                name="MyClass", kind="class", file_path="src/app.py",
                line_start=10, line_end=50, parent=None,
            ),
            CodeSymbol(
                name="run", kind="method", file_path="src/app.py",
                line_start=15, line_end=30, parent="MyClass",
            ),
            CodeSymbol(
                name="shutdown", kind="method", file_path="src/app.py",
                line_start=32, line_end=45, parent="MyClass",
            ),
            CodeSymbol(
                name="helper", kind="function", file_path="src/util.py",
                line_start=1, line_end=10, parent=None,
            ),
            CodeSymbol(
                name="config", kind="variable", file_path="src/util.py",
                line_start=12, line_end=12, parent=None,
            ),
        ]

    def test_add_symbol_and_count(self):
        rm = RepoMap()
        assert len(rm.symbols) == 0
        rm.add_symbol(self._make_symbols()[0])
        assert len(rm.symbols) == 1

    def test_get_symbols_for_file(self):
        rm = RepoMap()
        for s in self._make_symbols():
            rm.add_symbol(s)
        app_syms = rm.get_symbols_for_file("src/app.py")
        assert len(app_syms) == 3
        assert all(s.file_path == "src/app.py" for s in app_syms)

    def test_get_symbols_for_file_empty(self):
        rm = RepoMap()
        assert rm.get_symbols_for_file("nonexistent.py") == []

    def test_get_top_symbols(self):
        rm = RepoMap()
        for s in self._make_symbols():
            rm.add_symbol(s)
        top = rm.get_top_symbols(n=2)
        assert len(top) == 2

    def test_file_count_and_total_lines(self):
        rm = RepoMap(file_count=5, total_lines=200)
        assert rm.file_count == 5
        assert rm.total_lines == 200


class TestRepoMapCompactString:
    def test_compact_string_format(self):
        rm = RepoMap()
        rm.add_symbol(CodeSymbol(
            name="MyApp", kind="class", file_path="src/main.py",
            line_start=10, line_end=50,
        ))
        rm.add_symbol(CodeSymbol(
            name="run", kind="method", file_path="src/main.py",
            line_start=15, line_end=30, parent="MyApp",
        ))
        rm.add_symbol(CodeSymbol(
            name="shutdown", kind="method", file_path="src/main.py",
            line_start=32, line_end=45, parent="MyApp",
        ))
        rm.add_symbol(CodeSymbol(
            name="create_app", kind="function", file_path="src/main.py",
            line_start=52, line_end=60,
        ))
        output = rm.to_compact_string()
        assert "src/main.py:" in output
        assert "class MyApp" in output
        assert "L10-50" in output
        assert "method run()" in output
        assert "L15-30" in output
        assert "method shutdown()" in output
        assert "L32-45" in output
        assert "function create_app()" in output
        assert "L52-60" in output

    def test_compact_string_groups_by_file(self):
        rm = RepoMap()
        rm.add_symbol(CodeSymbol(name="a", kind="function", file_path="a.py", line_start=1, line_end=5))
        rm.add_symbol(CodeSymbol(name="b", kind="function", file_path="b.py", line_start=1, line_end=5))
        output = rm.to_compact_string()
        assert "a.py:" in output
        assert "b.py:" in output

    def test_compact_string_empty(self):
        rm = RepoMap()
        assert rm.to_compact_string() == ""


class TestRepoMapSerialization:
    def test_to_dict_roundtrip(self):
        rm = RepoMap(file_count=2, total_lines=100)
        rm.add_symbol(CodeSymbol(name="foo", kind="function", file_path="a.py", line_start=1, line_end=10))
        rm.add_symbol(CodeSymbol(name="Bar", kind="class", file_path="b.py", line_start=5, line_end=20))
        data = rm.to_dict()
        assert data["file_count"] == 2
        assert data["total_lines"] == 100
        assert len(data["symbols"]) == 2
        restored = RepoMap.from_dict(data)
        assert restored.file_count == rm.file_count
        assert restored.total_lines == rm.total_lines
        assert len(restored.symbols) == len(rm.symbols)
        assert restored.symbols[0].name == "foo"

    def test_to_dict_empty(self):
        rm = RepoMap()
        data = rm.to_dict()
        assert data["symbols"] == []
        assert data["file_count"] == 0
        restored = RepoMap.from_dict(data)
        assert len(restored.symbols) == 0


class TestRepoMapBuilderParseFile:
    def test_parse_simple_function(self):
        builder = RepoMapBuilder(language="python")
        code = "def hello():\n    return 'world'\n"
        symbols = builder.parse_file("test.py", code)
        assert len(symbols) >= 1
        fn = next(s for s in symbols if s.name == "hello")
        assert fn.kind == "function"
        assert fn.file_path == "test.py"
        assert fn.line_start == 0
        assert fn.parent is None

    def test_parse_class_with_methods(self):
        builder = RepoMapBuilder(language="python")
        code = "class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n"
        symbols = builder.parse_file("cls.py", code)
        classes = [s for s in symbols if s.kind == "class"]
        methods = [s for s in symbols if s.kind == "method"]
        assert len(classes) == 1
        assert classes[0].name == "Foo"
        assert len(methods) == 2
        method_names = {m.name for m in methods}
        assert "bar" in method_names
        assert "baz" in method_names
        for m in methods:
            assert m.parent == "Foo"

    def test_parse_decorated_function(self):
        builder = RepoMapBuilder(language="python")
        code = "@decorator\ndef decorated():\n    pass\n"
        symbols = builder.parse_file("dec.py", code)
        fn = [s for s in symbols if s.name == "decorated"]
        assert len(fn) == 1
        assert fn[0].kind == "function"

    def test_parse_imports(self):
        builder = RepoMapBuilder(language="python")
        code = "import os\nfrom sys import path\n"
        symbols = builder.parse_file("imp.py", code)
        imports = [s for s in symbols if s.kind == "import"]
        assert len(imports) >= 1
        import_names = {s.name for s in imports}
        assert "os" in import_names

    def test_parse_empty_content(self):
        builder = RepoMapBuilder(language="python")
        symbols = builder.parse_file("empty.py", "")
        assert symbols == []


class TestRepoMapBuilderRankSymbols:
    def test_rank_returns_top_n(self):
        builder = RepoMapBuilder(language="python")
        symbols = [
            CodeSymbol(name="a", kind="function", file_path="f1.py", line_start=1, line_end=2),
            CodeSymbol(name="b", kind="function", file_path="f2.py", line_start=1, line_end=2),
            CodeSymbol(name="c", kind="class", file_path="f3.py", line_start=1, line_end=2),
        ]
        ranked = builder._rank_symbols(symbols, n=2)
        assert len(ranked) == 2

    def test_rank_prefers_classes_then_functions(self):
        builder = RepoMapBuilder(language="python")
        symbols = [
            CodeSymbol(name="func_a", kind="function", file_path="f.py", line_start=1, line_end=2),
            CodeSymbol(name="MyClass", kind="class", file_path="f.py", line_start=1, line_end=50),
            CodeSymbol(name="func_b", kind="function", file_path="f.py", line_start=1, line_end=2),
        ]
        ranked = builder._rank_symbols(symbols, n=2)
        kinds = [s.kind for s in ranked]
        assert "class" in kinds
