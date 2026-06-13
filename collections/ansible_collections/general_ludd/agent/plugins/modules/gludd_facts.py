#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_facts
  short_description: Inject live daemon facts (work/todo/model/history/messages) as ansible_facts
  description:
    - Queries the daemon's read-only C(GET /api/facts) aggregation endpoint and
      returns the structured snapshot under C(ansible_facts.gludd) so a playbook
      can branch on live data in C(when:) / C(vars:).
    - Read-only and check-mode safe — it performs no writes.
    - Exposes C(gludd.work), C(gludd.todos), C(gludd.models), C(gludd.history),
      and C(gludd.messages).
  options:
    project_id:
      description: Optional project scope for the facts snapshot.
      type: str
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
      default: 30

EXAMPLES:
  - name: Load gludd facts
    general_ludd.agent.gludd_facts:
    register: facts

  - name: Only proceed when there is backlog
    general_ludd.agent.gludd_facts:

  - name: Branch on backlog size
    ansible.builtin.debug:
      msg: "Backlog is {{ gludd.todos.backlog_size }}"
    when: gludd.todos.backlog_size | int > 0

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd) snapshot.
    type: dict
    returned: always
    contains:
      gludd:
        description: The aggregated facts snapshot (work/todos/models/history/messages).
        type: dict
        returned: always
"""

from __future__ import annotations

import os

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        GluddClient,
        error_result,
        ok_result,
    )
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import GluddClient, error_result, ok_result  # type: ignore[import]


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            project_id=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        supports_check_mode=True,
    )

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    params = {}
    if module.params["project_id"]:
        params["project_id"] = module.params["project_id"]

    resp = client.get("/api/facts", params=params or None)
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return
    if status_code not in (200, 201):
        module.fail_json(**error_result(f"gludd_facts failed (HTTP {status_code})", status=status_code))
        return

    snapshot = {k: v for k, v in resp.items() if not k.startswith("_")}
    module.exit_json(**ok_result({"ansible_facts": {"gludd": snapshot}}, changed=False))


if __name__ == "__main__":
    main()
