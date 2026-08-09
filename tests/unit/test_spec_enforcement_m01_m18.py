"""M01,M02,M06,M08,M12,M15,M18: Makefile merge target enforcement specs.

M01: Never merge without conflict resolution
M02: Merge uses --no-ff
M06: Worktree agent never pushes or merges
M08: Merge conflict abort available
M12: Git-locking works for merge operations
M15: Merge target branch verified before proceeding
M18: Development-status shows merge readiness
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"
AGENTS = ROOT / "AGENTS.md"


def _makefile_content() -> str:
    return MAKEFILE.read_text() if MAKEFILE.exists() else ""


def _target_names(content: str) -> set[str]:
    targets: set[str] = set()
    for line in content.split("\n"):
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)", line.strip())
        if m:
            targets.add(m.group(1))
    return targets


def _find_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


def _agents_content() -> str:
    return AGENTS.read_text() if AGENTS.exists() else ""


class TestM01M02MergeConflictAndNoFF:
    """M01/M02: merge conflict resolution and --no-ff enforcement."""

    def test_m01_merge_conflict_resolution_policy_exists(self):
        agents = _agents_content()
        has_conflict = "merge" in agents.lower() and ("conflict" in agents.lower() or "resolve" in agents.lower())
        assert has_conflict, "M01: AGENTS.md must codify merge conflict resolution policy"

    def test_m02_merge_uses_no_ff(self):
        content = _makefile_content()
        recipe = _find_recipe(content, "git-merge")
        has_no_ff = "--no-ff" in recipe
        assert has_no_ff, "M02: Makefile git-merge target must enforce --no-ff"


class TestM06WorktreeAgentNoPushOrMerge:
    """M06: worktree agents must never push or merge to shared branches."""

    def test_m06_worktree_agent_never_pushes_policy_exists(self):
        agents = _agents_content()
        has_rule = "worktree" in agents.lower() and (
            "never push" in agents.lower() or "must not push" in agents.lower()
        )
        assert has_rule, "M06: AGENTS.md must codify that worktree agents never push or merge"


class TestM08MergeConflictAbort:
    """M08: merge conflict abort target must be available."""

    def test_m08_merge_abort_target_exists(self):
        content = _makefile_content()
        targets = _target_names(content)
        assert "git-merge-abort" in targets, "M08: Makefile must have git-merge-abort target"


class TestM12GitLockingForMerge:
    """M12: git-locking must serialize merge operations."""

    def test_m12_git_locking_source_exists(self):
        locking_path = ROOT / "src" / "general_ludd" / "git_automation" / "locking.py"
        assert locking_path.exists(), "M12: git_automation/locking.py must exist for merge serialization"

    def test_m12_git_locking_referenced_in_policy(self):
        agents = _agents_content()
        has_lock_ref = "git_automation/locking.py" in agents or "git_repo_lock" in agents
        assert has_lock_ref, "M12: AGENTS.md must reference git locking for merge operations"


class TestM15M18MergeTargetAndStatus:
    """M15/M18: merge target verification and development status."""

    def test_m15_merge_target_branch_verification_policy(self):
        agents = _agents_content()
        has_verify = "merge" in agents.lower() and ("branch" in agents.lower() and "verified" in agents.lower())
        assert has_verify, "M15: AGENTS.md must codify merge target branch verification"

    def test_m18_development_status_target_exists(self):
        content = _makefile_content()
        targets = _target_names(content)
        assert "development-status" in targets, "M18: Makefile must have development-status target"

    def test_m18_development_status_shows_merge_readiness(self):
        content = _makefile_content()
        recipe = _find_recipe(content, "development-status")
        has_log = "git log" in recipe or "git-log" in recipe
        assert has_log, "M18: development-status must show commits on development not yet on master"
