"""Regression tests for shell-safe, observable ``make gate`` phases."""

from pathlib import Path

MAKEFILE = Path(__file__).parents[2] / "Makefile"


def _gate_recipe() -> str:
    content = MAKEFILE.read_text()
    start = content.index("\ngate:")
    next_target = content.index("\ngate-lite:", start)
    return content[start:next_target]


def test_gate_recipe_has_no_literal_escaped_shell_operators() -> None:
    recipe = _gate_recipe()

    assert r"\&\&" not in recipe
    assert r"\(" not in recipe
    assert r"\)" not in recipe


def test_opencode_e2e_is_an_observable_gate_phase() -> None:
    recipe = _gate_recipe()

    assert 'echo "=== GATE PHASE: opencode-e2e ==="' in recipe
    assert 'printf "opencode-e2e " >> .gate-status' in recipe
    assert (
        "$(MAKE) --no-print-directory test-opencode-e2e "
        "> .gate-logs/opencode-e2e.log 2>&1 &&"
    ) in recipe
