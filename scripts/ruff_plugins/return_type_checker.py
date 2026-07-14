#!/usr/bin/env python3
"""Ruff AST plugin: catches bad Python patterns via AST walking.

Patterns detected:
  - Functions returning `Any` (-> Any return annotation)
  - Functions with no return annotation
  - bare `except:` without specific exception types
  - Mutable default arguments (list, dict, set, bytearray)
  - isinstance checks with non-type second arg or bad call shape

Output format: file:line:col: CODE MESSAGE  (ruff-compatible)
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path


class PatternVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.findings: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_mutable_defaults(node)
        self._check_return_annotation(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_mutable_defaults(node)
        self._check_return_annotation(node)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None and node.name is None:
            msg = "BARE-EXCEPT bare `except:` without exception type"
            self.findings.append(f"{self.filename}:{node.lineno}:{node.col_offset}: {msg}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            arg2 = node.args[1]
            _ = node.args[0]
            if not self._is_valid_type_arg(arg2):
                msg = "BAD-ISINSTANCE second arg of isinstance() must be a type or tuple of types"
                self.findings.append(f"{self.filename}:{node.lineno}:{node.col_offset}: {msg}")
        self.generic_visit(node)

    def _check_mutable_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for default in node.args.defaults + node.args.kw_defaults:
            if default is None:
                continue
            if self._is_mutable_literal(default):
                mutable_kind = type(default).__name__
                msg = f"MUTABLE-DEFAULT mutable default argument: {mutable_kind}"
                self.findings.append(f"{self.filename}:{node.lineno}:{node.col_offset}: {msg}")

    def _check_return_annotation(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.returns is None:
            msg = "MISSING-RETURN function has no return type annotation"
            self.findings.append(f"{self.filename}:{node.lineno}:{node.col_offset}: {msg}")
            return
        if isinstance(node.returns, ast.Name) and node.returns.id == "Any":
            msg = "ANY-RETURN function returns `Any` — use a concrete type"
            self.findings.append(f"{self.filename}:{node.lineno}:{node.col_offset}: {msg}")

    @staticmethod
    def _is_mutable_literal(node: ast.expr) -> bool:
        if isinstance(node, ast.List):
            return True
        if isinstance(node, ast.Dict):
            return True
        if isinstance(node, ast.Set):
            return True
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"list", "dict", "set", "bytearray", "defaultdict"}:
                return True
        return False

    @staticmethod
    def _is_valid_type_arg(node: ast.expr) -> bool:
        if isinstance(node, ast.Tuple):
            return all(PatternVisitor._is_valid_type_arg(elt) for elt in node.elts)
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Attribute):
            return True
        if isinstance(node, ast.Subscript):
            return True
        return False


def scan_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    visitor = PatternVisitor(str(path))
    visitor.visit(tree)
    return visitor.findings


def scan_directory(root: Path) -> Iterator[str]:
    for pyfile in sorted(root.rglob("*.py")):
        for finding in scan_file(pyfile):
            yield finding


def main() -> int:
    exit_code = 0
    base = Path(__file__).resolve().parent.parent.parent

    for target in ["src", "tests"]:
        dirpath = base / target
        if not dirpath.is_dir():
            continue
        for finding in scan_directory(dirpath):
            print(finding)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
