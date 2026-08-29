"""Branch-floor contracts for beta4 test-generation agents."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import pytest

from general_ludd.agents.test_generation import code_path_analyzer
from general_ludd.agents.test_generation.code_path_analyzer import (
    CodePathAnalyzer,
    _extract_methods,
)
from general_ludd.agents.test_generation.contracts import GenerationHarness, GenerationSpec
from general_ludd.agents.test_generation.test_generator import GeneratorImpl


class _Node:
    """Minimal tree-sitter node double with explicit named fields."""

    def __init__(
        self,
        node_type: str,
        *,
        children: list[object] | None = None,
        fields: dict[str, _Node | None] | None = None,
        start_byte: int = 0,
        end_byte: int = 0,
        start_line: int = 0,
        end_line: int = 0,
    ) -> None:
        self.type = node_type
        self.children = children or []
        self._fields = fields or {}
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = (start_line, 0)
        self.end_point = (end_line, 0)

    def child_by_field_name(self, name: str) -> _Node | None:
        """Return a named child just like tree-sitter's node API."""
        return self._fields.get(name)


class _ParsedTree:
    """Tree result returned by the parser double."""

    def __init__(self, root_node: _Node) -> None:
        self.root_node = root_node


class _Parser:
    """Parser double that returns one predetermined tree."""

    def __init__(self, root_node: _Node) -> None:
        self._root_node = root_node

    def parse(self, _source: bytes) -> _ParsedTree:
        """Return the owned parsed-tree double."""
        return _ParsedTree(self._root_node)


def _definition(
    node_type: str,
    name: str,
    *,
    children: list[object] | None = None,
    start_byte: int = 0,
) -> _Node:
    """Build a named function or class definition node."""
    return _Node(
        node_type,
        children=children,
        fields={
            "name": _Node(
                "identifier",
                start_byte=start_byte,
                end_byte=start_byte + len(name),
            )
        },
        end_line=1,
    )


def test_parser_import_failure_is_cached_and_analysis_fails_soft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A controller without tree-sitter returns an empty, observable result."""
    real_import = builtins.__import__

    def missing_tree_sitter(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "tree_sitter_python":
            raise ImportError("missing optional parser")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_tree_sitter)
    monkeypatch.setattr(code_path_analyzer, "_PARSER", False)
    source = tmp_path / "sample.py"
    source.write_text("def visible():\n    pass\n")
    caplog.set_level("WARNING")

    result = CodePathAnalyzer().analyze(str(source))

    assert result.functions == []
    assert result.classes == []
    assert "tree-sitter not available" in caplog.text
    assert code_path_analyzer._get_parser() is None


def test_analyze_walks_decorated_and_nested_definitions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Decorated definitions and nested blocks retain public-symbol metadata."""
    decorated_function = _definition("function_definition", "public", start_byte=0)
    nested_function = _definition("function_definition", "nested", start_byte=6)
    method = _definition("function_definition", "method", start_byte=18)
    body = _Node("block", children=[_Node("decorated_definition", children=[method])])
    class_node = _definition("class_definition", "Public", children=[body], start_byte=12)
    class_node._fields["body"] = body
    root = _Node(
        "module",
        children=[
            _Node("decorated_definition", children=[_Node("decorator"), decorated_function]),
            _Node("block", children=[nested_function]),
            class_node,
        ],
    )
    monkeypatch.setattr(code_path_analyzer, "_PARSER", _Parser(root))
    source = tmp_path / "synthetic.py"
    source.write_text("publicnestedPublicmethod")

    result = CodePathAnalyzer().analyze(str(source))

    assert [symbol.name for symbol in result.functions] == ["public", "nested"]
    assert result.classes[0].name == "Public"
    assert [symbol.name for symbol in result.classes[0].methods] == ["method"]


def test_walk_skips_malformed_nodes_and_recurses_into_method_blocks() -> None:
    """Incomplete parser nodes are ignored without losing valid nested methods."""
    no_name_function = _Node("function_definition")
    no_name_class = _Node("class_definition")
    no_body_class = _Node(
        "class_definition",
        fields={"name": _Node("identifier", start_byte=0, end_byte=6)},
    )
    nested_method = _definition("function_definition", "method")
    method_block = _Node("body", children=[nested_method])
    undecorated_wrapper = _Node("decorated_definition", children=[_Node("decorator")])
    methods: list[code_path_analyzer.Symbol] = []

    functions: list[code_path_analyzer.Symbol] = []
    classes: list[code_path_analyzer.ClassSymbol] = []
    CodePathAnalyzer._walk(
        _Node("module", children=[object(), undecorated_wrapper, no_name_function, no_name_class, no_body_class]),
        "Publicmethod",
        functions,
        classes,
    )
    _extract_methods(
        _Node("block", children=[object(), undecorated_wrapper, no_name_function, method_block]),
        "method",
        methods,
    )

    assert functions == []
    assert [item.name for item in classes] == ["Public"]
    assert classes[0].methods == []
    assert [item.name for item in methods] == ["method"]


def test_generation_preserves_existing_conftest(tmp_path: Path) -> None:
    """Generation owns its test file but never overwrites caller configuration."""
    conftest = tmp_path / "conftest.py"
    conftest.write_text("CALLER_OWNED = True\n")
    generator = GeneratorImpl(
        spec=GenerationSpec(target_module="general_ludd.example", output_dir=str(tmp_path)),
        harness=GenerationHarness(),
    )

    generated = generator.generate()

    assert len(generated) == 1
    assert conftest.read_text() == "CALLER_OWNED = True\n"
