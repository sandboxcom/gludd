"""Unit test for the gludd_git Ansible module.

Loads the real shipped module via importlib, drives main() with a mocked
AnsibleModule + an injected fake GitAutomation (NO real git runs), and asserts
the commit/branch behaviour plus the worktree/merge/push ops added on top of the
hardened Python control plane. Every git boundary is mocked; the module is a
thin delegating wrapper, so the test pins that it (a) delegates to the library,
(b) routes the commit dirty-check through changed_files() (under the lock, not a
bare `git status`), and (c) surfaces the typed results.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from general_ludd.git_automation.types import (
    GatedCommitResult,
    GitStateResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)

ROOT = Path(__file__).parent.parent.parent
MODULE_PATH = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
    / "plugins" / "modules" / "gludd_git.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_git", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAnsibleModule:
    def __init__(self, params: dict[str, Any], check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class _FakeGit:
    """Records calls and returns canned typed results."""

    def __init__(self, repo_path: str = ".", **behaviour: Any) -> None:
        self.repo_path = repo_path
        self.calls: list[tuple] = []
        self._behaviour = behaviour

    def changed_files(self) -> list[str]:
        self.calls.append(("changed_files",))
        return self._behaviour.get("changed_files", [])

    def commit(self, message: str) -> str:
        self.calls.append(("commit", message))
        return self._behaviour.get("commit_sha", "abc1234")

    def create_branch(self, name: str) -> str:
        self.calls.append(("create_branch", name))
        if self._behaviour.get("branch_exists"):
            raise subprocess.CalledProcessError(
                1, "git branch", stderr="fatal: a branch named 'x' already exists"
            )
        return name

    def list_worktrees(self, repo_path: str) -> list[WorktreeInfo]:
        self.calls.append(("list_worktrees", repo_path))
        return [WorktreeInfo(path="/wt/a", branch="main", commit="deadbeef")]

    def create_worktree(self, repo_path: str, branch: str, wt_path: str) -> WorktreeResult:
        self.calls.append(("create_worktree", repo_path, branch, wt_path))
        if self._behaviour.get("worktree_reject"):
            raise ValueError("refusing worktree path containing '..' traversal")
        return WorktreeResult(path=wt_path, branch=branch, success=True)

    def remove_worktree(self, repo_path: str, wt_path: str) -> bool:
        self.calls.append(("remove_worktree", repo_path, wt_path))
        return True

    def merge_branch(self, repo_path: str, source: str, target: str, strategy: str = "ff") -> MergeResult:
        self.calls.append(("merge_branch", repo_path, source, target, strategy))
        return MergeResult(success=True, strategy=strategy, message="merged")

    def push_to_remote(self, repo_path: str, remote: str = "origin", branch: str | None = None) -> PushResult:
        self.calls.append(("push_to_remote", repo_path, remote, branch))
        return PushResult(success=True, remote=remote, branch=branch or "")

    def gated_commit(self, files: list[str], message: str, gate_cmd: list[str]) -> GatedCommitResult:
        self.calls.append(("gated_commit", files, message, gate_cmd))
        failure = self._behaviour.get("gated_commit_failure", "")
        return GatedCommitResult(
            success=not bool(failure),
            commit_sha=None if failure else "gate1234",
            gate_returncode=1 if failure else 0,
            message=failure or "committed",
        )

    def gated_merge(self, source: str, target: str, gate_cmd: list[str], strategy: str = "ff") -> GatedCommitResult:
        self.calls.append(("gated_merge", source, target, gate_cmd, strategy))
        failure = self._behaviour.get("gated_merge_failure", "")
        return GatedCommitResult(
            success=not bool(failure),
            commit_sha=None if failure else "merge1234",
            gate_returncode=1 if failure else 0,
            message=failure or "merged",
        )

    def workflow_state(self, **kwargs: Any) -> GitStateResult:
        self.calls.append(("workflow_state", kwargs))
        errors = self._behaviour.get("state_errors", [])
        return GitStateResult(
            success=not errors,
            branch="development",
            head="abc1234",
            dirty_count=0,
            remote=kwargs.get("remote", "sandboxcom"),
            remote_ref="refs/heads/development",
            remote_head="abc1234",
            errors=errors,
        )

def _params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "path": "/repo",
        "op": "commit",
        "message": None,
        "files": [],
        "gate_cmd": [],
        "branch": None,
        "worktree_path": None,
        "source": None,
        "target": None,
        "strategy": "ff",
        "remote": "origin",
        "state_ref": "",
        "state_gha_head_sha": "",
        "state_worktree_target_ref": "HEAD",
        "state_preserve_branch_patterns": [],
        "state_reconciled_preserve_heads": [],
        "state_reconciled_preserve_head_file": "config/reconciled_preserved_heads.txt",
        "state_assert_clean": False,
        "state_assert_no_feature_on_master": False,
        "state_assert_merge_ready": False,
        "state_assert_remote_head": False,
        "state_assert_gha_matches_local": False,
        "state_assert_no_unintegrated_worktrees": False,
        "state_assert_no_unintegrated_branches": False,
    }
    params.update(overrides)
    return params


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def _inject_fake_git(module: ModuleType, monkeypatch: pytest.MonkeyPatch, **behaviour: Any) -> dict:
    """Inject a fake general_ludd.git_automation.repo.GitAutomation; capture instance."""
    holder: dict = {}

    def _factory(repo_path: str = ".") -> _FakeGit:
        git = _FakeGit(repo_path, **behaviour)
        holder["git"] = git
        return git

    fake_pkg = types.ModuleType("general_ludd.git_automation.repo")
    cast(Any, fake_pkg).GitAutomation = _factory
    monkeypatch.setitem(sys.modules, "general_ludd.git_automation.repo", fake_pkg)
    return holder


def _run(module: ModuleType, monkeypatch: pytest.MonkeyPatch, params: dict, **behaviour: Any):
    fake_mod = _FakeAnsibleModule(params, check_mode=params.pop("_check_mode", False))
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    holder = _inject_fake_git(module, monkeypatch, **behaviour)
    module.main()
    return fake_mod, holder.get("git")


# --- commit -----------------------------------------------------------------

def test_commit_with_changes_uses_changed_files_not_bare_status(module, monkeypatch):
    fake_mod, git = _run(
        module, monkeypatch, _params(op="commit", message="m"), changed_files=["a.py"]
    )
    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["sha"] == "abc1234"
    # Lock-respecting dirty-check: changed_files() was called, no bare git status.
    assert ("changed_files",) in git.calls
    assert ("commit", "m") in git.calls


def test_commit_nothing_to_commit_is_unchanged(module, monkeypatch):
    fake_mod, git = _run(
        module, monkeypatch, _params(op="commit", message="m"), changed_files=[]
    )
    assert fake_mod.exited["changed"] is False
    assert ("commit", "m") not in git.calls


# --- branch -----------------------------------------------------------------

def test_branch_create_is_changed(module, monkeypatch):
    fake_mod, _git = _run(module, monkeypatch, _params(op="branch", branch="feature/x"))
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["branch"] == "feature/x"


def test_branch_already_exists_is_unchanged(module, monkeypatch):
    fake_mod, _git = _run(
        module, monkeypatch, _params(op="branch", branch="feature/x"), branch_exists=True
    )
    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is False


# --- worktree_list (read-only, check-mode safe) -----------------------------

def test_worktree_list_returns_typed_rows(module, monkeypatch):
    fake_mod, _git = _run(module, monkeypatch, _params(op="worktree_list"))
    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is False
    wts = fake_mod.exited["result"]["worktrees"]
    assert wts[0]["path"] == "/wt/a"
    assert wts[0]["branch"] == "main"


def test_worktree_list_runs_in_check_mode(module, monkeypatch):
    fake_mod, git = _run(
        module, monkeypatch, _params(op="worktree_list", _check_mode=True)
    )
    assert fake_mod.failed is None
    assert ("list_worktrees", "/repo") in git.calls


# --- worktree_create / remove ----------------------------------------------

def test_worktree_create_delegates(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(op="worktree_create", branch="agent/x", worktree_path="/wt/x"),
    )
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["result"]["success"] is True
    assert ("create_worktree", "/repo", "agent/x", "/wt/x") in git.calls


def test_worktree_create_security_reject_is_clean_failure(module, monkeypatch):
    fake_mod, _git = _run(
        module,
        monkeypatch,
        _params(op="worktree_create", branch="agent/x", worktree_path="../evil"),
        worktree_reject=True,
    )
    assert fake_mod.exited is None
    assert "rejected" in fake_mod.failed["msg"]


def test_worktree_remove_delegates(module, monkeypatch):
    fake_mod, _git = _run(
        module, monkeypatch, _params(op="worktree_remove", worktree_path="/wt/x")
    )
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["result"]["removed"] is True


# --- merge / push -----------------------------------------------------------

def test_merge_delegates_with_strategy(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(op="merge", source="feature/x", target="main", strategy="no-ff"),
    )
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["result"]["strategy"] == "no-ff"
    assert ("merge_branch", "/repo", "feature/x", "main", "no-ff") in git.calls


def test_push_delegates(module, monkeypatch):
    fake_mod, git = _run(
        module, monkeypatch, _params(op="push", branch="main", remote="upstream")
    )
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["result"]["remote"] == "upstream"
    assert ("push_to_remote", "/repo", "upstream", "main") in git.calls


def test_mutating_op_check_mode_does_not_call_git(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(op="push", branch="main", _check_mode=True),
    )
    assert fake_mod.failed is None
    assert fake_mod.exited["result"]["would_change"] is True
    # No real push happened.
    assert all(c[0] != "push_to_remote" for c in git.calls)


# --- workflow state ----------------------------------------------------------


def test_state_op_delegates_to_workflow_state(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(
            op="state",
            remote="sandboxcom",
            state_ref="development",
            state_assert_clean=True,
            state_assert_remote_head=True,
        ),
    )

    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is False
    assert fake_mod.exited["result"]["success"] is True
    assert fake_mod.exited["result"]["remote"] == "sandboxcom"
    assert git.calls[0][0] == "workflow_state"
    assert git.calls[0][1]["assert_clean"] is True
    assert git.calls[0][1]["assert_remote_head"] is True


def test_state_op_delegates_complete_state_machine_surface(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(
            op="state",
            remote="sandboxcom",
            state_ref="fix/full-run",
            state_gha_head_sha="abc123def456",
            state_worktree_target_ref="development",
            state_preserve_branch_patterns=["main-dirty-preserve-*", "preserve-*"],
            state_reconciled_preserve_heads=["1111111", "2222222"],
            state_reconciled_preserve_head_file="config/custom-preserve-heads.txt",
            state_assert_clean=True,
            state_assert_no_feature_on_master=True,
            state_assert_merge_ready=True,
            state_assert_remote_head=True,
            state_assert_gha_matches_local=True,
            state_assert_no_unintegrated_worktrees=True,
            state_assert_no_unintegrated_branches=True,
        ),
    )

    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is False
    assert git.calls[0][0] == "workflow_state"
    assert git.calls[0][1] == {
        "remote": "sandboxcom",
        "ref": "fix/full-run",
        "gha_head_sha": "abc123def456",
        "worktree_target_ref": "development",
        "preserve_branch_patterns": ("main-dirty-preserve-*", "preserve-*"),
        "reconciled_preserve_heads": ("1111111", "2222222"),
        "reconciled_preserve_head_file": "config/custom-preserve-heads.txt",
        "assert_clean": True,
        "assert_no_feature_on_master": True,
        "assert_merge_ready": True,
        "assert_remote_head": True,
        "assert_gha_matches_local": True,
        "assert_no_unintegrated_worktrees": True,
        "assert_no_unintegrated_branches": True,
    }

def test_state_op_fails_when_workflow_state_has_errors(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(op="state", state_assert_merge_ready=True),
        state_errors=["master has commits not contained in development"],
    )

    assert fake_mod.exited is None
    assert fake_mod.failed["msg"] == "git state guard failed"
    assert fake_mod.failed["result"]["success"] is False
    assert git.calls[0][0] == "workflow_state"


def test_state_op_delegates_unintegrated_worktree_guard(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(
            op="state",
            state_worktree_target_ref="development",
            state_assert_no_unintegrated_worktrees=True,
        ),
    )

    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is False
    assert git.calls[0][0] == "workflow_state"
    assert git.calls[0][1]["worktree_target_ref"] == "development"
    assert git.calls[0][1]["assert_no_unintegrated_worktrees"] is True


def test_state_op_delegates_unintegrated_branch_guard(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(
            op="state",
            state_worktree_target_ref="development",
            state_preserve_branch_patterns=["main-dirty-preserve-*"],
            state_assert_no_unintegrated_branches=True,
        ),
    )

    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is False
    assert git.calls[0][0] == "workflow_state"
    assert git.calls[0][1]["worktree_target_ref"] == "development"
    assert git.calls[0][1]["preserve_branch_patterns"] == ("main-dirty-preserve-*",)
    assert git.calls[0][1]["assert_no_unintegrated_branches"] is True


def test_state_op_delegates_reconciled_preserved_head_inputs(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(
            op="state",
            state_reconciled_preserve_heads=["preservehead"],
            state_reconciled_preserve_head_file="config/custom-preserve-heads.txt",
            state_assert_no_unintegrated_branches=True,
        ),
    )

    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is False
    assert git.calls[0][0] == "workflow_state"
    assert git.calls[0][1]["reconciled_preserve_heads"] == ("preservehead",)
    assert git.calls[0][1]["reconciled_preserve_head_file"] == "config/custom-preserve-heads.txt"
    assert git.calls[0][1]["assert_no_unintegrated_branches"] is True


# --- gated commit / merge ----------------------------------------------------


def test_gated_commit_delegates_files_and_gate_command(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(
            op="gated_commit",
            message="m",
            files=["a.py", "b.py"],
            gate_cmd=["make", "gate"],
        ),
    )

    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["result"]["commit_sha"] == "gate1234"
    assert ("gated_commit", ["a.py", "b.py"], "m", ["make", "gate"]) in git.calls


def test_gated_commit_requires_non_empty_gate_command(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(op="gated_commit", message="m", files=["."]),
    )

    assert fake_mod.exited is None
    assert fake_mod.failed["msg"] == "gated_commit requires non-empty gate_cmd"
    assert git.calls == []


def test_gated_commit_fails_closed_with_result_payload(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(op="gated_commit", message="m", files=["."], gate_cmd=["make", "gate"]),
        gated_commit_failure="gate failed",
    )

    assert fake_mod.exited is None
    assert fake_mod.failed["msg"] == "gated_commit failed"
    assert fake_mod.failed["result"]["success"] is False
    assert ("gated_commit", ["."], "m", ["make", "gate"]) in git.calls


def test_gated_merge_delegates_gate_command_and_strategy(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(
            op="gated_merge",
            source="feature/x",
            target="main",
            strategy="no-ff",
            gate_cmd=["make", "gate"],
        ),
    )

    assert fake_mod.failed is None
    assert fake_mod.exited["changed"] is True
    assert fake_mod.exited["result"]["commit_sha"] == "merge1234"
    assert ("gated_merge", "feature/x", "main", ["make", "gate"], "no-ff") in git.calls


def test_gated_merge_fails_closed_with_result_payload(module, monkeypatch):
    fake_mod, git = _run(
        module,
        monkeypatch,
        _params(
            op="gated_merge",
            source="feature/x",
            target="main",
            gate_cmd=["make", "gate"],
        ),
        gated_merge_failure="gate failed",
    )

    assert fake_mod.exited is None
    assert fake_mod.failed["msg"] == "gated_merge failed"
    assert fake_mod.failed["result"]["success"] is False
    assert ("gated_merge", "feature/x", "main", ["make", "gate"], "ff") in git.calls
