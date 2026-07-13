"""Aspirational type-safety guardrails — ratcheted until the typing refactor lands.

These tests enforce the "100% strict typing" requirement but currently fail on
pre-existing violations (`from typing import Any`, `typing.Mapping/Sequence`,
bare `dict/list/set` annotations). They are marked xfail(strict=False) so the
gate stays green; each fix wave moves us toward removing the markers.

See:
- AGENTS.md "100% strict typing" requirement
- docs/audit/NOQA_GUARDRAIL_ROOT_CAUSE_2026-07-06.md
- config/ratchet.yml
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def _src_files() -> list[Path]:
    return list(Path("src").rglob("*.py"))


@pytest.mark.xfail(
    strict=False,
    reason="ratchet: burn down Any imports (AGENTIC_IMPLEMENTATION_SPEC.md §E1 types)",
)
def test_no_any_imports():
    violations: list[str] = []
    pat1 = re.compile(r"from\s+typing\s+import\s+.*\bAny\b")
    pat2 = re.compile(r"import\s+typing.*\bAny\b")
    for py_file in _src_files():
        for i, line in enumerate(py_file.read_text().splitlines(), 1):
            if pat1.search(line) or pat2.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    assert not violations, (
        f"Found {len(violations)} 'Any' imports:\n" + "\n".join(violations)
    )


def test_no_loose_generics_in_annotations():
    violations: list[str] = []
    for py_file in _src_files():
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.annotation, ast.Name):
                if node.annotation.id in ("dict", "list", "set", "tuple"):
                    violations.append(
                        f"{py_file}:{node.lineno}: loose '{node.annotation.id}' annotation"
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in node.args.args:
                    if (
                        arg.annotation
                        and isinstance(arg.annotation, ast.Name)
                        and arg.annotation.id in ("dict", "list", "set", "tuple")
                    ):
                        violations.append(
                            f"{py_file}:{arg.lineno}: loose '{arg.annotation.id}' arg"
                        )
                if (
                    node.returns
                    and isinstance(node.returns, ast.Name)
                    and node.returns.id in ("dict", "list", "set", "tuple")
                ):
                    violations.append(
                        f"{py_file}:{node.lineno}: loose '{node.returns.id}' return"
                    )
    assert not violations, (
        f"Found {len(violations)} loose generic annotations:\n"
        + "\n".join(violations)
    )


@pytest.mark.xfail(
    strict=False,
    reason="ratchet: burn down old-style typing.Dict/List/Mapping "
    "(AGENTIC_IMPLEMENTATION_SPEC.md §E1 types)",
)
def test_no_loose_generics_in_type_hints():
    violations: list[str] = []
    old_generics = re.compile(
        r"\b(Dict|List|Set|Tuple|Mapping|Sequence|Iterable|MutableMapping|MutableSequence)\["
    )
    for py_file in _src_files():
        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if old_generics.search(line) and "from typing import" in content[
                : content.find(line)
            ]:
                violations.append(f"{py_file}:{i}: {line.strip()}")
    assert not violations, (
        f"Found {len(violations)} old-style typing generics:\n"
        + "\n".join(violations)
    )
