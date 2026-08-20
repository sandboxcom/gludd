"""Mechanical guardrails for process-global import state in ordered suites."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_ORDER_SENSITIVE_TESTS = (
    "tests/unit/test_language_translation.py",
    "tests/unit/test_radio_antenna_design.py",
    "tests/unit/test_radio_link_budget.py",
    "tests/unit/test_radio_propagation_regulation_exam.py",
)


def _is_sys_attribute(node: ast.expr, attribute: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attribute
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


@pytest.mark.parametrize("relative_path", _ORDER_SENSITIVE_TESTS)
def test_order_sensitive_tests_do_not_mutate_import_globals(relative_path: str) -> None:
    """Reject short-name caches and direct path/argv mutations at their source."""
    path = _ROOT / relative_path
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                node.func.attr in {"append", "insert"}
                and isinstance(owner, ast.Attribute)
                and _is_sys_attribute(owner, "path")
            ):
                violations.append(f"line {node.lineno}: direct sys.path mutation")
            if (
                node.func.attr == "import_module"
                and isinstance(owner, ast.Name)
                and owner.id == "importlib"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "." not in node.args[0].value
            ):
                violations.append(
                    f"line {node.lineno}: short import alias {node.args[0].value!r}"
                )

        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if _is_sys_attribute(target, "argv"):
                    violations.append(f"line {node.lineno}: direct sys.argv mutation")

    assert not violations, f"{relative_path} leaks process-global import state: {violations}"
