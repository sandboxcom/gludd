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
        "state_reconciled_preserve_heads",
        "state_reconciled_preserve_head_file",
        "state_assert_no_unintegrated_worktrees",
        "state_assert_no_unintegrated_branches",
    ]:
        assert name in defaults
        assert name in state_task

    assert "main-dirty-preserve-*" in defaults
    assert "preserve-*" in defaults
    assert "general_ludd.agent.gludd_git" in state_task


def test_workflow_state_docs_list_full_collection_parity() -> None:
    doc = (ROOT / "docs" / "WORKFLOW_STATE_MACHINE.md").read_text(encoding="utf-8")

    for token in [
        "general_ludd.agent.gludd_git",
        "general_ludd.agent.git_automation",
        "state_assert_gha_matches_local",
        "state_assert_no_unintegrated_worktrees",
        "state_assert_no_unintegrated_branches",
        "state_reconciled_preserve_head_file",
        "config/reconciled_preserved_heads.txt",
        "exact HEAD SHA",
        "Ansible git role context",
        "GIT_TERMINAL_PROMPT=0",
        "git-module-often-marked-as-changed-even-when-nothing-changed",
        "error-your-local-changes-to-the-following-files-would-be-overwritten-by-checkout",
    ]:
        assert token in doc



def test_git_automation_role_exposes_gated_git_operations() -> None:
    defaults = (ROLE / "defaults" / "main.yml").read_text(encoding="utf-8")
    main_task = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
    commit_task = (ROLE / "tasks" / "commit.yml").read_text(encoding="utf-8")
    merge_task = (ROLE / "tasks" / "merge.yml").read_text(encoding="utf-8")

    for token in [
        "gated_commit",
        "gated_merge",
        "commit_files",
        "general_ludd.agent.gludd_git",
        "op: gated_commit",
        "op: gated_merge",
        "gate_cmd",
    ]:
        assert token in defaults + main_task + commit_task + merge_task

    assert "ansible.builtin.command" not in commit_task
    assert "ansible.builtin.command" not in merge_task
