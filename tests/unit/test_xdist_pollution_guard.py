"""Structural guard: _LEAKY_ENV_VARS synchronisation with test-code env writes.

Per conftest.py:96-108, the ``_LEAKY_ENV_VARS`` frozenset gates the
``_restore_leaky_env_vars`` autouse fixture, which snapshots and restores
those vars around every test so xdist workers don't cross-pollute.

This test scans all ``tests/**/*.py`` for ``monkeypatch.setenv(`` and
``os.environ[...] =`` calls, extracts the literal variable names, and
asserts every one is present in ``_LEAKY_ENV_VARS``.  If any are missing,
the test fails with the exact frozenset update required — catching the
desync at the gate instead of as a flaky xdist failure in CI.

Spec: A12.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT / "tests"


def _import_leaky_env_vars() -> frozenset[str]:
    spec = importlib.util.spec_from_file_location(
        "conftest", TESTS_DIR / "conftest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._LEAKY_ENV_VARS


# ---------------------------------------------------------------------------
# Scanners: find env-var literals written by test code
# ---------------------------------------------------------------------------

_MONKEYPATCH_SETENV_RE = re.compile(
    r"monkeypatch\s*\.\s*setenv\s*\(\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1\b"
)

_OS_ENVIRON_ASSIGN_RE = re.compile(
    r"os\s*\.\s*environ\s*\[\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1\s*\]\s*=(?!=)"
)


def _scan_setenv_literals(paths: list[Path]) -> set[str]:
    found: set[str] = set()
    for p in paths:
        for match in _MONKEYPATCH_SETENV_RE.finditer(p.read_text()):
            found.add(match.group(2))
    return found


def _scan_environ_assign_literals(paths: list[Path]) -> set[str]:
    found: set[str] = set()
    for p in paths:
        for match in _OS_ENVIRON_ASSIGN_RE.finditer(p.read_text()):
            found.add(match.group(2))
    return found


def _collect_test_py_files() -> list[Path]:
    paths = sorted(p for p in TESTS_DIR.rglob("*.py") if p.is_file())
    this_file = Path(__file__).resolve()
    return [p for p in paths if p.resolve() != this_file]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_all_setenv_literals_are_in_leaky_env_vars():
    """Every monkeypatch.setenv / os.environ[...] = literal in tests/ must be
    listed in _LEAKY_ENV_VARS so the autouse restore fixture cleans it up."""
    leaky = _import_leaky_env_vars()
    py_files = _collect_test_py_files()

    setenv_vars = _scan_setenv_literals(py_files)
    environ_vars = _scan_environ_assign_literals(py_files)
    discovered = setenv_vars | environ_vars

    missing = discovered - leaky
    if not missing:
        return

    lines = ["_LEAKY_ENV_VARS frozenset is missing these vars:"]
    for v in sorted(missing):
        lines.append(f"  - {v}")

    updated = sorted(leaky | missing)
    indent = " " * 4
    frozenset_literal = "frozenset({\n" + "".join(
        f'{indent}"{v}",\n' for v in updated
    ) + "})"
    lines.append("\nUpdate tests/conftest.py _LEAKY_ENV_VARS to:")
    lines.append(frozenset_literal)

    pytest.fail("\n".join(lines))
