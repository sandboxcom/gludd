"""AST block extractor — extracts functions, classes, methods from source code via tree-sitter.

Inspired by Graphify/code-knowledge's tree-sitter AST extraction approach.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_LANGUAGE_PARSERS: dict[str, Any] = {}


def _get_parser(language: str) -> Any:
    if language in _LANGUAGE_PARSERS:
        return _LANGUAGE_PARSERS[language]

    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        if language == "python":
            PY_LANG = Language(tspython.language())
            parser = Parser(PY_LANG)
            _LANGUAGE_PARSERS[language] = parser
            return parser
    except ImportError:
        logger.warning("tree-sitter not available, using regex fallback for %s", language)
    return None


class ASTBlockExtractor:
    """Extracts code blocks (functions, classes, methods) from source code."""

    def extract_blocks(self, source_code: str, language: str = "python") -> list[dict[str, Any]]:
        parser = _get_parser(language)
        if parser is None:
            return self._extract_fallback(source_code)

        try:
            tree = parser.parse(bytes(source_code, "utf-8"))
            blocks: list[dict[str, Any]] = []
            self._walk_tree(tree.root_node, source_code, blocks, parent=None)
            return blocks
        except Exception as exc:
            logger.debug("tree-sitter parse failed: %s, falling back", exc)
            return self._extract_fallback(source_code)

    def _walk_tree(
        self,
        node: Any,
        source: str,
        blocks: list[dict[str, Any]],
        parent: str | None = None,
    ) -> None:
        for child in node.children:
            child_type = child.type if hasattr(child, "type") else ""

            actual_child = child
            if child_type == "decorated_definition":
                for sub in child.children:
                    st = sub.type if hasattr(sub, "type") else ""
                    if st == "function_definition":
                        actual_child = sub
                        child_type = "function_definition"
                        break

            if child_type == "function_definition":
                name_node = actual_child.child_by_field_name("name")
                name = source[name_node.start_byte : name_node.end_byte] if name_node else "unknown"

                docstring = self._extract_docstring(actual_child, source)
                block_type = "method" if parent else "function"
                block = {
                    "name": name,
                    "type": block_type,
                    "start_line": actual_child.start_point[0] + 1,
                    "end_line": actual_child.end_point[0] + 1,
                    "start_byte": actual_child.start_byte,
                    "end_byte": actual_child.end_byte,
                    "parent": parent,
                    "docstring": docstring,
                    "source": source[actual_child.start_byte : actual_child.end_byte],
                }
                blocks.append(block)

            elif child_type == "class_definition":
                name_node = child.child_by_field_name("name")
                name = source[name_node.start_byte : name_node.end_byte] if name_node else "unknown"

                docstring = self._extract_docstring(child, source)
                block = {
                    "name": name,
                    "type": "class",
                    "start_line": child.start_point[0] + 1,
                    "end_line": child.end_point[0] + 1,
                    "start_byte": child.start_byte,
                    "end_byte": child.end_byte,
                    "parent": parent,
                    "docstring": docstring,
                    "source": source[child.start_byte : child.end_byte],
                    "base_classes": self._extract_bases(child, source),
                }
                blocks.append(block)
                body = child.child_by_field_name("body")
                if body:
                    self._walk_tree(body, source, blocks, parent=name)

            elif child_type in ("block", "body"):
                self._walk_tree(child, source, blocks, parent=parent)

    def _extract_docstring(self, node: Any, source: str) -> str | None:
        body = node.child_by_field_name("body")
        if body is None:
            return None
        for stmt in body.children:
            stmt_type = stmt.type if hasattr(stmt, "type") else ""
            if stmt_type == "expression_statement":
                for child in stmt.children:
                    if hasattr(child, "type") and child.type == "string":
                        text = source[child.start_byte : child.end_byte]
                        return text.strip('"').strip("'").strip()
        return None

    def _extract_bases(self, node: Any, source: str) -> list[str]:
        bases = []
        for child in node.children:
            if hasattr(child, "type") and child.type == "argument_list":
                for arg in child.children:
                    if hasattr(arg, "type") and arg.type == "identifier":
                        bases.append(source[arg.start_byte : arg.end_byte])
        return bases

    @staticmethod
    def _extract_fallback(source_code: str) -> list[dict[str, Any]]:
        import re

        blocks: list[dict[str, Any]] = []
        lines = source_code.split("\n")
        pattern = re.compile(r"^\s*(class|def)\s+(\w+)")

        for i, line in enumerate(lines, 1):
            match = pattern.match(line)
            if match:
                kind, name = match.groups()
                blocks.append({
                    "name": name,
                    "type": "class" if kind == "class" else "function",
                    "start_line": i,
                    "end_line": i,
                    "start_byte": 0,
                    "end_byte": 0,
                    "parent": None,
                    "docstring": None,
                    "source": line.strip(),
                })
        return blocks
