#!/usr/bin/env python3
"""analyze_code_paths — AST-based Python source analyzer for E2E test generation.

Usage:
    python analyze_code_paths.py --target-module <path.py> --output <json>

Parses a Python source file with the stdlib ast module to extract:
  - Function and class definitions (name, line range, public/private)
  - Import graph (module names → imported names)
  - Call graph (function → called-functions within its body)
  - Testable code paths (public functions and methods, with dependencies)

Output is written as JSON to ``--output``.  This is a standalone script that
works without tree-sitter; the CodePathAnalyzer (tree-sitter) provides richer
parsing, but the AST fallback is always available.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any


class _FunctionInfo:
    __slots__ = ("calls", "is_public", "line_end", "line_start", "name")

    def __init__(
        self,
        name: str,
        line_start: int,
        line_end: int,
        is_public: bool,
        calls: list[str] | None = None,
    ) -> None:
        self.name = name
        self.line_start = line_start
        self.line_end = line_end
        self.is_public = is_public
        self.calls = calls if calls is not None else []

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "is_public": self.is_public,
            "calls": self.calls,
        }


class _ClassInfo:
    __slots__ = ("is_public", "line_end", "line_start", "methods", "name")

    def __init__(
        self,
        name: str,
        line_start: int,
        line_end: int,
        is_public: bool,
        methods: list[_FunctionInfo] | None = None,
    ) -> None:
        self.name = name
        self.line_start = line_start
        self.line_end = line_end
        self.is_public = is_public
        self.methods = methods if methods is not None else []

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "is_public": self.is_public,
            "methods": [m.to_dict() for m in self.methods],
        }


class _ImportInfo:
    __slots__ = ("module", "names")

    def __init__(self, module: str, names: list[str] | None = None) -> None:
        self.module = module
        self.names = names if names is not None else []

    def to_dict(self) -> dict[str, Any]:
        return {"module": self.module, "names": self.names}


class _CodePathAnalyzerAST:
    """Walks Python AST to extract symbols, imports, and call graphs."""

    def __init__(self) -> None:
        self.functions: list[_FunctionInfo] = []
        self.classes: list[_ClassInfo] = []
        self.imports: list[_ImportInfo] = []
        self._call_graph: dict[str, set[str]] = {}

    def analyze(self, source: str) -> None:
        tree = ast.parse(source)
        self._walk(tree)

    def _walk(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._handle_function(child)
            elif isinstance(child, ast.ClassDef):
                self._handle_class(child)
            elif isinstance(child, ast.Import):
                self._handle_import(child)
            elif isinstance(child, ast.ImportFrom):
                self._handle_import_from(child)
            if not isinstance(child, ast.ClassDef):
                self._walk(child)

    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        calls = _extract_calls(node)
        fn = _FunctionInfo(
            name=node.name,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            is_public=not node.name.startswith("_"),
            calls=calls,
        )
        self._call_graph[fn.name] = set(calls)
        self.functions.append(fn)

    def _handle_class(self, node: ast.ClassDef) -> None:
        methods: list[_FunctionInfo] = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls = _extract_calls(child)
                m = _FunctionInfo(
                    name=child.name,
                    line_start=child.lineno,
                    line_end=child.end_lineno or child.lineno,
                    is_public=not child.name.startswith("_"),
                    calls=calls,
                )
                self._call_graph[f"{node.name}.{m.name}"] = set(calls)
                methods.append(m)
        self.classes.append(
            _ClassInfo(
                name=node.name,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                is_public=not node.name.startswith("_"),
                methods=methods,
            )
        )

    def _handle_import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(_ImportInfo(module=alias.name, names=[alias.asname or alias.name]))

    def _handle_import_from(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        names = [alias.name if alias.asname is None else f"{alias.name} as {alias.asname}" for alias in node.names]
        self.imports.append(_ImportInfo(module=module, names=names))

    def call_graph(self) -> dict[str, list[str]]:
        return {k: sorted(v) for k, v in self._call_graph.items()}

    def testable_paths(self) -> list[dict[str, Any]]:
        paths: list[dict[str, Any]] = []
        for fn in self.functions:
            if fn.is_public:
                paths.append({
                    "target": fn.name,
                    "type": "function",
                    "line_range": [fn.line_start, fn.line_end],
                    "dependencies": sorted(self._call_graph.get(fn.name, set())),
                })
        for cls in self.classes:
            if cls.is_public:
                for m in cls.methods:
                    if m.is_public:
                        fqn = f"{cls.name}.{m.name}"
                        paths.append({
                            "target": fqn,
                            "type": "method",
                            "line_range": [m.line_start, m.line_end],
                            "dependencies": sorted(self._call_graph.get(fqn, set())),
                        })
        return paths

    def to_dict(self) -> dict[str, Any]:
        return {
            "functions": [f.to_dict() for f in self.functions],
            "classes": [c.to_dict() for c in self.classes],
            "imports": [i.to_dict() for i in self.imports],
            "call_graph": self.call_graph(),
            "testable_paths": self.testable_paths(),
        }


def _extract_calls(node: ast.stmt) -> list[str]:
    calls: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _call_name(child.func)
            if name:
                calls.append(name)
    return sorted(set(calls))


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Python source file for E2E test generation"
    )
    parser.add_argument("--target-module", required=True, help="Path to Python source file")
    parser.add_argument("--output", required=True, help="Path for output JSON artifact")
    parser.add_argument("--ast-only", action="store_true", help="Skip tree-sitter, use AST only")

    args = parser.parse_args()

    source_path = args.target_module
    if not Path(source_path).exists():
        print(json.dumps({"error": f"File not found: {source_path}", "status": "failed"}))
        sys.exit(1)

    source_text = Path(source_path).read_text(encoding="utf-8")

    ast_analyzer = _CodePathAnalyzerAST()
    try:
        ast_analyzer.analyze(source_text)
    except SyntaxError as e:
        print(json.dumps({"error": f"Syntax error in {source_path}: {e}", "status": "failed"}))
        sys.exit(1)

    ast_output = ast_analyzer.to_dict()

    symbol_data = {
        "name": source_path,
        "functions": ast_output.get("functions", []),
        "classes": ast_output.get("classes", []),
    }

    output = {
        "module": Path(source_path).name,
        "path": source_path,
        "source": symbol_data,
        "imports": ast_output.get("imports", []),
        "call_graph": ast_output.get("call_graph", {}),
        "testable_paths": ast_output.get("testable_paths", []),
        "testable_path_count": len(ast_output.get("testable_paths", [])),
        "function_count": len(symbol_data.get("functions", [])),
        "class_count": len(symbol_data.get("classes", [])),
        "parser": "ast",
        "status": "completed",
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(json.dumps({
        "module": output["module"],
        "testable_path_count": output["testable_path_count"],
        "function_count": output["function_count"],
        "class_count": output["class_count"],
        "output": str(out_path),
    }))


if __name__ == "__main__":
    main()
