#!/usr/bin/env python3
"""Find and print the exact circular import cycle in src/general_ludd/.

Uses the same AST import graph and DFS logic as
tests/unit/test_python_imports_deep.py::_static_import_graph + _has_cycle.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_PKG = ROOT / "src" / "general_ludd"
PKG_NAME = "general_ludd"


def _subpackage_of(module: str, parent: str) -> bool:
    return module == parent or module.startswith(parent + ".")


def _path_to_module(path: Path) -> str:
    rel = path.relative_to(SRC_PKG.parent)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _collect_py_files() -> list[Path]:
    pf: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(SRC_PKG):
        for fn in filenames:
            if fn.endswith(".py"):
                pf.append(Path(dirpath) / fn)
    return sorted(pf)


def _static_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for p in _collect_py_files():
        mod = _path_to_module(p)
        graph[mod] = set()
        if p.name == "__init__.py":
            continue
        src = p.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _subpackage_of(alias.name, PKG_NAME):
                        graph[mod].add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                if _subpackage_of(node.module, PKG_NAME):
                    graph[mod].add(node.module)
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    color: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    for v in graph:
        color[v] = 0
        parent[v] = None

    def _dfs_cycle(u: str) -> list[str] | None:
        color[u] = 1
        for v in graph.get(u, set()):
            if v not in color:
                color[v] = 0
                parent[v] = u
                cycle = _dfs_cycle(v)
                if cycle is not None:
                    return cycle
            elif color.get(v) == 1:
                cycle_path = [v]
                cur = u
                while cur is not None and cur != v:
                    cycle_path.append(cur)
                    cur = parent.get(cur)
                cycle_path.append(v)
                cycle_path.reverse()
                return cycle_path
        color[u] = 2
        return None

    for node in sorted(graph):
        if color.get(node) == 0:
            cycle = _dfs_cycle(node)
            if cycle is not None:
                return cycle
    return None


def main() -> None:
    graph = _static_import_graph()
    cycle = _find_cycle(graph)
    if cycle is None:
        print("No circular import cycle found.", file=sys.stderr)
        sys.exit(0)

    print(f"\nCYCLE: {' -> '.join(cycle)}", file=sys.stderr)
    for i, a in enumerate(cycle):
        b = cycle[(i + 1) % len(cycle)]
        print(f"  {a} imports {b}", file=sys.stderr)

    sys.exit(1)


if __name__ == "__main__":
    main()
