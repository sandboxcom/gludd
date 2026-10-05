#!/usr/bin/python
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

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.fs_write_audit import (
    IntegrityViolation,
    WriteAuditLog,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.fs_write_policy import (
    WritePolicyError,
    default_policy,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            repo_path=dict(type="str", required=True),
            branch=dict(type="str", required=True),
            worktree_path=dict(type="str", required=True),
            state=dict(type="str", default="present", choices=["present", "absent"]),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=120),
        ),
        supports_check_mode=True,
    )

    repo_path: str = module.params["repo_path"]
    branch: str = module.params["branch"]
    worktree_path: str = module.params["worktree_path"]
    state: str = module.params["state"]

    # Fail-closed write-policy guard: the worktree_path is a filesystem write
    # target (git plants/removes a working tree there). Enforce that it resolves
    # inside an explicit allowlist — the repo's parent dir (worktrees live beside
    # the repo), the repo itself, and /tmp scratch — BEFORE any git mutation, so
    # a traversal/symlink/outside-root path can never plant or delete a tree in
    # an arbitrary location. Applies to both create and remove.
    repo_parent = os.path.dirname(os.path.abspath(repo_path)) or os.sep
    policy = default_policy(
        workspace=os.path.abspath(repo_path),
        worktree_root=repo_parent,
    )
    try:
        policy.check(worktree_path)
    except WritePolicyError as exc:
        module.fail_json(**error_result(
            f"worktree_path denied by write policy: {exc}",
            worktree_path=worktree_path,
        ))
        return

    # FIM-on-write: compose an audit log over the SAME policy so the worktree
    # state-marker we write is auditable (path/sha256/timestamp manifest) and
    # any out-of-band tampering of that marker between runs is detected. The
    # manifest lives beside the worktree root and is itself policy-checked.
    audit = WriteAuditLog(
        policy=policy,
        manifest_path=os.path.join(repo_parent, ".gludd_worktree_audit.json"),
    )
    marker_path = os.path.join(worktree_path, ".gludd_worktree_state")

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )
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
        result = client.post(
            "/admin/git/operation",
            {"op": "worktree_create", "path": repo_path, "branch": branch, "worktree_path": worktree_path},
        )
        operation = result.get("result")
        if (
            result.get("_error")
            or result.get("_status") != 200
            or not isinstance(operation, dict)
            or not operation.get("success")
        ):
            module.fail_json(
                **error_result(
                    "Failed to create worktree: "
                    f"{result.get('detail') or result.get('_error') or operation}",
                    worktree_path=worktree_path,
                    branch=branch,
                )
            )
            return
        # Record an auditable, FIM-tracked state marker for the new worktree.
        # audited_write fail-closes on policy violation AND on out-of-band
        # tampering of a previously-recorded marker, so a hijacked marker path
        # never silently succeeds.
        try:
            audit.audited_write(
                marker_path,
                f"branch={branch}\nstate=present\n".encode(),
            )
        except (WritePolicyError, IntegrityViolation) as exc:
            module.fail_json(**error_result(
                f"worktree state-marker audit failed: {exc}",
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
        result = client.post(
            "/admin/git/operation",
            {"op": "worktree_remove", "path": repo_path, "worktree_path": worktree_path},
        )
        operation = result.get("result")
        removed = isinstance(operation, dict) and bool(operation.get("removed"))
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
