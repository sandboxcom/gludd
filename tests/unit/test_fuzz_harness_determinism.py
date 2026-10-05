"""Determinism contracts for the deep parser fuzz harness."""

from __future__ import annotations

import ast
from pathlib import Path

HARNESS = Path("tests/unit/test_fuzz_harness_deep.py")


def _qualified_call(node: ast.Call) -> str | None:
    """Return the two-part name for a direct module attribute call."""

    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    return f"{func.value.id}.{func.attr}"


def test_deep_fuzz_harness_uses_only_replayable_entropy() -> None:
    """Reject OS entropy and UUID sources that cannot reproduce a CI failure."""

    tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
    forbidden = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (name := _qualified_call(node)) in {"os.urandom", "uuid.uuid4"}
    }
    assert forbidden == set()


def test_regex_fuzz_classifies_the_nested_set_warning() -> None:
    """Pin explicit inspection of CPython's ambiguous nested-set warning."""

    source = HARNESS.read_text(encoding="utf-8")
    assert "warnings.catch_warnings(record=True)" in source
    assert "Possible nested set" in source
