#!/usr/bin/python
"""Execute packaged forensic analysis operations on an Ansible managed host."""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.forensics.plugins.module_utils.forensic_adapter import (
    run_analysis,
)

DOCUMENTATION = r"""
---
module: forensic_analysis
short_description: Run a packaged forensic analysis operation
description:
  - Dispatches to the forensics collection's module utilities.
  - Uses Ansible's selected managed-host interpreter without ambient path mutation.
options:
  operation:
    type: str
    required: true
    choices: [dna, fingerprint, chain_of_custody, photo, trace]
  sample:
    type: dict
    default: {}
  reference:
    type: dict
    default: {}
  analysis_type:
    type: str
    default: str
  data:
    type: dict
    default: {}
  case_id:
    type: str
    default: ''
  image_path:
    type: path
    default: ''
  analysis_types:
    type: list
    elements: str
    default: []
  evidence_type:
    type: str
    default: ''
  output_dir:
    type: path
    default: ''
author:
  - General Ludd
"""

RETURN = r"""
result:
  description: Structured operation result.
  returned: success
  type: dict
"""

ARGUMENT_SPEC: dict[str, dict[str, Any]] = {
    "operation": {
        "type": "str",
        "required": True,
        "choices": ["dna", "fingerprint", "chain_of_custody", "photo", "trace"],
    },
    "sample": {"type": "dict", "default": {}, "no_log": True},
    "reference": {"type": "dict", "default": {}, "no_log": True},
    "analysis_type": {"type": "str", "default": "str"},
    "data": {"type": "dict", "default": {}, "no_log": True},
    "case_id": {"type": "str", "default": ""},
    "image_path": {"type": "path", "default": ""},
    "analysis_types": {"type": "list", "elements": "str", "default": []},
    "evidence_type": {"type": "str", "default": ""},
    "output_dir": {"type": "path", "default": ""},
}


def main() -> None:
    """Run the module and fail closed on invalid or unreadable evidence."""
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    try:
        result = run_analysis(str(module.params["operation"]), module.params)
    except (OSError, TypeError, ValueError) as exc:
        module.fail_json(msg=f"forensic analysis failed: {exc}")
    module.exit_json(changed=False, result=result)


if __name__ == "__main__":
    main()
