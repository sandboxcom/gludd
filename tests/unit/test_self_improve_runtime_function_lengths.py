"""Focused maintainability contract for managed self-improvement runtimes."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_FILES = (
    _PROJECT_ROOT / "src/general_ludd/self_improve/runtime.py",
    _PROJECT_ROOT / "src/general_ludd/self_improve/codex_comparison.py",
)


@pytest.mark.parametrize("source_path", _RUNTIME_FILES, ids=lambda path: path.name)
def test_self_improve_runtime_functions_fit_one_reviewable_screen(
    source_path: Path,
) -> None:
    """Every function in the managed runtime boundary stays at most 100 lines."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    oversized = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno is not None
        and node.end_lineno - node.lineno + 1 > 100
    }

    assert oversized == {}


def test_evaluate_attempt_stays_below_global_complexity_ceiling() -> None:
    """The critical attempt transaction remains well below the 300-line ceiling."""
    source_path = _RUNTIME_FILES[0]
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    evaluate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate_attempt"
    )

    assert evaluate.end_lineno is not None
    assert evaluate.end_lineno - evaluate.lineno + 1 <= 100
