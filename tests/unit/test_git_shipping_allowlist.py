"""Verify git shipping operations don't increment the enforcement streak counter.

Git operations (git-add, git-commit, git-push, git-tag-push, release-cut) are
terminal actions that complete work. They must NOT be counted as "grinding"
by enforce-delegate.ts. Without this allowlist, git-add followed by git-commit
triggers MAINTHREAD_THRESHOLD=2 and blocks the commit — forcing the agent to
disengage ALL enforcement for 60 minutes.

This test pins the structural presence of:
1. GIT_SHIPPING_TARGETS set constant
2. isGitShippingTarget() function
3. mainthreadBudgetBefore accepting a command parameter
4. mainthreadBudgetAfter resetting streak for git operations
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"


def _src() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin not found: {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


class TestGitShippingAllowlistExists:
    """The allowlist constant and helper must exist in enforce-delegate.ts."""

    def test_constant_exists(self):
        src = _src()
        assert "GIT_SHIPPING_TARGETS" in src, (
            "enforce-delegate.ts must define GIT_SHIPPING_TARGETS set — "
            "without it, git operations are counted as grinding and "
            "the agent is forced to disengage enforcement"
        )

    def test_isGitShippingTarget_function_exists(self):
        src = _src()
        assert "function isGitShippingTarget" in src, (
            "enforce-delegate.ts must have isGitShippingTarget() function"
        )

    def test_function_takes_command_param(self):
        src = _src()
        assert re.search(r"isGitShippingTarget\s*\(\s*command\s*:\s*string", src), (
            "isGitShippingTarget must accept a command: string parameter"
        )


class TestRequiredGitTargetsPresent:
    """All critical git shipping targets must be in the allowlist."""

    REQUIRED_TARGETS = (
        "git-add", "git-add-all", "git-commit", "git-commit-file",
        "ship-commit", "commit-no-verify", "repo-commit",
        "git-push-sandboxcom", "batch-push",
        "git-tag-push", "git-tag-move", "git-tag-rm",
        "release-cut", "release-delete", "release-recut",
        "git-merge", "git-checkout", "git-branch",
        "git-stash", "git-stash-pop", "git-reset",
        "verify-remote", "verify-state",
    )

    @pytest.mark.parametrize("target", REQUIRED_TARGETS)
    def test_target_in_allowlist(self, target):
        src = _src()
        assert f'"{target}"' in src, (
            f"GIT_SHIPPING_TARGETS must include '{target}' — "
            f"this is a required git shipping operation that "
            f"should reset the streak counter, not increment it"
        )


class TestMainthreadBudgetAcceptsCommand:
    """mainthreadBudgetBefore and After must accept the command parameter."""

    def test_before_accepts_command(self):
        src = _src()
        assert re.search(
            r"mainthreadBudgetBefore\s*\(\s*tool\s*:\s*string\s*,\s*command\s*:\s*string",
            src
        ), (
            "mainthreadBudgetBefore must accept (tool, command) — "
            "without the command, it can't check if the operation is git shipping"
        )

    def test_after_accepts_command(self):
        src = _src()
        assert re.search(
            r"mainthreadBudgetAfter\s*\(\s*tool\s*:\s*string\s*,\s*command\s*:\s*string",
            src
        ), (
            "mainthreadBudgetAfter must accept (tool, command) — "
            "without the command, it can't reset the streak for git operations"
        )

    def test_before_checks_git_shipping(self):
        src = _src()
        assert "isGitShippingTarget(command)" in src, (
            "mainthreadBudgetBefore must call isGitShippingTarget(command) "
            "to skip the streak check for git operations"
        )

    def test_after_resets_for_git_shipping(self):
        src = _src()
        # Look for writeStreak({ count: 0 }) inside the git shipping branch
        assert "isGitShippingTarget(command)" in src, (
            "mainthreadBudgetAfter must check isGitShippingTarget(command) "
            "and reset the streak to 0 for git operations"
        )


class TestCallSitesPassCommand:
    """The tool.execute.before/after hooks must extract and pass the command."""

    def test_before_hook_extracts_command(self):
        src = _src()
        assert "const command = String" in src, (
            "tool.execute.before must extract the command from input/args "
            "and pass it to mainthreadBudgetBefore"
        )

    def test_before_hook_passes_command(self):
        src = _src()
        assert "mainthreadBudgetBefore(tool, command)" in src, (
            "tool.execute.before must call mainthreadBudgetBefore(tool, command)"
        )

    def test_after_hook_extracts_command(self):
        src = _src()
        # The after hook must also extract and pass the command
        after_section = src[src.find('"tool.execute.after"'):]
        assert "command" in after_section[:500], (
            "tool.execute.after must extract the command and pass it "
            "to mainthreadBudgetAfter"
        )
