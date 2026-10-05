#!/usr/bin/env python3
"""Fix __init__.py drift: missing docstrings, empty namespace inits, unsorted __all__.

Targets only files under src/general_ludd/**. Idempotent. Usage:

    python scripts/fix_init_drift.py           # apply fixes
    python scripts/fix_init_drift.py --check   # report only, exit 1 if drift found
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "general_ludd"


def _ruff_all_sort_key(name: str) -> tuple[int, tuple[tuple[int, object], ...]]:
    """Reproduce ruff RUF022's isort-style ordering: SCREAMING_SNAKE_CASE
    first, then CamelCase, then everything else; within each group a
    natural (digit-run-numeric, case-sensitive) sort."""
    first = name.lstrip("_")[0] if name else ""
    if first.isupper() and name.upper() == name:
        group = 0
    elif first.isupper():
        group = 1
    else:
        group = 2
    natural: list[tuple[int, object]] = []
    for part in re.split(r"(\d+)", name):
        if part.isdigit():
            natural.append((0, int(part)))
        else:
            natural.append((1, tuple(part)))
    return (group, tuple(natural))


DOCSTRINGS: dict[str, str] = {
    "ag15_benchmarks/__init__.py": "AG-15 benchmark suite definitions.",
    "algorithms/__init__.py": "Algorithm library: crypto primitives, data structures, and classic CS algorithms.\n\nConsumers import submodules directly (e.g. ``general_ludd.algorithms.rsa``);\nthe package itself re-exports nothing to keep eager imports light.",
    "benchmark/__init__.py": "Benchmark harnesses and fixtures.",
    "chat/__init__.py": "Chat session primitives.",
    "commands/__init__.py": "CLI command helpers.",
    "compression/__init__.py": "Compression algorithms and transforms.\n\nConsumers import submodules directly; the package re-exports nothing.",
    "config/__init__.py": "Configuration loading and defaults.",
    "distributed/__init__.py": "Distributed systems primitives: consensus, CRDTs, gossip.\n\nConsumers import submodules directly; the package re-exports nothing.",
    "entity/__init__.py": "Entity model primitives.",
    "execution/__init__.py": "Execution helpers.",
    "hardware/__init__.py": "Hardware detection and acceleration helpers.",
    "history/__init__.py": "Conversation and event history storage.",
    "local_model/__init__.py": "Local model discovery and routing.",
    "log_analysis/__init__.py": "Log analysis helpers.",
    "network/__init__.py": "Network primitives: packet filters, routing, streaming.\n\nConsumers import submodules directly; the package re-exports nothing.",
    "notifications/__init__.py": "Notification delivery helpers.",
    "observe/__init__.py": "Observability helpers.",
    "orchestration/__init__.py": "Orchestration helpers.",
    "probabilistic/__init__.py": "Probabilistic data structures: sketches and filters.\n\nConsumers import submodules directly; the package re-exports nothing.",
    "quantization/__init__.py": "Model quantization helpers.",
    "receiver/__init__.py": "Receiver primitives.",
    "renderers/templates/__init__.py": "Template definitions for renderers.",
    "resilience/__init__.py": "Resilience patterns: circuit breakers, bulkheads.\n\nConsumers import submodules directly; the package re-exports nothing.",
    "runner/__init__.py": "Runner helpers for jobs and playbooks.",
    "sagas/__init__.py": "Saga orchestration for long-running multi-step transactions.\n\nConsumers import submodules directly; the package re-exports nothing.",
    "sandbox_exec/__init__.py": "Sandboxed code execution.",
    "searx/__init__.py": "SearX search integration.",
    "ssl/__init__.py": "TLS/SSL helpers and certificate tooling.",
    "storage/__init__.py": "Storage subsystem: MVCC key-value store, versioned records, and transaction engine.\n\nConsumers import submodules directly; the package re-exports nothing.",
    "supervision/__init__.py": "Supervision trees and watchdog helpers.\n\nConsumers import submodules directly; the package re-exports nothing.",
    "templates/__init__.py": "Templates for rendered artifacts.",
    "templates/render/__init__.py": "Rendering pipeline templates.",
    "templates/render/sections/__init__.py": "Rendered document sections.",
    "util/__init__.py": "Small reusable utilities: caches, encodings, semver, uuid.\n\nConsumers import submodules directly; the package re-exports nothing.",
}

# Files that must remain non-empty (namespace-style: submodules are imported
# directly by consumers). A docstring alone is not "meaningful content", so
# these get an explicit empty public surface declaration.
EMPTY_NAMESPACE_INITS: set[str] = {
    "algorithms/__init__.py",
    "compression/__init__.py",
    "distributed/__init__.py",
    "experiments/__init__.py",
    "network/__init__.py",
    "probabilistic/__init__.py",
    "resilience/__init__.py",
    "sagas/__init__.py",
    "storage/__init__.py",
    "supervision/__init__.py",
    "util/__init__.py",
}

EMPTY_INIT_DOCSTRINGS: dict[str, str] = {
    "experiments/__init__.py": "A/B test experiment engine with variants, traffic split, metrics, significance testing, and decision logic.\n\nConsumers import submodules directly; the package re-exports nothing.",
}


def _iter_init_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("__init__.py"))


def _add_docstring(source: str, rel: str, docstring: str) -> str:
    """Insert the module docstring as the first statement if missing."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        return source
    body = f'"""{docstring}\n"""\n'
    if source.startswith("#!"):
        lines = source.splitlines(keepends=True)
        return lines[0] + body + "".join(lines[1:])
    return body + source


