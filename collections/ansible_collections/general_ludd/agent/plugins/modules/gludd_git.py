#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_git
  short_description: Run hardened git control-plane operations through Gludd
  description:
    - Sends a typed, authenticated request to the daemon-owned GitAutomation service.
    - Collection Python never imports Gludd core or launches git itself.
    - Mutations remain check-mode safe and preserve the historical result schema.
  options:
    path:
      type: str
      required: true
    op:
      type: str
      required: true
      choices: [init, clone, commit, gated_commit, current_branch, branch,
        branch_list, branch_delete, worktree_list, worktree_create,
        worktree_remove, merge, gated_merge, push, verify_remote, tag_release,
        tag_checkpoint, release_tag, checkpoint_tag, state, batch_push,
        release_cut, release_delete, release_recut, ci_verdict, ci_cancel]
    daemon_url:
      type: str
      default: http://localhost:8000
    psk:
      type: str
      no_log: true
      default: ""

EXAMPLES:
  - name: Create a branch through the daemon control plane
    general_ludd.agent.gludd_git:
      path: /workspace/repo
      op: branch
      branch: feature/example
      psk: "{{ vault_gludd_psk }}"

RETURN:
  result:
    description: Typed daemon result for the selected operation.
    type: dict
    returned: always
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)

_READ_ONLY_OPS = frozenset(
    {"current_branch", "branch_list", "worktree_list", "verify_remote", "state", "ci_verdict"}
)


def _argument_spec() -> dict[str, dict[str, Any]]:
    return {
        "path": {"type": "str", "required": True},
        "op": {
            "type": "str",
            "required": True,
            "choices": [
                "init", "clone", "commit", "gated_commit", "current_branch",
                "branch", "branch_list", "branch_delete", "worktree_list",
                "worktree_create", "worktree_remove", "merge", "gated_merge",
                "push", "verify_remote", "tag_release", "tag_checkpoint",
                "release_tag", "checkpoint_tag", "state", "batch_push",
                "release_cut", "release_delete", "release_recut", "ci_verdict",
                "ci_cancel",
            ],
        },
        "clone_url": {"type": "str", "default": None},
        "target_dir": {"type": "str", "default": None},
        "git_clone_timeout": {"type": "int", "default": 120},
        "clone_allow_local": {"type": "bool", "default": True},
        "message": {"type": "str", "default": None},
        "files": {"type": "list", "elements": "str", "default": []},
        "gate_cmd": {"type": "list", "elements": "str", "default": []},
        "branch": {"type": "str", "default": None},
        "worktree_path": {"type": "str", "default": None},
        "source": {"type": "str", "default": None},
        "target": {"type": "str", "default": None},
        "strategy": {"type": "str", "default": "ff", "choices": ["ff", "no-ff", "squash"]},
        "tag": {"type": "str", "default": None},
        "todo_id": {"type": "str", "default": None},
        "sha": {"type": "str", "default": None},
        "expected_sha": {"type": "str", "default": None},
        "ssh_key_path": {"type": "str", "default": None},
        "ref_type": {"type": "str", "default": "heads", "choices": ["heads", "tags"]},
        "threshold": {"type": "int", "default": 5},
        "force": {"type": "bool", "default": False},
        "check_ci": {"type": "bool", "default": True},
        "release_tag": {"type": "str", "default": None},
        "release_message": {"type": "str", "default": ""},
        "release_remote": {"type": "str", "default": "sandboxcom"},
        "release_repo": {"type": "str", "default": "sandboxcom/gludd"},
        "skip_readme_check": {"type": "bool", "default": False},
        "skip_ci_check": {"type": "bool", "default": False},
        "run_id": {"type": "str", "default": None},
        "remote": {"type": "str", "default": "origin"},
        "state_ref": {"type": "str", "default": ""},
        "state_gha_head_sha": {"type": "str", "default": ""},
        "state_worktree_target_ref": {"type": "str", "default": "HEAD"},
        "state_preserve_branch_patterns": {"type": "list", "elements": "str", "default": []},
        "state_reconciled_preserve_heads": {"type": "list", "elements": "str", "default": []},
        "state_reconciled_preserve_head_file": {"type": "str", "default": "config/reconciled_preserved_heads.txt"},
        "state_assert_clean": {"type": "bool", "default": False},
        "state_assert_no_feature_on_master": {"type": "bool", "default": False},
        "state_assert_merge_ready": {"type": "bool", "default": False},
        "state_assert_remote_head": {"type": "bool", "default": False},
        "state_assert_gha_matches_local": {"type": "bool", "default": False},
        "state_assert_no_unintegrated_worktrees": {"type": "bool", "default": False},
        "state_assert_no_unintegrated_branches": {"type": "bool", "default": False},
        "daemon_url": {"type": "str", "default": "http://localhost:8000"},
        "psk": {"type": "str", "default": "", "no_log": True},
        "timeout": {"type": "int", "default": 300},
        "idempotency_key": {"type": "str", "default": None},
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec=_argument_spec(),
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
            ("op", "release_cut", ["release_tag"]),
            ("op", "release_delete", ["release_tag"]),
            ("op", "release_recut", ["release_tag"]),
            ("op", "ci_cancel", ["run_id"]),
        ],
        supports_check_mode=True,
    )
    op = str(module.params["op"])
    if op in {"gated_commit", "gated_merge"} and not module.params["gate_cmd"]:
        module.fail_json(**error_result(f"{op} requires non-empty gate_cmd"))
        return
    if module.check_mode and op not in _READ_ONLY_OPS:
        module.exit_json(
            **ok_result(
                {"result": {"would_change": True, "op": op, "path": module.params["path"]}},
                changed=True,
            )
        )
        return

    excluded = {"daemon_url", "psk", "timeout", "idempotency_key"}
    body = {
        key: value
        for key, value in module.params.items()
        if key not in excluded and value is not None
    }
    body["idempotency_key"] = module.params["idempotency_key"]
    response = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    ).post("/admin/git/operation", body)
    if response.get("_error") or response.get("_status") != 200:
        module.fail_json(
            **error_result(
                str(response.get("detail") or response.get("_error") or f"{op} failed"),
                status=response.get("_status", 0),
            )
        )
        return
    result = response.get("result")
    payload: dict[str, Any] = {"result": result}
    if isinstance(result, dict):
        for key in ("sha", "branch", "tag", "message"):
            if key in result:
                payload[key] = result[key]
    module.exit_json(
        **ok_result(payload, changed=bool(response.get("changed", op not in _READ_ONLY_OPS)))
    )


if __name__ == "__main__":
    main()
