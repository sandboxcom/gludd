"""TDD tests for the commit-target gate-freshness guard.

The bug: `make commit-no-verify` and `make commit-bootstrap` bypass the
`.gate-status` freshness + green check that `make git-commit` enforces. An
agent can commit when the gate is red by reaching for these bypass targets
instead of fixing the actual failures. This test file proves the gap is closed:
every commit-shaped target must enforce the same fresh+green `.gate-status`.

Incident (BUGS.md 2026-06-22): agent committed a feature with red gate via
`make commit-no-verify`, rationalizing "pre-existing failures + env issue".
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
        assert _recipe("commit-no-verify"), "commit-no-verify target must exist"

    def test_commit_bootstrap_recipe_exists(self):
        assert _recipe("commit-bootstrap"), "commit-bootstrap target must exist"

    def test_git_commit_recipe_exists(self):
        assert _recipe("git-commit"), "git-commit target must exist"

    def test_commit_no_verify_enforces_gate(self):
        """The bug fix: commit-no-verify must check .gate-status freshness.

        Before: `git commit --no-verify` ran with no gate check — an agent
        could commit a red-gate change by reaching for this target.
        After: the target enforces the SAME lint/typecheck/collect/test/smoke
        PASS + 30-min freshness check as `git-commit`. The `--no-verify` flag
        only skips the pre-commit HOOK STASH, not the gate.
        """
        recipe = _recipe("commit-no-verify")
        # The check may be inline (`.gate-status` literal) OR delegated to the
        # `_gate-fresh-check` target (which itself checks `.gate-status`).
        gate_enforced = (
            ".gate-status" in recipe or
            "_gate-fresh-check" in recipe
        )
        assert gate_enforced, (
            "commit-no-verify must check .gate-status — it cannot bypass the gate"
        )

    def test_commit_bootstrap_enforces_gate(self):
        """commit-bootstrap (feature-branch commit) must also enforce the gate.

        The 'feature branch' rationalization is exactly how a red-gate bypass
        slipped in. A green gate is the universal precondition for ANY commit
        to master or a feature branch.
        """
        recipe = _recipe("commit-bootstrap")
        gate_enforced = (
            ".gate-status" in recipe or
            "_gate-fresh-check" in recipe
        )
        assert gate_enforced, (
            "commit-bootstrap must check .gate-status — it cannot bypass the gate"
        )

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
        offenders = []
        for block in blocks:
            # A target block starts with "name:" on its first line.
            lines = block.split("\n")
            if not lines:
                continue
            first = lines[0].strip()
            if not first.endswith(":") and ":" not in first.split()[0:1]:
                continue
            # Only inspect blocks that contain "git commit" as a real invocation
            # (not a comment, not a variable assignment).
            has_git_commit = any(
                "git commit" in line and not line.strip().startswith("#")
                for line in lines
            )
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
                # Runs pytest inline before committing — its own micro-gate.
                "test-and-commit",
                # Documented escape hatch (SHIP_DIRTY_TREE_PLAN) for non-code
                # meta-commits: version bumps, docs, release artifacts. NOT for
                # code changes — code commits must go through git-commit /
                # commit-no-verify / commit-bootstrap, all of which enforce the
                # gate. If you abuse repo-commit to land code with a red gate,
                # that is the same bug this test exists to catch.
                "repo-commit",
                # Pure revert utilities — never land content, just unwind it.
                "git-reset", "git-revert",
            }
            if target_name in ALLOWLIST_NO_GATE:
                continue
            # A gate check may be inline (`.gate-status` literal) OR delegated
            # to the `_gate-fresh-check` target (which itself checks `.gate-status`).
            has_gate_check = ".gate-status" in block or "_gate-fresh-check" in block
            if not has_gate_check:
                offenders.append(target_name)
        assert not offenders, (
            "These commit-shaped targets bypass the .gate-status check: "
            + ", ".join(offenders)
        )


class TestMakeGateStatusFile:
    """The gate itself must write .gate-status with the expected fields."""

    def test_gate_status_path_constant(self):
        """The gate status filename must be the same everywhere it's referenced."""
        content = MAKEFILE.read_text()
        # All references should use the literal ".gate-status" filename.
        assert content.count(".gate-status") >= 4, (
            "Expected .gate-status referenced in multiple gate + commit targets"
        )
