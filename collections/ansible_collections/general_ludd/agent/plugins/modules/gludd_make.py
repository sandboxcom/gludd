#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_make
  short_description: Run a make target via the MakeRunner abstraction
  description:
    - Runs a C(make) target through the C(MakeRunner) subprocess wrapper with
      proper sanitized environment, bounded output capture, and per-target
      timeout.
    - Supports both blocking and streaming modes.
    - Returns structured result (rc, stdout_tail, stderr_tail, success, phases).
  options:
    target:
      description: The make target to run (e.g. C(test), C(lint), C(gate)).
      type: str
      required: true
    extra_args:
      description: Additional arguments passed as C(make) variable assignments
        (e.g. C([TESTFILE=tests/unit/test_foo.py])).
      type: list
      elements: str
      required: false
      default: []
    cwd:
      description: Working directory for the C(make) invocation.
      type: str
      required: false
    timeout_s:
      description: Per-target timeout in seconds. Defaults to 300s.
      type: int
      required: false
    env_extra:
      description: Extra environment variables to pass into the subprocess.
      type: dict
      required: false
    stream:
      description: Whether to stream output with phase-marker callbacks.
      type: bool
      required: false
      default: false
    no_log:
      description: Prevent logging of this invocation (for sensitive targets).
      type: bool
      required: false
      default: false
  notes:
    - Uses C(MakeRunner) from C(general_ludd.commands.make).
    - Phase markers of the form C(=== PHASE NAME ===) in stdout are extracted
      automatically.
    - The C(no_log) flag suppresses Ansible logging of parameters and results.

EXAMPLES:
  - name: Run make test
    general_ludd.agent.gludd_make:
      target: test
    register: result

  - name: Run make test-specific for one file
    general_ludd.agent.gludd_make:
      target: test-specific
      extra_args:
        - TESTFILE=tests/unit/test_foo.py
    register: result

  - name: Run make gate with long timeout
    general_ludd.agent.gludd_make:
      target: gate
      timeout_s: 3600
    register: gate_result

  - name: Assert gate passed
    ansible.builtin.assert:
      that: gate_result.success
      fail_msg: "Gate failed — {{ gate_result.stdout_tail[-200:] }}"
      success_msg: "Gate passed."

  - name: Run make lint
    general_ludd.agent.gludd_make:
      target: lint
    register: lint_result

  - name: Run make with extra env
    general_ludd.agent.gludd_make:
      target: test
      env_extra:
        DEBUG: "1"
    register: debug_test_result

RETURN:
  target:
    description: The make target that was run.
    type: str
    returned: always
  exit_code:
    description: Process exit code (C(None) if spawn failed).
    type: int
    returned: always
  success:
    description: C(true) when exit code is 0 and no timeout or OOM kill.
    type: bool
    returned: always
  duration_s:
    description: Wall-clock duration of the run in seconds.
    type: float
    returned: always
  stdout_tail:
    description: Last 16000 characters of stdout.
    type: str
    returned: always
  stderr_tail:
    description: Last 16000 characters of stderr.
    type: str
    returned: always
  timed_out:
    description: C(true) when the command exceeded its timeout.
    type: bool
    returned: always
  oom_killed:
    description: C(true) when the process was killed by OOM (exit -9 or 137).
    type: bool
    returned: always
  error:
    description: Error message when spawn or exec fails.
    type: str
    returned: on error
  phases:
    description: Phase markers extracted from stdout.
    type: list
    returned: always
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            target=dict(type="str", required=True),
            extra_args=dict(type="list", elements="str", default=[]),
            cwd=dict(type="str", required=False),
            timeout_s=dict(type="int", required=False),
            env_extra=dict(type="dict", default=None),
            stream=dict(type="bool", default=False),
            no_log=dict(type="bool", default=False),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=False,
    )

    target: str = module.params["target"]
    extra_args: list[str] = module.params["extra_args"] or []
    cwd: str | None = module.params["cwd"]
    timeout_s: int | None = module.params["timeout_s"]
    env_extra: dict[str, Any] | None = module.params["env_extra"]
    stream: bool = module.params["stream"]
    no_log: bool = module.params["no_log"]

    if no_log:
        module.no_log_values.add(target)
        module.no_log_values.update(extra_args)

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=timeout_s if timeout_s is not None else 300,
    )
    result = client.post(
        "/admin/make",
        {
            "target": target,
            "extra_args": extra_args,
            "cwd": cwd,
            "timeout_s": timeout_s,
            "env_extra": env_extra,
            "stream": stream,
        },
    )
    if result.get("_error") or result.get("_status") != 200:
        module.fail_json(
            **error_result(
                str(result.get("detail") or result.get("_error") or "daemon make request failed"),
                status=result.get("_status", 0),
            )
        )
        return

    # Ansible-style: mask secret-like environment values from no_log.
    # env_extra is applied before the subprocess runs, but by the time
    # exit_json is called, sensitive values should already be cleared from
    # params. If no_log was set, ansible-core suppresses the params block
    # and result block via no_log=True, so this is belt-and-suspenders.
    if no_log:
        module.no_log_values.add(str(result.get("exit_code")))
        module.no_log_values.add(str(result.get("stdout_tail", ""))[:200])
        module.no_log_values.add(str(result.get("stderr_tail", ""))[:200])

    module.exit_json(
        target=result.get("target", target),
        exit_code=result.get("exit_code"),
        success=bool(result.get("success", False)),
        duration_s=result.get("duration_s", 0.0),
        stdout_tail=result.get("stdout_tail", ""),
        stderr_tail=result.get("stderr_tail", ""),
        timed_out=bool(result.get("timed_out", False)),
        oom_killed=bool(result.get("oom_killed", False)),
        error=result.get("error"),
        phases=result.get("phases", []),
        changed=False,
        failed=not bool(result.get("success", False)) and not bool(result.get("timed_out", False)),
    )


if __name__ == "__main__":
    main()
