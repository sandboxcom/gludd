#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_gate_check
  short_description: Check whether a .gate-status file is complete and passed
  description:
    - Reads the C(.gate-status) file written by C(make gate) and determines
      whether the gate run is complete (a terminal marker is present) and
      whether it passed.
    - C(gate_complete) is C(true) when the file exists and contains either
      C(=== GATE: PASSED ===) or C(=== GATE: FAILED ===).
    - C(gate_passed) is C(true) when the file exists, is complete, and
      contains C(=== GATE: PASSED ===).
    - Check-mode safe — this module performs no writes.
    - Wraps the same logic as C(scripts/gate_fresh_check.py).
  options:
    gate_path:
      description: Path to the C(.gate-status) file.
      type: str
      default: ".gate-status"
    state:
      description:
        - Operation to perform. Currently only C(check) is supported.
        - C(check) inspects the gate status file and returns the
          C(gate_complete) and C(gate_passed) flags.
      type: str
      default: check
      choices: [check]
  notes:
    - Always reports C(changed=false) — this is a read-only probe.

EXAMPLES:
  - name: Check whether the gate is complete
    general_ludd.agent.gludd_gate_check:
      gate_path: ".gate-status"
      state: check
    register: gate_result

  - name: Assert gate passed
    ansible.builtin.assert:
      that: gate_result.gate_passed
      fail_msg: "Gate did not pass — fix failures before proceeding."
      success_msg: "Gate passed."

  - name: Block commit if gate incomplete
    ansible.builtin.meta: end_play
    when: not gate_result.gate_complete

RETURN:
  gate_complete:
    description:
      - Whether the gate file exists and contains a terminal marker
        (C(=== GATE: PASSED ===) or C(=== GATE: FAILED ===)).
    type: bool
    returned: always
  gate_passed:
    description:
      - Whether the gate file exists, is complete, and contains
        C(=== GATE: PASSED ===).
    type: bool
    returned: always
  gate_path:
    description: The absolute path of the file that was checked.
    type: str
    returned: always
"""

from __future__ import annotations

from pathlib import Path

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

_TERMINAL_PASSED = "=== GATE: PASSED ==="
_TERMINAL_FAILED = "=== GATE: FAILED ==="


def _is_gate_complete(gate_path: Path) -> bool:
    if not gate_path.exists():
        return False
    try:
        content = gate_path.read_text()
    except Exception:
        return False
    return _TERMINAL_PASSED in content or _TERMINAL_FAILED in content


def _is_gate_passed(gate_path: Path) -> bool:
    if not gate_path.exists():
        return False
    try:
        content = gate_path.read_text()
    except Exception:
        return False
    return _TERMINAL_PASSED in content


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            gate_path=dict(type="str", default=".gate-status"),
            state=dict(type="str", default="check", choices=["check"]),
        ),
        supports_check_mode=True,
    )

    gate_path_raw: str = module.params["gate_path"]
    gate_path = Path(gate_path_raw).resolve()

    gate_complete = _is_gate_complete(gate_path)
    gate_passed = _is_gate_passed(gate_path)

    module.exit_json(
        gate_complete=gate_complete,
        gate_passed=gate_passed,
        gate_path=str(gate_path),
        changed=False,
        failed=False,
    )


if __name__ == "__main__":
    main()
