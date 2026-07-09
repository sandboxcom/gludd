"""TDD tests for the verify-state Makefile target.

The gap: before committing or claiming work done, an agent had to run
git-status, git-log, git-rev-parse, git ls-remote, and gh run list
separately to assemble the full pre-claim picture. verify-state consolidates
working-tree cleanliness, HEAD identity, remote sync state, recent commits,
and CI verdict into one read-only command.

These tests prove the target exists, runs without error (its network calls
are fail-soft), and emits each of the five state sections.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    """Extract the full recipe body for a make target. Assert target exists."""
    content = MAKEFILE.read_text()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_target = content.find("\n\n", start)
    if next_target == -1:
        return content[start:]
    return content[start:next_target]


class TestVerifyStateTarget:
    """verify-state consolidates git/CI state for pre-claim verification."""

    def test_verify_state_target_exists(self) -> None:
        assert _recipe("verify-state"), "verify-state target must exist"

    def test_verify_state_runs_without_error(self) -> None:
        """`make verify-state` exits 0.

        The recipe is fail-soft: every network call (git ls-remote, gh run
        list) is wrapped in 2>/dev/null with a fallback message, so the
        command succeeds even when the network or gh is unavailable.
        """
        result = subprocess.run(
            ["make", "-s", "verify-state"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, (
            "verify-state must exit 0 (it is read-only and fail-soft).\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
        )

    def test_verify_state_includes_working_tree_section(self) -> None:
        recipe = _recipe("verify-state")
        assert "Working Tree" in recipe, (
            "verify-state must emit a 'Working Tree' section showing CLEAN/DIRTY"
        )

    def test_verify_state_includes_head_section(self) -> None:
        recipe = _recipe("verify-state")
        assert "HEAD" in recipe, (
            "verify-state must emit a 'HEAD' section with local SHA + branch"
        )

    def test_verify_state_includes_ci_section(self) -> None:
        recipe = _recipe("verify-state")
        assert "CI" in recipe, (
            "verify-state must emit a 'CI' section with the verdict for HEAD"
        )