def _ensure_namespace_all(source: str) -> str:
    """Append an explicit empty public-surface declaration when the file has
    no meaningful top-level content besides docstring and __future__ imports."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    meaningful = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        meaningful.append(node)
    if meaningful:
        return source
    text = source.rstrip()
    if text.endswith('"""'):
        text += "\n"
    if "__all__" in source:
        return source
    return text + "\n__all__: list[str] = []\n"


def _sort_all(source: str) -> str:
    """Sort a top-level __all__ list/tuple of string literals in ruff
    RUF022 isort-style order, matching the audit test."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    edits: list[tuple[int, int, int, int, str]] = []
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name) and node.targets[0].id == "__all__":
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
            value = node.value
        if value is None or not isinstance(value, (ast.List, ast.Tuple)):
            continue
        elts = value.elts
        if not elts or not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts):
            continue
        names = [e.value for e in elts]
        if names == sorted(names, key=_ruff_all_sort_key):
            continue
        open_ch = "[" if isinstance(value, ast.List) else "("
        close_ch = "]" if isinstance(value, ast.List) else ")"
        quote = '"'
        first_span = ast.get_source_segment(source, elts[0])
        if first_span and first_span.startswith("'"):
            quote = "'"
        edits.append(
            (
                value.lineno,
                value.col_offset,
                value.end_lineno,
                value.end_col_offset,
                (open_ch, close_ch, quote, sorted(names, key=_ruff_all_sort_key)),
            )
        )
    if not edits:
        return source
    lines = source.splitlines(keepends=True)
    result: list[str] = []
    cursor = 0
    line_offsets = [0]
    total = 0
    for line in lines:
        total += len(line)
        line_offsets.append(total)

    def offset(lineno: int, col: int) -> int:
        return line_offsets[lineno - 1] + col

    for start_line, start_col, end_line, end_col, spec in sorted(edits, key=lambda e: e[0]):
        open_ch, close_ch, quote, sorted_names = spec
        start = offset(start_line, start_col)
        end = offset(end_line, end_col)
        result.append(source[cursor:start])
        entry_indent = "    "
        body = [open_ch]
        body.extend(f"{entry_indent}{quote}{name}{quote}," for name in sorted_names)
        body.append(close_ch)
        result.append("\n".join(body))
        cursor = end
    result.append(source[cursor:])
    return "".join(result)


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    changed: list[str] = []
    for path in _iter_init_files():
        rel = _rel(path)
        source = path.read_text(encoding="utf-8")
        original = source

        docstring = DOCSTRINGS.get(rel)
        if docstring:
            source = _add_docstring(source, rel, docstring)

        if rel in EMPTY_NAMESPACE_INITS:
            source = _add_docstring(source, rel, EMPTY_INIT_DOCSTRINGS.get(rel) or DOCSTRINGS[rel])
            source = _ensure_namespace_all(source)

        source = _sort_all(source)

        if source != original:
            changed.append(rel)
            if not args.check:
                path.write_text(source, encoding="utf-8")

    if args.check:
        if changed:
            print("Init drift found:")
            for rel in changed:
                print(f"  - {rel}")
            return 1
        print("No init drift")
        return 0

    for rel in changed:
        print(f"fixed {rel}")
    print(f"fixed {len(changed)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
