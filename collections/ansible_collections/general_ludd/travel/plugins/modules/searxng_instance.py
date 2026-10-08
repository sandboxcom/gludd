#!/usr/bin/python
"""Ansible module stub paired with the controller-side lifecycle action."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: searxng_instance
short_description: Manage a native controller-local SearXNG runtime
description:
  - Uses the official SearXNG Python WSGI application on the controller.
  - Does not install Docker, open a loopback listener, or require Terraform.
  - The paired action plugin implements idempotent lifecycle transitions.
options:
  state:
    description: Desired lifecycle state.
    type: str
    choices: [started, stopped, restarted, status]
    default: started
  namespace:
    description: Bounded resource namespace for parallel Gludd projects.
    type: str
    default: gludd-travel
  settings_path:
    description: Optional owner-writable SearXNG settings file on the controller.
    type: path
author:
  - Agentic Harness Agent
"""

EXAMPLES = r"""
- name: Start the native controller integration
  general_ludd.travel.searxng_instance:
    state: started
    namespace: gludd-travel
"""

RETURN = r"""
instance_uri:
  description: Non-network identity of the native runtime.
  type: str
  returned: always
running:
  description: Whether the native runtime is healthy.
  type: bool
  returned: always
"""


def main() -> None:
    """Fail closed when Ansible bypasses the paired action plugin."""
    from ansible.module_utils.basic import AnsibleModule

    module = AnsibleModule(
        argument_spec={
            "state": {
                "type": "str",
                "default": "started",
                "choices": ["started", "stopped", "restarted", "status"],
            },
            "namespace": {"type": "str", "default": "gludd-travel"},
            "settings_path": {"type": "path", "default": ""},
        },
        supports_check_mode=True,
    )
    module.fail_json(msg="searxng_instance requires its controller-side action plugin")


if __name__ == "__main__":
    main()
