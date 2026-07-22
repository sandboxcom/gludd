from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles" / "git_automation"


def test_git_automation_role_exposes_full_state_machine_guards() -> None:
    defaults = (ROLE / "defaults" / "main.yml").read_text(encoding="utf-8")
    state_task = (ROLE / "tasks" / "state.yml").read_text(encoding="utf-8")

    for name in [
        "state_worktree_target_ref",
        "state_preserve_branch_patterns",
        "state_assert_no_unintegrated_worktrees",
        "state_assert_no_unintegrated_branches",
    ]:
        assert name in defaults
        assert name in state_task

    assert "main-dirty-preserve-*" in defaults
    assert "preserve-*" in defaults
    assert "general_ludd.agent.gludd_git" in state_task
