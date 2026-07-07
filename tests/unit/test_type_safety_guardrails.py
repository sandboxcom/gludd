"""Hard type-safety guardrails — enforced via assert (no warnings.warn).

Aspirational guardrails (Any imports, loose generics, old-style typing.Dict)
live in tests/unit/test_type_safety_aspirational.py and are ratcheted until
the strict-typing refactor burns down.

Allowlisted files contain the suppression patterns as DATA (string literals,
frozenset entries, regex fixtures) — NOT as live suppression comments. They
are the policy's own enforcement code. This list MUST match the
ALLOWLIST_PATHS export in .opencode/plugin/enforce-no-suppressions.ts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SUPPRESSION_ALLOWLIST: tuple[str, ...] = (
    "src/general_ludd/security/fix_not_disable.py",
)


def get_python_files() -> list[Path]:
    """Get all Python files in src/, excluding the suppression-pattern allowlist."""
    src_root = Path("src")
    return [
        p
        for p in src_root.rglob("*.py")
        if not any(allowed in str(p) for allowed in SUPPRESSION_ALLOWLIST)
    ]


def test_no_noqa_comments():
    """No # noqa comments in source files — fix the underlying issue."""
    violations: list[str] = []
    noqa_pattern = re.compile(r"#\s*noqa")
    for py_file in get_python_files():
        for i, line in enumerate(py_file.read_text().splitlines(), 1):
            if noqa_pattern.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    assert not violations, (
        f"Found {len(violations)} # noqa comments:\n" + "\n".join(violations)
    )


def test_no_type_ignore_comments():
    """No # type: ignore comments in source files — fix the underlying issue."""
    violations: list[str] = []
    ignore_pattern = re.compile(r"#\s*type:\s*ignore")
    for py_file in get_python_files():
        for i, line in enumerate(py_file.read_text().splitlines(), 1):
            if ignore_pattern.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    assert not violations, (
        f"Found {len(violations)} # type: ignore comments:\n" + "\n".join(violations)
    )


@pytest.mark.xfail(strict=False, reason="ratchet: burn down cast(Any) in src/")
def test_no_cast_any():
    """No cast(Any, ...) usages — narrow with a typed cast target instead."""
    violations: list[str] = []
    pat = re.compile(r"cast\s*\(\s*Any\s*,")
    for py_file in get_python_files():
        for i, line in enumerate(py_file.read_text().splitlines(), 1):
            if pat.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    assert not violations, (
        f"Found {len(violations)} cast(Any, ...) usages:\n" + "\n".join(violations)
    )
