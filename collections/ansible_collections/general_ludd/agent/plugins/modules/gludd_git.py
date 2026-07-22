#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_git
  short_description: Git control-plane ops (commit/branch/worktree/merge/push) via git_automation
  description:
    - Exposes gludd's hardened C(general_ludd.git_automation.GitAutomation)
      control plane to roles/playbooks so an agent-authored job can perform git
      operations WITHOUT reimplementing the safety logic.
    - This is a thin B(delegating wrapper) — it does not reimplement git. The
      Python core provides per-repo C(.git/index.lock) serialization (issue #63),
      a bounded subprocess timeout, a non-interactive git environment,
      leading-dash ref rejection, C(--) end-of-options separators, worktree-path
      traversal guards, and typed results the daemon also consumes synchronously.
      Keeping that core in Python (rather than a pure role) preserves those
      guarantees; this module simply makes the same operations available on the
      Ansible execution seam.
    - Idempotent where git is: C(branch) is a no-op if the branch already exists;
      C(commit) reports C(changed=false) when there is nothing to commit.
    - Check-mode safe — read-only C(worktree_list) runs; mutating ops are skipped
      in check mode and report the change they WOULD make.
  options:
    path:
      description: Path to the git repository or worktree.
      type: str
      required: true
    op:
      description:
        - Operation to perform. C(worktree_list) is read-only; the rest mutate.
      type: str
      required: true
choices: [commit, branch, worktree_list, worktree_create, worktree_remove, merge, push, state]
    message:
      description: Commit message (required when C(op=commit)).
      type: str
    branch:
      description:
        - Branch name. Required for C(op=branch), C(op=worktree_create), and
          C(op=push) (the refspec to push).
      type: str
    worktree_path:
      description: Worktree path (required for C(worktree_create)/C(worktree_remove)).
      type: str
    source:
      description: Source ref to merge from (required for C(op=merge)).
      type: str
    target:
      description: Target ref to merge into / check out first (required for C(op=merge)).
      type: str
    strategy:
      description: Merge strategy for C(op=merge).
      type: str
      default: "ff"
      choices: [ff, no-ff, squash]
    remote:
      description: Remote name for C(op=push).
      type: str
      default: "origin"

EXAMPLES:
  - name: Commit changes in worktree
    general_ludd.agent.gludd_git:
      path: "/tmp/worktrees/fix-auto-20260612"
      op: commit
      message: "auto: apply model-suggested fix"
    register: git_result

  - name: Create branch
    general_ludd.agent.gludd_git:
      path: "/workspace/myrepo"
      op: branch
      branch: "fix/auto-20260612"

  - name: List worktrees
    general_ludd.agent.gludd_git:
      path: "/workspace/myrepo"
      op: worktree_list
    register: wts

  - name: Add an agent worktree on a new branch
    general_ludd.agent.gludd_git:
      path: "/workspace/myrepo"
      op: worktree_create
      branch: "agent/TODO-1234/feature"
      worktree_path: "/workspace/myrepo-wt-1234"

  - name: Fast-forward merge a green branch
    general_ludd.agent.gludd_git:
      path: "/workspace/myrepo"
      op: merge
      source: "agent/TODO-1234/feature"
      target: "main"
      strategy: ff

  - name: Push a branch to origin (bounded by the control-plane timeout)
    general_ludd.agent.gludd_git:
      path: "/workspace/myrepo"
      op: push
      branch: "main"

RETURN:
  sha:
    description: Commit SHA (op=commit only).
    type: str
    returned: when op=commit and changed=true
  branch:
    description: Branch name (op=branch).
    type: str
    returned: when op=branch
  result:
    description: Typed result dict for worktree/merge/push ops.
    type: dict
    returned: when op in (worktree_list, worktree_create, worktree_remove, merge, push)
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        error_result,
        ok_result,
    )
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import error_result, ok_result  # type: ignore[import]


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type="str", required=True),
            op=dict(
                type="str",
                required=True,
choices=[
                    "commit",
                    "branch",
                    "worktree_list",
                    "worktree_create",
                    "worktree_remove",
                    "merge",
                    "push",
                    "state",
                ],
            ),
            message=dict(type="str", default=None),
            branch=dict(type="str", default=None),
            worktree_path=dict(type="str", default=None),
            source=dict(type="str", default=None),
            target=dict(type="str", default=None),
            strategy=dict(type="str", default="ff", choices=["ff", "no-ff", "squash"]),

            remote=dict(type="str", default="origin"),
            state_ref=dict(type="str", default=""),
            state_gha_head_sha=dict(type="str", default=""),
            state_worktree_target_ref=dict(type="str", default="HEAD"),
            state_preserve_branch_patterns=dict(type="list", elements="str", default=[]),
            state_assert_clean=dict(type="bool", default=False),
            state_assert_no_feature_on_master=dict(type="bool", default=False),
            state_assert_merge_ready=dict(type="bool", default=False),
            state_assert_remote_head=dict(type="bool", default=False),
            state_assert_gha_matches_local=dict(type="bool", default=False),
            state_assert_no_unintegrated_worktrees=dict(type="bool", default=False),
            state_assert_no_unintegrated_branches=dict(type="bool", default=False),
        ),
        required_if=[
            ("op", "commit", ["message"]),
            ("op", "branch", ["branch"]),
            ("op", "worktree_create", ["branch", "worktree_path"]),
            ("op", "worktree_remove", ["worktree_path"]),
            ("op", "merge", ["source", "target"]),
            ("op", "push", ["branch"]),
        ],
        supports_check_mode=True,
    )

    path: str = module.params["path"]
    op: str = module.params["op"]


    try:
        from general_ludd.git_automation.repo import GitAutomation  # type: ignore[import]
    except ImportError as exc:
        module.fail_json(**error_result(f"general_ludd not importable: {exc}"))
        return

    git = GitAutomation(repo_path=path)


    if op == "state":
        try:
            state_result = git.workflow_state(
                remote=module.params["remote"],
                ref=module.params["state_ref"],
                gha_head_sha=module.params["state_gha_head_sha"],
                worktree_target_ref=module.params["state_worktree_target_ref"],
                preserve_branch_patterns=tuple(module.params["state_preserve_branch_patterns"] or []),
                assert_clean=module.params["state_assert_clean"],
                assert_no_feature_on_master=module.params["state_assert_no_feature_on_master"],
                assert_merge_ready=module.params["state_assert_merge_ready"],
                assert_remote_head=module.params["state_assert_remote_head"],
                assert_gha_matches_local=module.params["state_assert_gha_matches_local"],
                assert_no_unintegrated_worktrees=module.params["state_assert_no_unintegrated_worktrees"],
                assert_no_unintegrated_branches=module.params["state_assert_no_unintegrated_branches"],
            )
        except subprocess.CalledProcessError as exc:
            module.fail_json(**error_result(f"git state failed: {exc.stderr or exc}"))
            return
        payload = asdict(state_result)
        if not state_result.success:
            module.fail_json(**error_result("git state guard failed", result=payload))
            return
        module.exit_json(**ok_result({"result": payload}, changed=False))
        return
    if op == "commit":
        message: str = module.params["message"]
        # Route the dirty-check through the library (under git_repo_lock) rather
        # than shelling out to `git status` directly — a bare subprocess here
        # would bypass the per-repo lock and race a concurrent role on the same
        # worktree. changed_files() is fail-safe ([] on any error).
        has_changes = bool(git.changed_files())
        if not has_changes:
            module.exit_json(**ok_result({"sha": None, "message": "nothing to commit"}, changed=False))
            return
        if module.check_mode:
            module.exit_json(**ok_result({"sha": "[check-mode]", "message": message}, changed=True))
            return
        try:
            sha = git.commit(message)
        except subprocess.CalledProcessError as exc:
            module.fail_json(**error_result(f"git commit failed: {exc.stderr}"))
            return
        module.exit_json(**ok_result({"sha": sha, "message": message}, changed=True))
        return

    if op == "branch":
        branch_name: str = module.params["branch"]
        if module.check_mode:
            module.exit_json(**ok_result({"branch": branch_name}, changed=True))
            return
        try:
            created = git.create_branch(branch_name)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            if "already exists" in stderr:
                module.exit_json(**ok_result({"branch": branch_name}, changed=False))
                return
            module.fail_json(**error_result(f"git branch failed: {stderr}"))
            return
        module.exit_json(**ok_result({"branch": created}, changed=True))
        return

    # --- read-only worktree listing: runs in check mode ---------------------
    if op == "worktree_list":
        try:
            worktrees = git.list_worktrees(path)
        except Exception as exc:
            module.fail_json(**error_result(f"worktree_list failed: {exc}"))
            return
        module.exit_json(
            **ok_result(
                {"result": {"worktrees": [asdict(w) for w in worktrees]}}, changed=False
            )
        )
        return

    # --- mutating worktree/merge/push ops: check-mode safe ------------------
    if module.check_mode:
        module.exit_json(
            **ok_result(
                {"result": {"would_change": True, "op": op, "path": path}}, changed=True
            )
        )
        return

    try:
        if op == "worktree_create":
            res = git.create_worktree(
                path, module.params["branch"], module.params["worktree_path"]
            )
            module.exit_json(**ok_result({"result": asdict(res)}, changed=res.success))
            return

        if op == "worktree_remove":
            removed = git.remove_worktree(path, module.params["worktree_path"])
            module.exit_json(
                **ok_result(
                    {"result": {"removed": removed, "path": module.params["worktree_path"]}},
                    changed=removed,
                )
            )
            return

        if op == "merge":
            res = git.merge_branch(
                path,
                module.params["source"],
                module.params["target"],
                strategy=module.params["strategy"],
            )
            module.exit_json(**ok_result({"result": asdict(res)}, changed=res.success))
            return

        if op == "push":
            res = git.push_to_remote(
                path, remote=module.params["remote"], branch=module.params["branch"]
            )
            module.exit_json(**ok_result({"result": asdict(res)}, changed=res.success))
            return
    except ValueError as exc:
        # Control-plane security guards (leading-dash refs, traversal paths)
        # raise ValueError — surface as a clean rejection, not a stack trace.
        module.fail_json(**error_result(f"{op} rejected: {exc}"))
        return
    except subprocess.CalledProcessError as exc:
        module.fail_json(**error_result(f"{op} failed: {exc.stderr or exc}"))
        return
    except Exception as exc:
        module.fail_json(**error_result(f"{op} failed: {exc}"))
        return

    module.fail_json(**error_result(f"unhandled op: {op!r}"))


if __name__ == "__main__":
    main()
