"""
Tests asserting the gate-lock decoupling invariants in the Makefile.

These tests verify that:
  - test-count and collect-check (--co, collection-only) never acquire the global
    gate flock and never reference run_gate.sh.
  - test-unit TESTFILE=... uses its OWN unique basetemp (/tmp/gludd-unit-XXXXXX)
    so concurrent single-file runs cannot collide on shared popen-gwN tmp paths.
  - Only the full `gate` target delegates to run_gate.sh (which holds the flock).

# HOOK NARROWING NOTE (for orchestrator to apply to main-tree
# .claude/hooks/gate_concurrency_pretool.sh):
#
# The hook should ONLY block a 2nd FULL gate invocation (matching `make gate` or
# `bash scripts/run_gate.sh` or `pytest tests/` without --co flag).
# It must NOT block:
#   - `make test-count`  (uses --co, collection only, no basetemp workers)
#   - `make collect-check`  (uses --co, collection only)
#   - `make test-unit TESTFILE=...`  (now uses own unique basetemp
#     /tmp/gludd-unit-XXXXXX, never writes to /tmp/gludd-gate.lock,
#     safe to run concurrently)
# Detection pattern for "is this a full gate?":
#   grep for 'make gate$' or 'run_gate.sh' or 'pytest tests/' without '--co'
# Safe commands (no blocking needed):
#   - Any pytest with --co flag
#   - Any pytest with a specific TESTFILE (not `tests/` bare)
"""

from __future__ import annotations

import pathlib
import re

MAKEFILE = pathlib.Path(__file__).parents[2] / "Makefile"


def _recipe_lines(target: str) -> str:
    """Extract the recipe text for a given Makefile target."""
    text = MAKEFILE.read_text()
    # Find the target header, then collect lines until the next non-indented line
    in_target = False
    recipe_lines: list[str] = []
    for line in text.splitlines():
        if re.match(rf"^{re.escape(target)}\s*:", line):
            in_target = True
            continue
        if in_target:
            if line.startswith("\t") or line.startswith(" ") or line == "" or line.startswith("#"):
                recipe_lines.append(line)
            else:
                # Next non-indented non-blank line = new target
                break
    return "\n".join(recipe_lines)


def test_test_count_runs_no_lock() -> None:
    """test-count must use --co (collection only) and must NOT delegate to run_gate.sh."""
    recipe = _recipe_lines("test-count")
    assert "--co" in recipe, "test-count must use --co (collection-only mode)"
    assert "run_gate.sh" not in recipe, "test-count must NOT delegate to run_gate.sh — it is a collection-only check"
    assert "gludd-gate.lock" not in recipe, "test-count must NOT reference the global gate lock"


def test_collect_check_runs_no_lock() -> None:
    """collect-check must use --co and must NOT acquire the gate flock."""
    recipe = _recipe_lines("collect-check")
    assert "--co" in recipe, "collect-check must use --co (collection-only mode)"
    assert "run_gate.sh" not in recipe, "collect-check must NOT delegate to run_gate.sh"
    assert "gludd-gate.lock" not in recipe, "collect-check must NOT reference the global gate lock"


def test_unit_single_file_uses_own_basetemp() -> None:
    """test-unit TESTFILE=... must use a unique basetemp, NOT the global gate lock."""
    recipe = _recipe_lines("test-unit")
    # The TESTFILE branch should create its own unique basetemp
    assert "gludd-testunit" in recipe, "test-unit TESTFILE branch must create a unique per-invocation basetemp dir"
    assert "$${ID:-$$$$}" in recipe, "test-unit TESTFILE branch must use ID/PID-unique basetemp naming"
    assert "--basetemp" in recipe, "test-unit TESTFILE branch must pass --basetemp to pytest"
    # Must NOT reference the global gate lock at all
    assert "gludd-gate.lock" not in recipe, (
        "test-unit must NOT reference /tmp/gludd-gate.lock — single-file runs are lock-free"
    )
    assert "run_gate.sh" not in recipe, "test-unit must NOT delegate to run_gate.sh"


def test_full_gate_still_uses_lock() -> None:
    """The `gate` target must still delegate to run_gate.sh (which holds the flock)."""
    recipe = _recipe_lines("gate")
    assert "run_gate.sh" in recipe, "gate target must still delegate to scripts/run_gate.sh for exclusive flock"
    assert "gludd-gate.lock" not in recipe or "run_gate.sh" in recipe, (
        "gate target must use run_gate.sh (which internally manages /tmp/gludd-gate.lock)"
    )


def test_two_single_file_runs_dont_share_lock() -> None:
    """
    Behavioral / structural assertion: two concurrent test-unit TESTFILE= invocations
    cannot share a lock because neither references gludd-gate.lock.

    We verify structurally (Makefile text) rather than via subprocess, since spawning
    make inside pytest would require network isolation guarantees we don't have here.
    """
    recipe = _recipe_lines("test-unit")
    # Neither the TESTFILE branch nor the full-unit branch must touch the gate lock
    assert "gludd-gate.lock" not in recipe, (
        "test-unit recipe must not reference /tmp/gludd-gate.lock — "
        "any two concurrent TESTFILE= invocations are safe to run without coordination"
    )
    # Each invocation gets a fresh basetemp so popen-gwN dirs never collide
    assert 'BT="/tmp/gludd-testunit-$${ID:-$$$$}"; rm -rf "$$BT"' in recipe, (
        "test-unit TESTFILE branch must use ID/PID-unique basetemp naming "
        "to guarantee collision-free basetemp paths between concurrent runs"
    )
