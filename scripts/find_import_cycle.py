#!/usr/bin/env python3
"""Find and print the exact circular import cycle in src/general_ludd/.

Uses the same AST import graph as tests/unit/test_python_imports_deep.py
but with a robust path-tracking DFS that cannot miss cycles.
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


def _find_cycle_path(graph: dict[str, set[str]]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    parent: dict[str, str | None] = {}

    for key in graph:
        color[key] = WHITE
        parent[key] = None

    def dfs(u: str) -> list[str] | None:
        color[u] = GRAY
        for v in graph.get(u, set()):
            cv = color.get(v)
            if cv is None:
                continue
            if cv == WHITE:
                parent[v] = u
                result = dfs(v)
                if result is not None:
                    return result
            elif cv == GRAY:
                cycle = [v]
                cur = u
                while cur is not None and cur != v:
                    cycle.append(cur)
                    cur = parent.get(cur)
                cycle.append(v)
                cycle.reverse()
                return cycle
        color[u] = BLACK
        return None

    for node in sorted(graph):
        if color.get(node) == WHITE:
            cycle = dfs(node)
            if cycle is not None:
                return cycle

    known_keys = set(graph)
    for node in sorted(graph):
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in known_keys:
                print(f"NOTE: {node} imports {neighbor} which has no file on disk (not in graph keys)", file=sys.stderr)

    return None


def main() -> None:
    graph = _static_import_graph()
    cycle = _find_cycle_path(graph)
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
