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

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

from general_ludd.commands.make import MakeResult, MakeRunner


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
        ),
        supports_check_mode=False,
    )

    target: str = module.params["target"]
    extra_args: list[str] = module.params["extra_args"] or []
    cwd: str | None = module.params["cwd"]
    timeout_s: int | None = module.params["timeout_s"]
    env_extra: dict | None = module.params["env_extra"]
    stream: bool = module.params["stream"]
    no_log: bool = module.params["no_log"]

    if no_log:
        module.no_log_values.add(target)
        module.no_log_values.update(extra_args)

    runner = MakeRunner(
        cwd=cwd,
        default_timeout_s=timeout_s if timeout_s is not None else 300,
    )

    result: MakeResult
    if stream:
        phases_seen: list[str] = []

        def _phase_callback(phase: str) -> None:
            phases_seen.append(phase)

        result = runner.run(
            target,
            extra_args=extra_args,
            timeout_s=timeout_s,
            env_extra=env_extra,
            stream=True,
            stream_callback=_phase_callback,
        )
        result.phases = phases_seen or result.phases
    else:
        result = runner.run(
            target,
            extra_args=extra_args,
            timeout_s=timeout_s,
            env_extra=env_extra,
        )

    # Ansible-style: mask secret-like environment values from no_log.
    # env_extra is applied before the subprocess runs, but by the time
    # exit_json is called, sensitive values should already be cleared from
    # params. If no_log was set, ansible-core suppresses the params block
    # and result block via no_log=True, so this is belt-and-suspenders.
    if no_log:
        module.no_log_values.add(str(result.exit_code))
        module.no_log_values.add(result.stdout_tail[:200])
        module.no_log_values.add(result.stderr_tail[:200])

    module.exit_json(
        target=result.target,
        exit_code=result.exit_code,
        success=result.success,
        duration_s=result.duration_s,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
        timed_out=result.timed_out,
        oom_killed=result.oom_killed,
        error=result.error,
        phases=result.phases,
        changed=False,
        failed=not result.success and not result.timed_out,
    )


if __name__ == "__main__":
    main()
