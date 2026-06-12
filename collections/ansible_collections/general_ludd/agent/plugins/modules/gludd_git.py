#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_git
  short_description: Git operations (commit/branch) via git_automation
  description:
    - Performs git commit or branch operations on a local repository.
    - Wraps C(general_ludd.git_automation.repo.GitAutomation).
    - Idempotent for C(branch) (no-op if branch already exists).
    - C(commit) reports C(changed=true) when a commit is created; C(changed=false)
      if there is nothing to commit.
  options:
    path:
      description: Path to the git repository or worktree.
      type: str
      required: true
    op:
      description: Operation to perform.
      type: str
      required: true
      choices: [commit, branch]
    message:
      description: Commit message (required when C(op=commit)).
      type: str
    branch:
      description: Branch name (required when C(op=branch)).
      type: str

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

RETURN:
  sha:
    description: Commit SHA (op=commit only).
    type: str
    returned: when op=commit and changed=true
  branch:
    description: Branch name (op=branch).
    type: str
    returned: when op=branch
"""

from __future__ import annotations

import os
import subprocess

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


def _has_staged_or_unstaged(repo_path: str) -> bool:
    """Return True if there is anything to commit."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type="str", required=True),
            op=dict(type="str", required=True, choices=["commit", "branch"]),
            message=dict(type="str", default=None),
            branch=dict(type="str", default=None),
        ),
        required_if=[
            ("op", "commit", ["message"]),
            ("op", "branch", ["branch"]),
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

    if op == "commit":
        message: str = module.params["message"]
        has_changes = _has_staged_or_unstaged(path)
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

    elif op == "branch":
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


if __name__ == "__main__":
    main()
