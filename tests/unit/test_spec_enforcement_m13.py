"""M13: Worktree git-lock bug is known and documented.

The worktree `.git`-as-file locking gap MUST be documented as a known
issue in AGENTS.md. The `src/general_ludd/git_automation/locking.py`
module must use `git rev-parse --git-common-dir` (or document the gap)
and tests must verify the documentation exists.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


class TestM13WorktreeLockBugDocumented:
    """M13 — worktree git-lock bug is documented and tracked."""

    def test_known_gap_documented_in_agents_md(self):
        agents = ROOT / "AGENTS.md"
        content = agents.read_text()
        assert "KNOWN GAP" in content, "M13: AGENTS.md must document known gaps"
        assert "git locking is broken inside worktrees" in content.lower() or (
            "worktree" in content.lower() and "lock" in content.lower()
        ), "M13: AGENTS.md must document the worktree git-locking gap"

    def test_locking_module_exists(self):
        locking = ROOT / "src" / "general_ludd" / "git_automation" / "locking.py"
        assert locking.exists(), "M13: git_automation/locking.py must exist"

    def test_locking_module_references_git_common_dir(self):
        locking = ROOT / "src" / "general_ludd" / "git_automation" / "locking.py"
        if not locking.exists():
            return
        content = locking.read_text()
        has_fix_reference = (
            "git-common-dir" in content
            or "git rev-parse --git-common-dir" in content
            or "git rev-parse --git-common-dir" in content
        )
        has_documentation = "worktree" in content.lower() or "KNOWN" in content
        assert has_fix_reference or has_documentation, (
            "M13: locking.py must reference git-common-dir fix or document the worktree locking gap"
        )

    def test_check_worktree_health_script_exists(self):
        script = ROOT / "scripts" / "check_worktree_health.py"
        assert script.exists(), "M13: check_worktree_health.py must exist to detect stale worktrees"

    def test_worktree_agent_tests_exist(self):
        tests = ROOT / "tests" / "unit" / "test_agent_worktree_targets.py"
        assert tests.exists(), "M13: test_agent_worktree_targets.py must exist for worktree lifecycle"
