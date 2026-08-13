"""TDD tests for the commit-target gate-freshness guard.

The bug: `make git-commit-no-verify` and `make commit-bootstrap` bypass the
`.gate-status` freshness + green check that `make git-commit` enforces. An
agent can commit when the gate is red by reaching for these bypass targets
instead of fixing the actual failures. This test file proves the gap is closed:
every commit-shaped target must enforce the same fresh+green `.gate-status`.

Incident (BUGS.md 2026-06-22): agent committed a feature with red gate via
`make git-commit-no-verify`, rationalizing "pre-existing failures + env issue".
The bypass target exists for pre-commit stash conflicts, NOT for skipping the
gate. Gate integrity must hold across ALL commit targets.
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


class TestCommitTargetsEnforceGate:
    """Every commit-shaped target must enforce the fresh+green gate check."""

    def test_commit_no_verify_recipe_exists(self):
        assert _recipe("git-commit-no-verify"), "git-commit-no-verify target must exist"

    def test_commit_bootstrap_recipe_exists(self):
        assert _recipe("commit-bootstrap"), "commit-bootstrap target must exist"

    def test_git_commit_recipe_exists(self):
        assert _recipe("git-commit"), "git-commit target must exist"

    def test_git_commit_does_not_hide_hooks_or_stage_unrelated_changes(self):
        """A green gate cannot be followed by a suppressed pre-commit failure."""
        recipe = _recipe("git-commit")

        assert "pre-commit run --files" in recipe
        assert "2>/dev/null || true" not in recipe
        assert "git diff --name-only | xargs" not in recipe
        assert "git diff --cached --name-only -z" in recipe
        assert "$(UV) run pre-commit run --files" in recipe

    def test_commit_no_verify_enforces_gate(self):
        """git-commit-no-verify must check .gate-status freshness.

        Before: `git commit --no-verify` ran with no gate check — an agent
        could commit a red-gate change by reaching for this target.
        After: the target enforces the SAME lint/typecheck/collect/test/smoke
        PASS + 30-min freshness check as `git-commit`. The `--no-verify` flag
        only skips the pre-commit HOOK STASH, not the gate.

        There is NO bypass. The gate check is unconditional — a red, stale,
        or missing .gate-status always denies the commit.
        """
        recipe = _recipe("git-commit-no-verify")
        gate_enforced = ".gate-status" in recipe or "_gate-fresh-check" in recipe
        assert gate_enforced, "git-commit-no-verify must check .gate-status — it cannot silently bypass the gate"

    def test_commit_bootstrap_enforces_gate(self):
        """commit-bootstrap (feature-branch commit) must also enforce the gate.

        The 'feature branch' rationalization is exactly how a red-gate bypass
        slipped in. A green gate is the universal precondition for ANY commit
        to master or a feature branch.
        """
        recipe = _recipe("commit-bootstrap")
        gate_enforced = ".gate-status" in recipe or "_gate-fresh-check" in recipe
        assert gate_enforced, "commit-bootstrap must check .gate-status — it cannot bypass the gate"

    def test_git_amend_msg_enforces_gate(self):
        """git-amend-msg must check .gate-status — cannot bypass via --amend.

        Incident: git-amend-msg invoked `git commit --amend --no-verify` with
        no gate check, and the structural guard missed it because its block
        began with leading comment lines (the splitter blind spot). Both the
        target and the splitter are now fixed.
        """
        recipe = _recipe("git-amend-msg")
        gate_enforced = ".gate-status" in recipe or "_gate-fresh-check" in recipe
        assert gate_enforced, "git-amend-msg must check .gate-status — it cannot bypass the gate"

    def test_no_commit_target_lacks_gate_check(self):
        """No commit-shaped target may call git commit without a gate check.

        This is the structural guard: scan the Makefile for any target whose
        recipe invokes `git commit` without also referencing `.gate-status`.
        Catches future regressions where someone adds a new bypass target.
        """
        content = MAKEFILE.read_text()
        # Find every target that contains "git commit" in its recipe.
        # Split the Makefile into target blocks on blank-line boundaries.
        blocks = content.split("\n\n")
        # Regression guard: the splitter must reach past leading comment lines
        # and actually inspect the git-amend-msg block (the original blind spot
        # was that its two comment header lines fooled the old "first line is
        # the target name" logic).
        amend_block = next((b for b in blocks if "git-amend-msg:" in b), None)
        assert amend_block is not None, "splitter must locate the git-amend-msg block"
        assert "git commit" in amend_block, "splitter must reach git-amend-msg recipe"
        offenders = []
        for block in blocks:
            # A target block starts with "name:" on its first NON-COMMENT line.
            # Leading comment lines (documentation above the target) must be
            # stripped — otherwise the splitter misreads the first comment line
            # as the "target name" and silently skips the block. This was the
            # git-amend-msg blind spot: its block began with two comment lines,
            # so the guard never reached the `git commit` invocation inside.
            lines = block.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("#")]
            if not lines:
                continue
            first = lines[0].strip()
            if not first.endswith(":") and ":" not in first.split()[0:1]:
                continue
            # Only inspect blocks that contain "git commit" as a real invocation
            # (not a comment, not a variable assignment).
            has_git_commit = any("git commit" in line and not line.strip().startswith("#") for line in lines)
            if not has_git_commit:
                continue
            # Special-case: the .PHONY list, help text, or comments mentioning
            # "git commit" as documentation should not trip the guard.
            target_name = first.split(":")[0].strip()
            if target_name.startswith(".") or target_name in ("help", "usage"):
                continue
            # Targets that legitimately invoke git commit without the gate MUST
            # be on this explicit allowlist with a documented reason.
            ALLOWLIST_NO_GATE = {
                "test-and-commit",
                "repo-commit",
                "ship-commit",  # subagent-dispatch target; CI is the gate
                "git-reset",
                "git-revert",
                "submodule-pin",
                "_auto-commit-specs",  # internal auto-commit for BEHAVIORAL_SPECS.md
            }
            if target_name in ALLOWLIST_NO_GATE:
                continue
            has_gate_check = ".gate-status" in block or "_gate-fresh-check" in block
            if not has_gate_check:
                offenders.append(target_name)
        assert not offenders, "Bypass: " + ", ".join(offenders)


class TestCommitLintGuard:
    """AB030: _commit-lint-guard must be wired into every commit target."""

    def test_lint_guard_exists_in_all_commit_targets(self):
        """Verify _commit-lint-guard appears in every commit target prerequisite."""
        content = MAKEFILE.read_text()
        commit_targets = ["repo-commit", "ship-commit", "git-commit", "commit-no-verify"]
        missing = []
        for target in commit_targets:
            # Find the prerequisite line for this target
            marker = f"\n{target}:"
            assert marker in content, f"Makefile target '{target}' not found"
            start = content.index(marker)
            prereq_end = content.index("\n", start + 1)
            prereq_line = content[content.index(marker) : prereq_end]
            if "_commit-lint-guard" not in prereq_line:
                missing.append(target)
        assert not missing, "These commit targets lack _commit-lint-guard: " + ", ".join(missing)

    def test_no_commit_target_skips_lint(self):
        """No commit target that invokes 'git commit' may escape the lint guard."""
        content = MAKEFILE.read_text()
        blocks = content.split("\n\n")
        offenders = []
        for block in blocks:
            lines = block.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("#")]
            if not lines:
                continue
            first = lines[0].strip()
            if not first.endswith(":") and ":" not in first.split()[0:1]:
                continue
            has_git_commit = any("git commit" in line and not line.strip().startswith("#") for line in lines)
            if not has_git_commit:
                continue
            target_name = first.split(":")[0].strip()
            if target_name.startswith("."):
                continue
            if target_name in ("help", "usage", "git-reset", "git-revert", "submodule-pin", "_auto-commit-specs"):
                continue
            # Check the prerequisite line (first line after the target name)
            prereq_line = first
            has_lint_guard = "_commit-lint-guard" in prereq_line
            # Also check if this is a non-code target that doesn't need the guard
            if not has_lint_guard:
                offenders.append(target_name)
        assert not offenders, "Commit targets without _commit-lint-guard: " + ", ".join(offenders)


class TestMakeGateStatusFile:
    """The gate itself must write .gate-status with the expected fields."""

    def test_gate_status_path_constant(self):
        """The gate status filename must be the same everywhere it's referenced."""
        content = MAKEFILE.read_text()
        # All references should use the literal ".gate-status" filename.
        assert content.count(".gate-status") >= 4, "Expected .gate-status referenced in multiple gate + commit targets"
