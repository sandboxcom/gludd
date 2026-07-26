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
      choices:
        - clone
        - commit
        - gated_commit
        - current_branch
        - branch
        - branch_list
        - branch_delete
        - worktree_list
        - worktree_create
        - worktree_remove
        - merge
        - gated_merge
        - push
        - verify_remote
        - tag_release
        - tag_checkpoint
        - release_tag
        - checkpoint_tag
        - state
        - batch_push
    clone_url:
      description: Repository URL for C(op=clone). Python preflight rejects unsafe transports before clone.
      type: str
    target_dir:
      description: Destination checkout path for C(op=clone).
      type: str
    git_clone_timeout:
      description: Clone timeout in seconds for C(op=clone).
      type: int
      default: 120
    clone_allow_local:
      description: Whether C(op=clone) allows C(file://) or local source URLs.
      type: bool
      default: true
    message:
      description: Commit message (required when C(op=commit) or C(op=gated_commit)).
      type: str
    branch:
      description:
        - Branch name. Required for C(op=branch), C(op=branch_delete),
          C(op=worktree_create), and C(op=push) (the refspec to push).
      type: str
    worktree_path:
      description: Worktree path (required for C(worktree_create)/C(worktree_remove)).
      type: str
    source:
      description: Source ref to merge from (required for C(op=merge) or C(op=gated_merge)).
      type: str
    target:
      description: Target ref to merge into / check out first (required for C(op=merge) or C(op=gated_merge)).
      type: str
    strategy:
      description: Merge strategy for C(op=merge) and C(op=gated_merge).
      type: str
      default: "ff"
      choices: [ff, no-ff, squash]
    files:
      description: Paths to stage before C(op=gated_commit). Use ["."] for all tracked and untracked workspace changes.
      type: list
      elements: str
      default: []
    gate_cmd:
      description: Command argv to run before C(op=gated_commit) commits, or after C(op=gated_merge) merges.
      type: list
      elements: str
      default: []
    tag:
      description: Tag name for C(op=tag_release) or C(op=tag_checkpoint).
      type: str
    todo_id:
      description: Todo identifier for generated C(op=checkpoint_tag).
      type: str
    sha:
      description: Commit SHA used in generated C(op=checkpoint_tag).
      type: str

    remote:
      description: Remote name for C(op=push) and C(op=state).
      type: str
      default: "origin"
    state_reconciled_preserve_heads:
      description:
        - Preserved branch HEAD SHAs already audited and reconciled.
        - Matching heads do not block C(state_assert_no_unintegrated_branches).
      type: list
      elements: str
      default: []
    state_reconciled_preserve_head_file:
      description:
        - Repo-relative file listing audited preserved branch HEAD SHAs.
      type: str
      default: "config/reconciled_preserved_heads.txt"

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
    description: Branch name (op=branch/current_branch).
    type: str
    returned: when op=branch/current_branch
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
                    "clone",
                    "commit",
                    "gated_commit",
                    "current_branch",
                    "branch",
                    "branch_list",
                    "branch_delete",
                    "worktree_list",
                    "worktree_create",
                    "worktree_remove",
                    "merge",
                    "gated_merge",
                    "push",
                    "verify_remote",
                    "tag_release",
                    "tag_checkpoint",
                    "release_tag",
                    "checkpoint_tag",
            "state",
                    "batch_push",
                ],
            ),
            clone_url=dict(type="str", default=None),
            target_dir=dict(type="str", default=None),
            git_clone_timeout=dict(type="int", default=120),
            clone_allow_local=dict(type="bool", default=True),
            message=dict(type="str", default=None),
            files=dict(type="list", elements="str", default=[]),
            gate_cmd=dict(type="list", elements="str", default=[]),
            branch=dict(type="str", default=None),
            worktree_path=dict(type="str", default=None),
            source=dict(type="str", default=None),
            target=dict(type="str", default=None),
            strategy=dict(type="str", default="ff", choices=["ff", "no-ff", "squash"]),
            tag=dict(type="str", default=None),
            todo_id=dict(type="str", default=None),
            sha=dict(type="str", default=None),
            expected_sha=dict(type="str", default=None),
            ssh_key_path=dict(type="str", default=None),
            ref_type=dict(type="str", default="heads", choices=["heads", "tags"]),
            threshold=dict(type="int", default=5),
            force=dict(type="bool", default=False),
            check_ci=dict(type="bool", default=True),

            remote=dict(type="str", default="origin"),
            state_ref=dict(type="str", default=""),
            state_gha_head_sha=dict(type="str", default=""),
            state_worktree_target_ref=dict(type="str", default="HEAD"),
            state_preserve_branch_patterns=dict(type="list", elements="str", default=[]),
            state_reconciled_preserve_heads=dict(type="list", elements="str", default=[]),
            state_reconciled_preserve_head_file=dict(type="str", default="config/reconciled_preserved_heads.txt"),
            state_assert_clean=dict(type="bool", default=False),
            state_assert_no_feature_on_master=dict(type="bool", default=False),
            state_assert_merge_ready=dict(type="bool", default=False),
            state_assert_remote_head=dict(type="bool", default=False),
            state_assert_gha_matches_local=dict(type="bool", default=False),
            state_assert_no_unintegrated_worktrees=dict(type="bool", default=False),
            state_assert_no_unintegrated_branches=dict(type="bool", default=False),
        ),
        required_if=[
            ("op", "clone", ["clone_url", "target_dir"]),
            ("op", "commit", ["message"]),
            ("op", "gated_commit", ["message"]),
            ("op", "branch", ["branch"]),
            ("op", "branch_delete", ["branch"]),
            ("op", "worktree_create", ["branch", "worktree_path"]),
            ("op", "worktree_remove", ["worktree_path"]),
            ("op", "merge", ["source", "target"]),
            ("op", "gated_merge", ["source", "target"]),
            ("op", "push", ["branch"]),
            ("op", "verify_remote", ["branch", "expected_sha"]),
            ("op", "tag_release", ["tag"]),
            ("op", "tag_checkpoint", ["tag"]),
            ("op", "checkpoint_tag", ["todo_id", "sha"]),
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

    if op in {"gated_commit", "gated_merge"} and not module.params["gate_cmd"]:
        module.fail_json(**error_result(f"{op} requires non-empty gate_cmd"))
        return

    if op == "current_branch":
        module.exit_json(**ok_result({"branch": git.current_branch()}, changed=False))
        return

    if op == "branch_list":
        try:
            branches = git.list_branches()
        except subprocess.CalledProcessError as exc:
            module.fail_json(**error_result(f"branch_list failed: {exc.stderr or exc}"))
            return
        module.exit_json(**ok_result({"result": {"branches": branches}}, changed=False))
        return

    if op == "state":
        try:
            state_result = git.workflow_state(
                remote=module.params["remote"],
                ref=module.params["state_ref"],
                gha_head_sha=module.params["state_gha_head_sha"],
                worktree_target_ref=module.params["state_worktree_target_ref"],
                preserve_branch_patterns=tuple(module.params["state_preserve_branch_patterns"] or []),
                reconciled_preserve_heads=tuple(module.params["state_reconciled_preserve_heads"] or []),
                reconciled_preserve_head_file=module.params["state_reconciled_preserve_head_file"],
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

    if op == "batch_push":
        if module.check_mode:
            module.exit_json(
                **ok_result(
                    {"result": {"would_change": True, "op": op, "path": path}},
                    changed=True,
                )
            )
            return
        from general_ludd.git_automation.batch_push import batch_push

        result = batch_push(
            path,
            remote=module.params["remote"],
            branch=module.params["branch"] or "master",
            threshold=module.params["threshold"],
            force=module.params["force"],
            check_ci=module.params["check_ci"],
        )
        payload = asdict(result)
        module.exit_json(**ok_result({"result": payload}, changed=bool(result.pushed)))
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
        if op == "clone":
            res = git.clone(
                module.params["clone_url"],
                module.params["target_dir"],
                timeout=float(module.params["git_clone_timeout"]),
                allow_local=bool(module.params["clone_allow_local"]),
            )
            payload = asdict(res)
            if not res.success:
                module.fail_json(**error_result("clone failed", result=payload))
                return
            module.exit_json(**ok_result({"result": payload}, changed=not res.already_present))
            return

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

        if op == "branch_delete":
            deleted = git.delete_branch(module.params["branch"])
            module.exit_json(
                **ok_result(
                    {"result": {"branch": module.params["branch"], "deleted": deleted}},
                    changed=deleted,
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

        if op == "gated_commit":
            res = git.gated_commit(
                list(module.params["files"] or []),
                module.params["message"],
                list(module.params["gate_cmd"] or []),
            )
            payload = asdict(res)
            if not res.success:
                module.fail_json(**error_result("gated_commit failed", result=payload))
                return
            module.exit_json(**ok_result({"result": payload}, changed=True))
            return

        if op == "gated_merge":
            res = git.gated_merge(
                module.params["source"],
                module.params["target"],
                list(module.params["gate_cmd"] or []),
                strategy=module.params["strategy"],
            )
            payload = asdict(res)
            if not res.success:
                module.fail_json(**error_result("gated_merge failed", result=payload))
                return
            module.exit_json(**ok_result({"result": payload}, changed=True))
            return

        if op == "push":
            res = git.push_to_remote(
                path, remote=module.params["remote"], branch=module.params["branch"]
            )
            module.exit_json(**ok_result({"result": asdict(res)}, changed=res.success))
            return

        if op == "verify_remote":
            res = git.verify_remote(
                remote=module.params["remote"],
                branch=module.params["branch"],
                expected_sha=module.params["expected_sha"],
                ssh_key_path=module.params["ssh_key_path"],
                ref_type=module.params["ref_type"],
            )
            module.exit_json(**ok_result({"result": asdict(res)}, changed=False))
            return

        if op == "tag_release":
            tag = git.tag_release(module.params["tag"])
            module.exit_json(**ok_result({"tag": tag}, changed=True))
            return

        if op == "tag_checkpoint":
            tag = git.tag_checkpoint(module.params["tag"])
            module.exit_json(**ok_result({"tag": tag}, changed=True))
            return

        if op == "release_tag":
            tag = git.create_release_tag(path)
            module.exit_json(**ok_result({"tag": tag}, changed=True))
            return

        if op == "checkpoint_tag":
            tag = git.create_checkpoint_tag(
                path,
                module.params["todo_id"],
                module.params["sha"],
            )
            module.exit_json(**ok_result({"tag": tag}, changed=True))
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
