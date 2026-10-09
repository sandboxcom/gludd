#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: tolerance_model
  short_description: Evaluate a bounded tolerance model on the controller
  description:
    - Runs through the paired controller action plugin only.
    - Supports six deterministic, read-only materials tolerance operations.
    - Rejects covariance and correlation instead of implying independent RSS.
  options:
    operation:
      description: One allowlisted tolerance analysis.
      type: str
      required: true
      choices:
        - worst_case
        - rss
        - thermal
        - thermal_compensation
        - process_capability
        - assembly
    request:
      description: Operation-specific request no larger than 64 KiB.
      type: dict
      required: true

EXAMPLES:
  - name: Compute a worst-case dimensional stack
    general_ludd.materials.tolerance_model:
      operation: worst_case
      request:
        dims:
          - [10.0, 0.1]
          - [5.0, 0.2]
        unit: mm
    register: tolerance_result

RETURN:
  result:
    description: Bounded traceable tolerance calculation.
    type: dict
    returned: success
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def main() -> None:
    """Fail closed when Ansible bypasses the controller-side action plugin."""
    module = AnsibleModule(
        argument_spec={
            "operation": {
                "type": "str",
                "required": True,
                "choices": [
                    "worst_case",
                    "rss",
                    "thermal",
                    "thermal_compensation",
                    "process_capability",
                    "assembly",
                ],
            },
            "request": {"type": "dict", "required": True},
        },
        supports_check_mode=True,
    )
    module.fail_json(
        changed=False,
        msg="tolerance_model requires its controller-side action plugin",
    )


if __name__ == "__main__":
    main()
