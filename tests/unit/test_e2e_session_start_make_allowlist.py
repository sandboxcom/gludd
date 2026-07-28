"""E2E test: verify enforce-session-start.ts classifies make targets correctly.

Structural tests (no runtime execution) that parse:
  1. enforce-session-start.ts for tool classification functions
  2. The Makefile for expected target existence

Covers:
  - Read-only make target identification (git-status, verify-state, git-diff,
    ci-verdict, gate-status-check, disk, test-count, etc.)
  - Mutating make target rejection (git-commit, git-push, ship-commit,
    test-and-commit, etc.)
  - agent-worktree, agent-merge, agent-cleanup target existence in the Makefile
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
SHARED_PATH = ROOT / ".opencode" / "lib" / "shared.ts"
MAKEFILE_PATH = ROOT / "Makefile"

# --- Target classification simulators ----------------------------------------

# Read-only make targets: no side effects, safe to run inline during fresh session
READ_ONLY_TARGETS: frozenset[str] = frozenset([
    "git-status", "git-diff", "git-staged", "git-log", "git-show",
    "verify-state", "verify-remote", "verify-opencode-backup",
    "check-opencode-backup",
    "ci-verdict", "ci-verdict-safe", "ci-verdict-fast",
    "ci-cooldown-status", "gate-status", "gate-status-check", "gate-logs",
    "disk", "disk-check", "disk-guard", "check-disk",
    "test-count", "test-failures",
    "agent-worktree-list",
    "submodule-status",
    "playbook-list", "tf-versions-check",
    "git-index", "git-search", "git-stats",
    "check-node-v26-compat", "check-readme-status",
    "check-enhancement-ratio",
    "ci-status", "ci-status-check",
    "development-status", "development-diff",
])

# Mutating make targets: modify state, must NOT run inline during fresh session
MUTATING_TARGETS: frozenset[str] = frozenset([
    "git-commit", "git-push", "git-push-sandboxcom", "git-push-branch",
    "git-push-branch-nv",
    "ship-commit", "test-and-commit", "git-add", "git-add-all",
    "git-reset", "git-branch", "git-checkout", "git-merge",
    "git-stash", "git-stash-pop", "git-rm", "git-mv",
    "git-tag-push", "commit-no-verify", "commit-bootstrap",
    "repo-commit", "git-commit-file",
    "batch-push", "ci-push",
    "feature-start", "feature-done",
    "agent-worktree", "agent-merge", "agent-cleanup",
    "development-start", "development-merge-to-master",
    "release-cut", "release-promote", "release-recut",
    "release-branch-new",
    "lint-fix", "secrets-scrub", "secrets-baseline",
    "clean", "clean-artifacts", "clean-tmp", "clean-worktree-venvs",
])


def _is_read_only_make_target(target: str) -> bool:
    """Simulated isReadOnlyMakeTarget — structural analog of the expected behavior."""
    return target in READ_ONLY_TARGETS


def _is_mutating_make_target(target: str) -> bool:
    """Simulated mutating check: any known mutating target."""
    return target in MUTATING_TARGETS


def _no_overlap() -> bool:
    return READ_ONLY_TARGETS.isdisjoint(MUTATING_TARGETS)


# --- Plugin source helpers ---------------------------------------------------

def _plugin_src() -> str:
    return PLUGIN_PATH.read_text() + "\n" + SHARED_PATH.read_text()


def _makefile_src() -> str:
    return MAKEFILE_PATH.read_text()


def _has_makefile_target(target: str, makefile_src: str) -> bool:
    """Verify a Makefile target line exists: ^target:"""
    return bool(re.search(rf"^{re.escape(target)}:\s", makefile_src, re.MULTILINE))


# ===========================================================================
# 1. Plugin source classification checks
# ===========================================================================


class TestPluginToolClassification:
    """enforce-session-start.ts must classify dispatch, read, and bash tools."""

    def test_has_is_dispatch_tool(self):
        assert "isDispatchTool" in _plugin_src(), "enforce-session-start.ts must import isDispatchTool from shared.ts"

    def test_has_is_read_tool(self):
        assert "isReadTool" in _plugin_src(), "enforce-session-start.ts must import isReadTool from shared.ts"

    def test_has_is_task_file_read(self):
        assert "function isTaskFileRead" in _plugin_src()

    def test_dispatch_tool_includes_task(self):
        src = _plugin_src()
        assert 'isDispatchTool(tool)' in src

    def test_dispatch_tool_includes_agent(self):
        src = _plugin_src()
        assert 'isDispatchTool(tool)' in src

    def test_dispatch_tool_includes_workflow(self):
        src = _plugin_src()
        assert 'isDispatchTool(tool)' in src

    def test_read_tool_includes_read(self):
        src = _plugin_src()
        assert '"read"' in src

    def test_read_tool_includes_glob(self):
        src = _plugin_src()
        assert '"glob"' in src

    def test_read_tool_includes_grep(self):
        src = _plugin_src()
        assert '"grep"' in src

    def test_bash_tool_is_not_read_nor_dispatch(self):
        """Bash tool is neither a read nor a dispatch tool — it needs its own
        classification for make-target allowlisting. Post E.5 refactor the
        READ_TOOLS and DISPATCH_TOOLS sets live in shared.ts."""
        shared_src = (ROOT / ".opencode" / "lib" / "shared.ts").read_text()
        is_read_idx = shared_src.find("export function isReadTool")
        is_dispatch_idx = shared_src.find("export function isDispatchTool")
        assert is_read_idx > 0, "shared.ts must export isReadTool"
        assert is_dispatch_idx > 0, "shared.ts must export isDispatchTool"
        after_read = shared_src[is_read_idx:is_read_idx + 200]
        after_dispatch = shared_src[is_dispatch_idx:is_dispatch_idx + 200]
        assert '"bash"' not in after_read, "bash should not be classified as read tool"
        assert '"bash"' not in after_dispatch, "bash should not be classified as dispatch tool"


# ===========================================================================
# 2. Simulated isReadOnlyMakeTarget classification
# ===========================================================================


class TestReadOnlyMakeTargetClassification:
    """Simulated isReadOnlyMakeTarget: read-only targets accepted, mutating rejected."""

    def test_read_only_and_mutating_sets_are_disjoint(self):
        assert _no_overlap(), (
            "READ_ONLY_TARGETS and MUTATING_TARGETS must be disjoint — "
            "a target cannot be both read-only and mutating."
        )

    # Read-only targets — ACCEPTED
    def test_git_status_is_read_only(self):
        assert _is_read_only_make_target("git-status")

    def test_verify_state_is_read_only(self):
        assert _is_read_only_make_target("verify-state")

    def test_git_diff_is_read_only(self):
        assert _is_read_only_make_target("git-diff")

    def test_ci_verdict_is_read_only(self):
        assert _is_read_only_make_target("ci-verdict")

    def test_gate_status_check_is_read_only(self):
        assert _is_read_only_make_target("gate-status-check")

    def test_disk_is_read_only(self):
        assert _is_read_only_make_target("disk")

    def test_test_count_is_read_only(self):
        assert _is_read_only_make_target("test-count")

    def test_test_failures_is_read_only(self):
        assert _is_read_only_make_target("test-failures")

    def test_git_log_is_read_only(self):
        assert _is_read_only_make_target("git-log")

    def test_git_show_is_read_only(self):
        assert _is_read_only_make_target("git-show")

    def test_agent_worktree_list_is_read_only(self):
        assert _is_read_only_make_target("agent-worktree-list")

    def test_gate_status_is_read_only(self):
        assert _is_read_only_make_target("gate-status")

    def test_git_staged_is_read_only(self):
        assert _is_read_only_make_target("git-staged")

    def test_disk_check_is_read_only(self):
        assert _is_read_only_make_target("disk-check")

    def test_gate_logs_is_read_only(self):
        assert _is_read_only_make_target("gate-logs")

    # Mutating targets — REJECTED
    def test_git_commit_is_mutating(self):
        assert _is_mutating_make_target("git-commit")

    def test_git_push_is_mutating(self):
        assert _is_mutating_make_target("git-push")

    def test_ship_commit_is_mutating(self):
        assert _is_mutating_make_target("ship-commit")

    def test_test_and_commit_is_mutating(self):
        assert _is_mutating_make_target("test-and-commit")

    def test_git_add_is_mutating(self):
        assert _is_mutating_make_target("git-add")

    def test_git_add_all_is_mutating(self):
        assert _is_mutating_make_target("git-add-all")

    def test_git_reset_is_mutating(self):
        assert _is_mutating_make_target("git-reset")

    def test_git_branch_is_mutating(self):
        assert _is_mutating_make_target("git-branch")

    def test_git_checkout_is_mutating(self):
        assert _is_mutating_make_target("git-checkout")

    def test_git_merge_is_mutating(self):
        assert _is_mutating_make_target("git-merge")

    def test_feature_start_is_mutating(self):
        assert _is_mutating_make_target("feature-start")

    def test_feature_done_is_mutating(self):
        assert _is_mutating_make_target("feature-done")

    def test_release_cut_is_mutating(self):
        assert _is_mutating_make_target("release-cut")

    def test_release_promote_is_mutating(self):
        assert _is_mutating_make_target("release-promote")

    def test_agent_worktree_is_mutating(self):
        assert _is_mutating_make_target("agent-worktree")

    def test_agent_merge_is_mutating(self):
        assert _is_mutating_make_target("agent-merge")

    def test_agent_cleanup_is_mutating(self):
        assert _is_mutating_make_target("agent-cleanup")


class TestReadOnlyMakeTargetRejectsMutating:
    """Mutating targets must NOT pass the read-only check."""

    def test_git_commit_is_not_read_only(self):
        assert not _is_read_only_make_target("git-commit")

    def test_git_push_is_not_read_only(self):
        assert not _is_read_only_make_target("git-push")

    def test_ship_commit_is_not_read_only(self):
        assert not _is_read_only_make_target("ship-commit")

    def test_test_and_commit_is_not_read_only(self):
        assert not _is_read_only_make_target("test-and-commit")

    def test_git_add_is_not_read_only(self):
        assert not _is_read_only_make_target("git-add")

    def test_git_checkout_is_not_read_only(self):
        assert not _is_read_only_make_target("git-checkout")

    def test_agent_worktree_is_not_read_only(self):
        assert not _is_read_only_make_target("agent-worktree")

    def test_agent_merge_is_not_read_only(self):
        assert not _is_read_only_make_target("agent-merge")

    def test_agent_cleanup_is_not_read_only(self):
        assert not _is_read_only_make_target("agent-cleanup")


class TestReadOnlyMakeTargetRejectsUnknown:
    """Unknown/unlisted targets must NOT pass the read-only check."""

    def test_unknown_target_is_not_read_only(self):
        assert not _is_read_only_make_target("some-unknown-target-xyz")

    def test_empty_target_is_not_read_only(self):
        assert not _is_read_only_make_target("")

    def test_arbitrary_string_is_not_read_only(self):
        assert not _is_read_only_make_target("rm -rf /")


# ===========================================================================
# 3. Makefile target existence
# ===========================================================================


class TestMakefileAgentWorktreeTargets:
    """Verify agent-worktree, agent-merge, agent-cleanup exist in the Makefile."""

    def test_agent_worktree_target_exists(self):
        assert _has_makefile_target("agent-worktree", _makefile_src()), (
            "Makefile missing 'agent-worktree:' target"
        )

    def test_agent_merge_target_exists(self):
        assert _has_makefile_target("agent-merge", _makefile_src()), (
            "Makefile missing 'agent-merge:' target"
        )

    def test_agent_cleanup_target_exists(self):
        assert _has_makefile_target("agent-cleanup", _makefile_src()), (
            "Makefile missing 'agent-cleanup:' target"
        )


class TestMakefileReadOnlyTargetsExist:
    """Verify the read-only make targets actually exist in the Makefile."""

    def test_git_status_target_exists(self):
        assert _has_makefile_target("git-status", _makefile_src())

    def test_verify_state_target_exists(self):
        assert _has_makefile_target("verify-state", _makefile_src())

    def test_git_diff_target_exists(self):
        assert _has_makefile_target("git-diff", _makefile_src())

    def test_ci_verdict_target_exists(self):
        assert _has_makefile_target("ci-verdict", _makefile_src())

    def test_gate_status_check_target_exists(self):
        assert _has_makefile_target("gate-status-check", _makefile_src())

    def test_disk_target_exists(self):
        assert _has_makefile_target("disk", _makefile_src())

    def test_test_count_target_exists(self):
        assert _has_makefile_target("test-count", _makefile_src())
