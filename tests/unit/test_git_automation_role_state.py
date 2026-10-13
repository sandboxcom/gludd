from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.unit.native_role_tasks import effective_arguments, role_tasks

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles" / "git_automation"


@pytest.fixture(scope="module", autouse=True)
def isolated_ansible_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


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
        "op: current_branch",
        "op: tag_release",
        "op: checkpoint_tag",
        "branch_op: list/create/delete",
        "worktree_op: list/create/remove",
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


def _assert_git_module_delegation(role: Path) -> None:
    for task_name, options, expected in (
        ("clone", {}, "clone"),
        ("branch", {"branch_op": "list"}, "branch_list"),
        ("branch", {"branch_op": "create"}, "branch"),
        ("branch", {"branch_op": "delete"}, "branch_delete"),
        ("worktree", {"worktree_op": "list"}, "worktree_list"),
        ("worktree", {"worktree_op": "create"}, "worktree_create"),
        ("worktree", {"worktree_op": "remove"}, "worktree_remove"),
        ("push", {}, "push"),
    ):
        tasks = list(role_tasks(role, {
            "git_op": task_name, "repo_path": "/repo", "daemon_url": "http://daemon:8000", "psk": "secret",
            "clone_url": "https://example.org/repository.git", "target_dir": "/owned/clone",
            "branch_name": "topic", "worktree_branch": "topic", "worktree_path_param": "/owned/topic",
            "push_branch": "development", **options,
        }))
        assert all(task.action not in {"ansible.builtin.command", "command", "ansible.builtin.shell", "shell"}
                   for task, _ in tasks), task_name
        calls = [effective_arguments(task, templar) for task, templar in tasks
                 if task.action == "general_ludd.agent.gludd_git"]
        assert len(calls) == 1 and calls[0]["op"] == expected, (task_name, options)
        assert calls[0]["path"] == "/repo"
        assert calls[0]["daemon_url"] == "http://daemon:8000" and calls[0]["psk"] == "secret"

    clone_task = (role / "tasks" / "clone.yml").read_text(encoding="utf-8")
    push_task = (role / "tasks" / "push.yml").read_text(encoding="utf-8")

    for token in ["op: clone", "git_clone_timeout", "clone_url", "target_dir"]:
        assert token in clone_task
    for token in ["op: push", "push_remote", "push_branch"]:
        assert token in push_task


def test_git_automation_role_delegates_all_git_ops_to_module() -> None:
    _assert_git_module_delegation(ROLE)


@pytest.mark.parametrize("replacement", (
    'ansible.builtin.command: "git status"',
    'ansible.builtin.debug: {msg: "not the native Git module"}',
))
def test_git_delegation_acceptance_rejects_bypass_of_shared_owner(tmp_path: Path, replacement: str) -> None:
    from shutil import copytree

    role = tmp_path / "git_automation"
    copytree(ROLE, role)
    (role / "tasks/git_request.yml").write_text("---\n- name: Bypass\n  " + replacement + "\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_git_module_delegation(role)
