"""CodePathAnalyzer — extracts public symbols (functions, classes, methods) from Python source via tree-sitter.

Produces a ``ModuleSymbols`` namedtuple per module.  Public means not ``_``-prefixed.
"""

from __future__ import annotations

import logging
from collections import namedtuple
from typing import Any, cast

logger = logging.getLogger(__name__)

_PARSER: object | None = False


def _get_parser() -> object | None:
    global _PARSER
    if _PARSER is not False:
        return _PARSER
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        _PARSER = Parser(Language(tspython.language()))
    except ImportError:
        _PARSER = None
        logger.warning("tree-sitter not available, code_path_analyzer disabled")
    return _PARSER


Symbol = namedtuple("Symbol", ["name", "line_start", "line_end", "is_public"])

ClassSymbol = namedtuple("ClassSymbol", ["name", "line_start", "line_end", "is_public", "methods"])

ModuleSymbols = namedtuple("ModuleSymbols", ["name", "functions", "classes"])


class CodePathAnalyzer:
    """Extract module-level functions, classes, and class-owned methods."""

    def analyze(self, file_path: str) -> ModuleSymbols:
        """Parse one Python file and return its public-symbol structure."""
        parser = _get_parser()
        if parser is None:
            return ModuleSymbols(name=file_path, functions=[], classes=[])

        with open(file_path, "rb") as fh:
            source_bytes = fh.read()

        source_str = source_bytes.decode("utf-8")
        tree = cast("Any", parser).parse(source_bytes)

        functions: list[Symbol] = []
        classes: list[ClassSymbol] = []
        self._walk(tree.root_node, source_str, functions, classes)
        return ModuleSymbols(name=file_path, functions=functions, classes=classes)

    @staticmethod
    def _walk(
        node: object,
        source: str,
        functions: list[Symbol],
        classes: list[ClassSymbol],
    ) -> None:
        n = cast("Any", node)
        for child in n.children:
            child_type = child.type if hasattr(child, "type") else ""

            actual = child
            ctype = child_type
            if child_type == "decorated_definition":
                for sub in child.children:
                    st = sub.type if hasattr(sub, "type") else ""
                    if st == "function_definition":
                        actual = sub
                        ctype = "function_definition"
                        break

            if ctype == "function_definition":
                name_node = actual.child_by_field_name("name")
                if name_node is not None:
                    name = source[name_node.start_byte : name_node.end_byte]
                    sym = Symbol(
                        name=name,
                        line_start=actual.start_point[0] + 1,
                        line_end=actual.end_point[0] + 1,
                        is_public=not name.startswith("_"),
                    )
                    functions.append(sym)

            elif ctype == "class_definition":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    name = source[name_node.start_byte : name_node.end_byte]
                    methods: list[Symbol] = []
                    body = child.child_by_field_name("body")
                    if body is not None:
                        _extract_methods(body, source, methods)
                    csym = ClassSymbol(
                        name=name,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        is_public=not name.startswith("_"),
                        methods=methods,
                    )
                    classes.append(csym)

            if ctype in ("block", "body"):
                CodePathAnalyzer._walk(child, source, functions, classes)


def _extract_methods(body_node: object, source: str, methods: list[Symbol]) -> None:
    n = cast("Any", body_node)
    for child in n.children:
        child_type = child.type if hasattr(child, "type") else ""

        actual = child
        ctype = child_type
        if child_type == "decorated_definition":
            for sub in child.children:
                st = sub.type if hasattr(sub, "type") else ""
                if st == "function_definition":
                    actual = sub
                    ctype = "function_definition"
                    break

        if ctype == "function_definition":
            name_node = actual.child_by_field_name("name")
            if name_node is not None:
                name = source[name_node.start_byte : name_node.end_byte]
                sym = Symbol(
                    name=name,
                    line_start=actual.start_point[0] + 1,
                    line_end=actual.end_point[0] + 1,
                    is_public=not name.startswith("_"),
                )
                methods.append(sym)

        if ctype in ("block", "body"):
            _extract_methods(child, source, methods)
