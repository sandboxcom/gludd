#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_open_code
  short_description: Batched opencode agent tool patterns — gate, push, commit, test, status
  description:
    - Codifies the repeated back-and-forth tool-call patterns opencode agents
      perform by bundling multiple tool calls into single Ansible tasks.
    - Each action maps to a make target that composites the underlying checks.
    - Check-mode safe — all actions report the change they WOULD make without
      executing the underlying command.
    - All actions run locally via C(ansible.builtin.command) executing make
      targets in the repository root; no daemon round-trips are needed.
  options:
    action:
      description:
        - The bundled operation to perform.
        - C(gate_check) — lint + typecheck + collect in one call (make gate-fast).
        - C(push_and_verify) — push master + verify remote SHA + poll CI (make ci-push-and-verify).
        - C(commit_ship) — add + commit + push + verify in one call (make ship).
        - C(test_batch) — run multiple test files in one invocation (make test-batch).
        - C(status_check) — regenerate status table + verify it is current (make status-update).
      type: str
      required: true
      choices: [gate_check, push_and_verify, commit_ship, test_batch, status_check]
    repo_path:
      description: Path to the git repository root where make targets are defined.
      type: str
      default: "."
    commit_msg:
      description: Commit message (required when action=commit_ship).
      type: str
    test_files:
      description:
        - Space-separated list of test file paths (required when action=test_batch).
        - Passed as the C(TESTFILES) variable to C(make test-batch).
      type: list
      elements: str
    branch:
      description: Branch name for push_and_verify or commit_ship (defaults to master).
      type: str
      default: "master"
    wait_for_ci:
      description: When true, push_and_verify polls CI after push until completion or timeout.
      type: bool
      default: true
    ci_timeout:
      description: Maximum seconds to wait for CI when wait_for_ci=true.
      type: int
      default: 600

EXAMPLES:
  - name: Run fast gate check (lint + typecheck + collect)
    general_ludd.agent.gludd_open_code:
      action: gate_check
      repo_path: "/workspace/gludd"
    register: gate_result

  - name: Commit all changes and ship to remote
    general_ludd.agent.gludd_open_code:
      action: commit_ship
      repo_path: "/workspace/gludd"
      commit_msg: "feat: add batched opencode patterns"
    register: ship_result

  - name: Push master and poll CI until green
    general_ludd.agent.gludd_open_code:
      action: push_and_verify
      repo_path: "/workspace/gludd"
      branch: master
      wait_for_ci: true
      ci_timeout: 600
    register: push_result

  - name: Run a batch of test files
    general_ludd.agent.gludd_open_code:
      action: test_batch
      repo_path: "/workspace/gludd"
      test_files:
        - tests/unit/test_module_a.py
        - tests/unit/test_module_b.py
    register: test_result

  - name: Regenerate and verify README status table
    general_ludd.agent.gludd_open_code:
      action: status_check
      repo_path: "/workspace/gludd"
    register: status_result

RETURN:
  changed:
    description: Whether the action changed system state.
    returned: always
    type: bool
    sample: true
  action:
    description: The action that was performed.
    returned: always
    type: str
    sample: "gate_check"
  cmd:
    description: The full command that was executed (or would be in check mode).
    returned: always
    type: str
    sample: "make gate-fast"
  rc:
    description: Return code of the command (None in check mode).
    returned: always
    type: int
    sample: 0
  stdout:
    description: Standard output of the command (truncated to last 2000 chars).
    returned: always
    type: str
  stderr:
    description: Standard error of the command.
    returned: always
    type: str
  elapsed_seconds:
    description: Wall-clock duration of the command execution.
    returned: when command ran
    type: float
    sample: 12.34
  skipped:
    description: True when run in check mode (no command executed).
    returned: always
    type: bool
    sample: false
"""

from __future__ import annotations

import time
from typing import Any

from ansible.module_utils.basic import AnsibleModule


ACTION_MAKE_TARGETS: dict[str, str] = {
    "gate_check": "gate-fast",
    "push_and_verify": "ci-push-and-verify",
    "commit_ship": "ship",
    "test_batch": "test-batch",
    "status_check": "status-update",
}


def _build_cmd(action: str, params: dict) -> str:
    target = ACTION_MAKE_TARGETS.get(action, "")
    repo_path = params.get("repo_path", ".")
    cmd_parts = ["make", "-C", repo_path, target]

    if action == "commit_ship":
        msg = (params.get("commit_msg") or "").strip()
        if msg:
            cmd_parts.append('MSG="' + msg + '"')

    elif action == "test_batch":
        test_files = params.get("test_files") or []
        if isinstance(test_files, list):
            joined = " ".join(test_files)
        else:
            joined = str(test_files)
        if joined.strip():
            cmd_parts.append('TESTFILES="' + joined + '"')

    return " ".join(cmd_parts)


def run_module() -> dict[str, Any]:
    module_args: dict[str, Any] = dict(
        action=dict(
            type="str",
            required=True,
            choices=list(ACTION_MAKE_TARGETS.keys()),
        ),
        repo_path=dict(type="str", default="."),
        commit_msg=dict(type="str", default=""),
        test_files=dict(type="list", elements="str", default=[]),
        branch=dict(type="str", default="master"),
        wait_for_ci=dict(type="bool", default=True),
        ci_timeout=dict(type="int", default=600),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True,
    )

    action: str = module.params.get("action", "")
    repo_path: str = module.params.get("repo_path", ".")
    commit_msg: str = module.params.get("commit_msg", "")
    test_files: list[str] = module.params.get("test_files", [])
    branch: str = module.params.get("branch", "master")
    wait_for_ci: bool = module.params.get("wait_for_ci", True)
    ci_timeout: int = module.params.get("ci_timeout", 600)

    cmd = _build_cmd(action, module.params)

    result: dict[str, Any] = {
        "changed": False,
        "action": action,
        "cmd": cmd,
        "rc": None,
        "stdout": "",
        "stderr": "",
        "skipped": module.check_mode,
    }

    if action == "commit_ship" and not commit_msg:
        module.fail_json(msg="commit_msg is required when action=commit_ship", **result)

    if action == "test_batch" and not test_files:
        module.fail_json(msg="test_files is required when action=test_batch", **result)

    if module.check_mode:
        result["changed"] = False
        result["stdout"] = "[check_mode] would run: " + cmd
        module.exit_json(**result)

    t0 = time.monotonic()
    rc, stdout, stderr = module.run_command(
        cmd,
        environ_update={
            "BRANCH": branch,
            "CI_POLL_SECS": str(max(15, min(ci_timeout // 20, 60))),
        },
    )
    elapsed = time.monotonic() - t0

    result["changed"] = True
    result["rc"] = rc
    result["stdout"] = (stdout or "")[-2000:]
    result["stderr"] = stderr or ""
    result["elapsed_seconds"] = round(elapsed, 2)
    result["skipped"] = False

    if rc != 0:
        module.fail_json(msg=f"make {ACTION_MAKE_TARGETS.get(action, action)} failed (rc={rc})", **result)

    module.exit_json(**result)


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
