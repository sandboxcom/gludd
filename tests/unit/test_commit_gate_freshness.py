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

        The gate check may be skipped ONLY via the documented escape hatch
        `GLUDD_CI_IS_GATE=1` (see test_commit_no_verify_override_is_escape_hatch).
        In that case the target must still reference GLUDD_CI_IS_GATE so the
        skip is explicit, not silent.
        """
        recipe = _recipe("commit-no-verify")
        # The check may be inline (`.gate-status` literal) OR delegated to the
        # `_gate-fresh-check` target (which itself checks `.gate-status`) OR
        # conditionally skipped via the `GLUDD_CI_IS_GATE=1` escape hatch, in
        # which case the variable reference must appear in the recipe so the
        # bypass is visible (never a silent skip).
        gate_enforced = (
            ".gate-status" in recipe or
            "_gate-fresh-check" in recipe or
            "GLUDD_CI_IS_GATE" in recipe
        )
        assert gate_enforced, (
            "commit-no-verify must check .gate-status (or reference the "
            "GLUDD_CI_IS_GATE escape hatch) — it cannot silently bypass the gate"
        )

    def test_commit_no_verify_has_ci_is_gate_override(self):
        """commit-no-verify must mention GLUDD_CI_IS_GATE in its recipe.

        The override exists for the specific case where the local gate takes
        longer than the bash tool timeout (>30 min) and CI shards validate the
        change faster. The variable reference must appear in the recipe body so
        the escape hatch is grep-discoverable and auditable.
        """
        recipe = _recipe("commit-no-verify")
        assert "GLUDD_CI_IS_GATE" in recipe, (
            "commit-no-verify must reference GLUDD_CI_IS_GATE so the escape "
            "hatch is explicit and auditable, not silent"
        )

    def test_commit_no_verify_override_is_escape_hatch_not_default(self):
        """The GLUDD_CI_IS_GATE override must be documented as an escape hatch.

        The DEFAULT behaviour of commit-no-verify is to run the gate check.
        Skipping the gate is only acceptable as a documented escape hatch for
        the slow-local-gate / CI-is-gate case — never a silent or default off.
        This test verifies:
          1. the comment block documents it as an "escape hatch", and
          2. the recipe's default branch (no env var set) runs the gate check,
             so the override is opt-in rather than opt-out.
        """
        content = MAKEFILE.read_text()
        marker = "\ncommit-no-verify:"
        idx = content.index(marker)
        # The comment block immediately precedes the target. Walk backward to
        # capture the contiguous comment lines above the target header.
        header_start = idx
        comment_start = header_start
        while comment_start > 0:
            prev_newline = content.rfind("\n", 0, comment_start - 1)
            line_start = 0 if prev_newline == -1 else prev_newline + 1
            line = content[line_start:comment_start - 1] if prev_newline != -1 else content[:comment_start]
            if line.lstrip().startswith("#"):
                comment_start = line_start
            else:
                break
        block = content[comment_start:idx + len(marker)]
        # 1. The comment block must call out "escape hatch".
        assert "escape hatch" in block.lower(), (
            "commit-no-verify comment must document GLUDD_CI_IS_GATE as an "
            "escape hatch, not as a recommended default"
        )
        # 2. The default branch (when GLUDD_CI_IS_GATE != 1) must still run
        #    the gate check — i.e. the recipe must reference _gate-fresh-check
        #    or .gate-status in addition to the override variable.
        recipe = _recipe("commit-no-verify")
        assert ".gate-status" in recipe or "_gate-fresh-check" in recipe, (
            "commit-no-verify default branch (no GLUDD_CI_IS_GATE) must still "
            "run the gate check — the override may only SKIP, not REPLACE"
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

    def test_git_amend_msg_enforces_gate(self):
        """git-amend-msg must check .gate-status — cannot bypass via --amend.

        Incident: git-amend-msg invoked `git commit --amend --no-verify` with
        no gate check, and the structural guard missed it because its block
        began with leading comment lines (the splitter blind spot). Both the
        target and the splitter are now fixed.
        """
        recipe = _recipe("git-amend-msg")
        gate_enforced = (
            ".gate-status" in recipe or
            "_gate-fresh-check" in recipe
        )
        assert gate_enforced, (
            "git-amend-msg must check .gate-status — it cannot bypass the gate"
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
                "test-and-commit",
                "repo-commit",
                "ship-commit",  # subagent-dispatch target; CI is the gate
                "git-reset", "git-revert",
            }
            if target_name in ALLOWLIST_NO_GATE:
                continue
            has_gate_check = ".gate-status" in block or "_gate-fresh-check" in block or "GLUDD_CI_IS_GATE" in block
            if not has_gate_check:
                offenders.append(target_name)
        assert not offenders, "Bypass: " + ", ".join(offenders)
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
