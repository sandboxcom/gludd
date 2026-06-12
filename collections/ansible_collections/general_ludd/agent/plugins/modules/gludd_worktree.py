#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_worktree
  short_description: Manage git worktrees (idempotent)
  description:
    - Creates or removes a git worktree via git_automation.repo.GitAutomation.
    - Idempotent — C(changed=false) when the desired state already exists.
    - In check mode reports what would change without modifying the filesystem.
  options:
    repo_path:
      description: Path to the git repository root.
      type: str
      required: true
    branch:
      description: Branch name for the new worktree.
      type: str
      required: true
    worktree_path:
      description: Filesystem path for the new worktree.
      type: str
      required: true
    state:
      description: C(present) to create, C(absent) to remove.
      type: str
      choices: [present, absent]
      default: present

EXAMPLES:
  - name: Create a worktree
    general_ludd.agent.gludd_worktree:
      repo_path: "/workspace/myrepo"
      branch: "fix/auto-20260612"
      worktree_path: "/tmp/worktrees/fix-auto-20260612"
    register: wt

  - name: Remove worktree on cleanup
    general_ludd.agent.gludd_worktree:
      repo_path: "/workspace/myrepo"
      worktree_path: "/tmp/worktrees/fix-auto-20260612"
      branch: "fix/auto-20260612"
      state: absent

RETURN:
  worktree_path:
    description: Path of the worktree.
    type: str
    returned: always
  branch:
    description: Branch name.
    type: str
    returned: always
  state:
    description: Resulting state (present or absent).
    type: str
    returned: always
"""

from __future__ import annotations

import os

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
            repo_path=dict(type="str", required=True),
            branch=dict(type="str", required=True),
            worktree_path=dict(type="str", required=True),
            state=dict(type="str", default="present", choices=["present", "absent"]),
        ),
        supports_check_mode=True,
    )

    repo_path: str = module.params["repo_path"]
    branch: str = module.params["branch"]
    worktree_path: str = module.params["worktree_path"]
    state: str = module.params["state"]

    try:
        from general_ludd.git_automation.repo import GitAutomation  # type: ignore[import]
    except ImportError as exc:
        module.fail_json(**error_result(f"general_ludd not importable: {exc}"))
        return

    git = GitAutomation(repo_path=repo_path)
    existing = os.path.isdir(worktree_path)

    if state == "present":
        if existing:
            module.exit_json(**ok_result(
                {"worktree_path": worktree_path, "branch": branch, "state": "present"},
                changed=False,
            ))
            return
        if module.check_mode:
            module.exit_json(**ok_result(
                {"worktree_path": worktree_path, "branch": branch, "state": "present"},
                changed=True,
            ))
            return
        result = git.create_worktree(repo_path, branch, worktree_path)
        if not result.success:
            module.fail_json(**error_result(
                f"Failed to create worktree: {result.message}",
                worktree_path=worktree_path,
                branch=branch,
            ))
            return
        module.exit_json(**ok_result(
            {"worktree_path": worktree_path, "branch": branch, "state": "present"},
            changed=True,
        ))

    else:  # absent
        if not existing:
            module.exit_json(**ok_result(
                {"worktree_path": worktree_path, "branch": branch, "state": "absent"},
                changed=False,
            ))
            return
        if module.check_mode:
            module.exit_json(**ok_result(
                {"worktree_path": worktree_path, "branch": branch, "state": "absent"},
                changed=True,
            ))
            return
        removed = git.remove_worktree(repo_path, worktree_path)
        if not removed:
            module.fail_json(**error_result(
                "Failed to remove worktree",
                worktree_path=worktree_path,
            ))
            return
        module.exit_json(**ok_result(
            {"worktree_path": worktree_path, "branch": branch, "state": "absent"},
            changed=True,
        ))


if __name__ == "__main__":
    main()
