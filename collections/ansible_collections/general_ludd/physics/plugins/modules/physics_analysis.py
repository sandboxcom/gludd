#!/usr/bin/python
"""Execute packaged physics operations on an Ansible managed host."""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.physics.plugins.module_utils.physics_adapter import run_analysis

DOCUMENTATION = r"""
---
module: physics_analysis
short_description: Run a packaged physics, chemistry, or math operation
description:
  - Dispatches to authoritative physics collection module utilities.
  - Uses Ansible's selected managed-host interpreter without ambient path mutation.
options:
  operation:
    type: str
    required: true
    choices:
      - latex
      - math
      - organic_synthesis
      - paper_review
      - particle_experiment
      - quantum
      - spectroscopy
      - thermodynamics
  parameters:
    type: dict
    required: true
  output_dir:
    type: path
    required: true
author:
  - General Ludd
"""

RETURN = r"""
result:
  description: Stable role-compatible operation summary.
  returned: success
  type: dict
"""

ARGUMENT_SPEC: dict[str, dict[str, Any]] = {
    "operation": {
        "type": "str",
        "required": True,
        "choices": [
            "latex",
            "math",
            "organic_synthesis",
            "paper_review",
            "particle_experiment",
            "quantum",
            "spectroscopy",
            "thermodynamics",
        ],
    },
    "parameters": {"type": "dict", "required": True, "no_log": True},
    "output_dir": {"type": "path", "required": True},
}


def main() -> None:
    """Run the module and fail closed before exposing a partial success."""
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    try:
        result = run_analysis(
            str(module.params["operation"]),
            module.params["parameters"],
            str(module.params["output_dir"]),
        )
    except (OSError, TypeError, ValueError) as exc:
        module.fail_json(msg=f"physics analysis failed: {exc}")
    module.exit_json(changed=True, result=result)


if __name__ == "__main__":
    main()
