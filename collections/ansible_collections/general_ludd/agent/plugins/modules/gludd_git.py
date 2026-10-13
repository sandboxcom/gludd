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
        release_cut, release_delete, release_recut, ci_verdict, ci_cancel,
        feature_start, feature_done, rebranch_onto, ship_commit, worktree_merge,
        worktree_cleanup, worktree_health_check, worktree_merge_all]
    daemon_url:
      type: str
      default: http://localhost:8000
    psk:
      type: str
      no_log: true
      default: ""
    timeout:
      description:
        - HTTP timeout in seconds; ordinary and read-only operations default to 300.
        - feature_done, worktree_merge and worktree_merge_all default to 3900,
          covering the canonical 3600-second gate plus a 300-second cleanup and Git operation reserve.
        - An explicitly supplied composite merge timeout below 3900 is rejected before HTTP dispatch.
        - Adequate explicit values are preserved; transport failures and cancellation are not retried.
      type: int
    feature_name:
      description: Short feature name or full branch ref for feature_start and feature_done.
      type: str
    base:
      description: Exact base ref for feature_start or worktree_create; defaults to master or HEAD respectively.
      type: str
    new_base:
      description: Base commit ref for the existing rollback-safe rebranch_onto owner.
      type: str
    commits:
      description: Ordered commit refs to cherry-pick onto new_base.
      type: list
      elements: str
      default: []
    push:
      description: Explicitly request a push after ship_commit succeeds.
      type: bool
      default: false
    skip_gate:
      description: Compatibility input; true is rejected for ship_commit, which always requires its gate.
      type: bool
      default: false
    max_age_hours:
      description: Worktree health age budget; the daemon requires an integer from 1 through 87600.
      type: int
      default: 24
    remote_name:
      description: Remote used by the existing worktree health owner.
      type: str
      default: sandboxcom
    target_branch:
      description: Integration target used by the existing worktree health owner.
      type: str
      default: development

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
    {"current_branch", "branch_list", "worktree_list", "verify_remote", "state", "ci_verdict",
     "worktree_health_check"}
)
_GATE_RUNNING_OPS = frozenset({"feature_done", "worktree_merge", "worktree_merge_all"})
_DEFAULT_TIMEOUT_SECONDS = 300
# The isolated collection owns its protocol constants; native tests pin the gate
# value to commands.make.DEFAULT_GATE_TIMEOUT_SECONDS without importing core here.
_GATE_TIMEOUT_SECONDS = 3600
_GATE_MIN_TIMEOUT_SECONDS = _GATE_TIMEOUT_SECONDS + _DEFAULT_TIMEOUT_SECONDS


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
                "feature_start", "feature_done", "rebranch_onto", "ship_commit",
                "worktree_merge", "worktree_cleanup", "worktree_health_check", "worktree_merge_all",
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
        "feature_name": {"type": "str", "default": None},
        "base": {"type": "str", "default": None},
        "new_base": {"type": "str", "default": None},
        "commits": {"type": "list", "elements": "str", "default": []},
        "push": {"type": "bool", "default": False},
        "skip_gate": {"type": "bool", "default": False},
        "max_age_hours": {"type": "int", "default": 24},
        "remote_name": {"type": "str", "default": "sandboxcom"},
        "target_branch": {"type": "str", "default": "development"},
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
        "timeout": {"type": "int", "default": None},
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
            ("op", "feature_start", ["feature_name"]),
            ("op", "feature_done", ["feature_name"]),
            ("op", "rebranch_onto", ["new_base"]),
            ("op", "ship_commit", ["message"]),
            ("op", "worktree_merge", ["branch", "target"]),
            ("op", "worktree_cleanup", ["branch"]),
        ],
        supports_check_mode=True,
    )
    op = str(module.params["op"])
    timeout = module.params["timeout"]
    if timeout is None:
        timeout = _GATE_MIN_TIMEOUT_SECONDS if op in _GATE_RUNNING_OPS else _DEFAULT_TIMEOUT_SECONDS
    if op in _GATE_RUNNING_OPS and timeout < _GATE_MIN_TIMEOUT_SECONDS:
        module.fail_json(**error_result(
            f"{op} requires timeout >= {_GATE_MIN_TIMEOUT_SECONDS} seconds "
            "to await the canonical gate and cleanup; insufficient explicit budgets are not extended"
        ))
        return
    if op == "ship_commit" and module.params["skip_gate"]:
        module.fail_json(**error_result("ship_commit does not permit skip_gate"))
        return
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
        timeout=timeout,
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
