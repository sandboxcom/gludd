#!/usr/bin/env python3
"""Convert bare test environment writes to pytest's restoring fixture.

The checker in :mod:`scripts.check_test_env_writes` deliberately rejects
``os.environ[key] = value`` because it leaks across tests sharing an xdist
worker.  This codemod performs the mechanical, semantics-preserving repair:
replace each assignment with ``monkeypatch.setenv`` and add the fixture to the
containing test function when needed.
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path


class CodemodError(RuntimeError):
    """Raised when a source shape cannot be rewritten safely."""


def _is_environment_target(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "environ"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "os"
    )


def _containing_function(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _has_monkeypatch_argument(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    arguments = (
        list(node.args.posonlyargs)
        + list(node.args.args)
        + list(node.args.kwonlyargs)
    )
    return any(argument.arg == "monkeypatch" for argument in arguments)


def _add_fixture_to_signature(line: str, path: Path, lineno: int) -> str:
    match = re.match(
        r"^(?P<prefix>\s*(?:async\s+)?def\s+\w+\()"
        r"(?P<parameters>[^)]*)"
        r"(?P<suffix>\).*)$",
        line,
    )
    if match is None:
        raise CodemodError(
            f"{path}:{lineno}: multiline or unsupported function signature"
        )
    parameters = match.group("parameters").rstrip()
    separator = ", " if parameters else ""
    return (
        f"{match.group('prefix')}{parameters}{separator}monkeypatch"
        f"{match.group('suffix')}"
    )


def rewrite_file(path: Path) -> int:
    """Rewrite one test file and return the number of converted assignments."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and _is_environment_target(node.targets[0])
    ]
    if not assignments:
        return 0

    lines = source.splitlines()
    trailing_newline = source.endswith("\n")
    functions: set[ast.FunctionDef | ast.AsyncFunctionDef] = set()

    for assignment in assignments:
        if assignment.end_lineno != assignment.lineno:
            raise CodemodError(
                f"{path}:{assignment.lineno}: multiline environment assignment"
            )
        function = _containing_function(assignment, parents)
        if function is None:
            raise CodemodError(
                f"{path}:{assignment.lineno}: environment write must be "
                "inside a test function"
            )
        functions.add(function)

        target = assignment.targets[0]
        assert isinstance(target, ast.Subscript)
        key_source = ast.get_source_segment(source, target.slice)
        value_source = ast.get_source_segment(source, assignment.value)
        if key_source is None or value_source is None:
            raise CodemodError(
                f"{path}:{assignment.lineno}: unable to preserve assignment source"
            )
        original = lines[assignment.lineno - 1]
        suffix = original[assignment.end_col_offset :]
        indentation = original[: assignment.col_offset]
        lines[assignment.lineno - 1] = (
            f"{indentation}monkeypatch.setenv({key_source}, {value_source}){suffix}"
        )

    for function in sorted(functions, key=lambda item: item.lineno, reverse=True):
        if _has_monkeypatch_argument(function):
            continue
        lines[function.lineno - 1] = _add_fixture_to_signature(
            lines[function.lineno - 1],
            path,
            function.lineno,
        )

    rewritten = "\n".join(lines)
    if trailing_newline:
        rewritten += "\n"
    ast.parse(rewritten, filename=str(path))
    path.write_text(rewritten, encoding="utf-8")
    return len(assignments)


def _candidate_files(paths: list[Path]) -> list[Path]:
    candidates: set[Path] = set()
    for path in paths:
        if path.is_dir():
            candidates.update(path.rglob("test_*.py"))
        elif path.suffix == ".py":
            candidates.add(path)
        else:
            raise CodemodError(f"{path}: expected a Python file or directory")
    return sorted(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    total = 0
    changed = 0
    for path in _candidate_files(args.paths):
        count = rewrite_file(path)
        total += count
        if count:
            changed += 1
            print(f"rewrote {count:3d} environment write(s): {path}")
    print(f"converted {total} write(s) across {changed} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
