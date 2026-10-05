#!/usr/bin/python
"""Ansible module stub paired with the controller-side action plugin."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: language_operation
short_description: Execute one authenticated Gludd language operation
description:
  - This module is implemented by its controller-side action plugin.
  - No Gludd Python package or model runtime is required on the managed host.
options:
  operation:
    description: Bounded language operation name.
    required: true
    type: str
  payload:
    description: JSON-compatible operation payload.
    type: dict
    default: {}
  daemon_url:
    description: Gludd daemon base URL reachable from the controller.
    type: str
    default: http://localhost:8000
  psk:
    description: Daemon pre-shared authentication key.
    required: true
    type: str
  timeout:
    description: Request timeout in seconds.
    type: int
    default: 30
author:
  - Agentic Harness Agent
"""

EXAMPLES = r"""
- name: Detect language through the controller
  general_ludd.language.language_operation:
    operation: language_detect
    payload:
      input_text: hello
    daemon_url: http://127.0.0.1:8000
    psk: "{{ gludd_psk }}"
"""

RETURN = r"""
result:
  description: Operation-specific result returned by the daemon.
  type: dict
  returned: success
"""


def main() -> None:
    """Fail closed if Ansible bypasses the paired controller action plugin."""
    from ansible.module_utils.basic import AnsibleModule

    module = AnsibleModule(
        argument_spec={
            "operation": {"type": "str", "required": True},
            "payload": {"type": "dict", "default": {}},
            "daemon_url": {"type": "str", "default": "http://localhost:8000"},
            "psk": {"type": "str", "required": True, "no_log": True},
            "timeout": {"type": "int", "default": 30},
        },
        supports_check_mode=True,
    )
    module.fail_json(msg="language_operation requires its controller-side action plugin")


if __name__ == "__main__":
    main()
