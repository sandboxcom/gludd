"""Structural characterization for the self-improvement lifecycle refactor."""

from __future__ import annotations

import ast
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "general_ludd"
_SCOPED_MODULES = (
    _SOURCE_ROOT / "self_improve" / "model_lifecycle.py",
    _SOURCE_ROOT / "self_improve" / "managed_runner.py",
    _SOURCE_ROOT / "self_improve" / "model_candidate_planner.py",
    _SOURCE_ROOT / "self_improve" / "apply.py",
    _SOURCE_ROOT / "self_update" / "applier.py",
)


def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _span(node: ast.AST) -> int:
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    assert isinstance(start, int)
    assert isinstance(end, int)
    return end - start + 1


def test_model_lease_manager_is_below_500_lines() -> None:
    """Keep leasing orchestration small enough to review as one abstraction."""
    path = _SCOPED_MODULES[0]
    classes = [
        node
        for node in _parsed(path).body
        if isinstance(node, ast.ClassDef) and node.name == "ModelLeaseManager"
    ]

    assert len(classes) == 1
    assert _span(classes[0]) < 500


def test_scoped_functions_are_at_most_100_lines() -> None:
    """Pin the requested function-length boundary across lifecycle modules."""
    violations: list[str] = []
    for path in _SCOPED_MODULES:
        for node in ast.walk(_parsed(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lines = _span(node)
            if lines > 100:
                violations.append(f"{path.name}:{node.lineno} {node.name} ({lines})")

    assert violations == []
