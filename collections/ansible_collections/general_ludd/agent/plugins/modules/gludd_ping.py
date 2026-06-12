#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_ping
  short_description: Verify daemon reachability
  description:
    - Pings the general_ludd daemon by calling /healthz.
    - Returns C(pong=true) and C(daemon_reachable) flag.
    - Safe to use in check mode (read-only).
  options:
    daemon_url:
      description: Base URL of the daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      no_log: true
      default: ""
    timeout:
      description: Request timeout in seconds.
      type: int
      default: 10
  notes:
    - Always reports C(changed=false) — this is a read-only probe.

EXAMPLES:
  - name: Ping the daemon
    general_ludd.agent.gludd_ping:
      daemon_url: "http://localhost:8000"
    register: ping_result

  - name: Assert daemon reachable
    ansible.builtin.assert:
      that: ping_result.daemon_reachable

RETURN:
  pong:
    description: Always true when module succeeds.
    type: bool
    returned: always
  daemon_reachable:
    description: Whether /healthz responded 200.
    type: bool
    returned: always
  daemon_url:
    description: URL that was probed.
    type: str
    returned: always
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        GluddClient,
        error_result,
        ok_result,
    )
except ImportError:
    # Fallback for in-tree testing without a full collection install
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import GluddClient, error_result, ok_result  # type: ignore[import]


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=10),
        ),
        supports_check_mode=True,
    )

    daemon_url: str = module.params["daemon_url"]
    psk: str = module.params["psk"]
    timeout: int = module.params["timeout"]

    client = GluddClient(base_url=daemon_url, psk=psk, timeout=timeout)
    reachable = client.reachable()

    result = ok_result(
        {"pong": True, "daemon_reachable": reachable, "daemon_url": daemon_url},
        changed=False,
    )
    module.exit_json(**result)


if __name__ == "__main__":
    main()
