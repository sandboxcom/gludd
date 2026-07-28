"""Structural test for the _stash-leak-guard Makefile target.

Verifies the guard is BLOCKING (exit 1) when ANY stash entries exist (>0),
not just when >3 entries accumulate. FORCE=1 must allow bypass. The 2026-07-28
incident reference must be present.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
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


class TestStashLeakGuard:
    """_stash-leak-guard must be BLOCKING at >0 stash entries (not just >3)."""

    def test_target_exists(self):
        """_stash-leak-guard target must exist in the Makefile."""
        recipe = _recipe("_stash-leak-guard")
        assert recipe, "_stash-leak-guard target must be present"

    def test_blocks_at_any_stash_entries_not_just_three(self):
        """Guard must fire when >0 stash entries exist — not only at >3.

        The old threshold (>3) allowed up to 3 stashed changes to accumulate
        without detection. The 2026-07-28 incident proved this was too loose.
        The guard must now block when ANY stash entries exist.
        """
        recipe = _recipe("_stash-leak-guard")
        assert "-gt 0" in recipe, (
            "_stash-leak-guard must check '-gt 0' (block on ANY stash entry), not '-gt 3' or higher threshold"
        )

    def test_stash_count_via_git_stash_list(self):
        """Guard must count stash entries via 'git stash list'."""
        recipe = _recipe("_stash-leak-guard")
        assert "git stash list" in recipe, "_stash-leak-guard must count stash entries via 'git stash list'"

    def test_blocks_with_exit_1_when_stash_exists(self):
        """Guard must exit 1 (hard block) when stash entries exist."""
        recipe = _recipe("_stash-leak-guard")
        assert "exit 1" in recipe, "_stash-leak-guard must exit 1 (hard block) when stash entries exist"

    def test_force_bypass_works(self):
        """FORCE=1 must bypass the block (warn but proceed).

        The FORCE=1 path prints a warning but does NOT exit 1, allowing
        the commit to proceed when the operator explicitly overrides.
        """
        recipe = _recipe("_stash-leak-guard")
        assert "FORCE" in recipe, "_stash-leak-guard must check FORCE for bypass"
        if_block_pos = recipe.find('if [ "$$FORCE"')
        if if_block_pos == -1:
            if_block_pos = recipe.find('if [ "$$FORCE"')
        assert if_block_pos != -1, "_stash-leak-guard must check FORCE to allow bypass"

    def test_force_bypass_warns_about_bypass(self):
        """FORCE=1 path must emit a warning mentioning the bypass."""
        recipe = _recipe("_stash-leak-guard")
        assert "FORCED" in recipe or "FORCE=1" in recipe, (
            "_stash-leak-guard FORCE=1 path must mention the forced bypass"
        )
        assert "git-stash-pop" in recipe, (
            "_stash-leak-guard FORCE=1 path must remind operator to run 'make git-stash-pop'"
        )

    def test_incident_reference_present(self):
        """The 2026-07-28 incident must be referenced in the guard message.

        The incident produced merge conflicts in engine.py and
        test_escalation_no_self_approve.py from 3 accumulated stashes.
        """
        recipe = _recipe("_stash-leak-guard")
        assert "2026-07-28" in recipe, (
            "_stash-leak-guard message must reference the 2026-07-28 incident "
            "(accumulated stashes caused merge conflicts)"
        )
        assert "engine.py" in recipe, "_stash-leak-guard must name the affected file engine.py"
        assert "test_escalation_no_self_approve" in recipe, "_stash-leak-guard must name the affected test file"

    def test_guard_is_wired_as_prerequisite(self):
        """Guard must be a prerequisite on commit-shaped targets.

        git-commit and ship-commit must list _stash-leak-guard as a dependency.
        """
        content = MAKEFILE.read_text()

        commit_line = [ln for ln in content.split("\n") if ln.startswith("git-commit:") or ln.startswith("git-commit ")]
        assert commit_line, "git-commit target must exist"
        assert "_stash-leak-guard" in commit_line[0], "git-commit must list _stash-leak-guard as a prerequisite"

        ship_line = [ln for ln in content.split("\n") if ln.startswith("ship-commit:") or ln.startswith("ship-commit ")]
        assert ship_line, "ship-commit target must exist"
        assert "_stash-leak-guard" in ship_line[0], "ship-commit must list _stash-leak-guard as a prerequisite"

    def test_aa_reference_present(self):
        """The AA028 audit reference must be present in the guard comments."""
        recipe = _recipe("_stash-leak-guard")
        assert "AA028" in recipe, "_stash-leak-guard must reference AA028 audit number in its documentation comment"

    def test_block_message_explains_root_cause(self):
        """The block message must explain what caused the stashes.

        The message must mention that pre-commit hooks stashed changes
        without popping, so the operator understands the root cause.
        """
        recipe = _recipe("_stash-leak-guard")
        assert "popping" in recipe or "pop" in recipe, (
            "_stash-leak-guard block message must explain that pre-commit hooks stashed without popping"
        )
        assert "pre-commit" in recipe, "_stash-leak-guard must mention pre-commit hooks as the cause"

    def test_pass_message_emitted_when_clean(self):
        """When no stash entries exist, guard must emit PASS and continue."""
        recipe = _recipe("_stash-leak-guard")
        assert "PASS" in recipe, "_stash-leak-guard must emit a PASS message when no stash entries exist"
