#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_environment
  short_description: Inject the consolidated environment + optimization brief as ansible_facts
  description:
    - Queries the daemon's read-only C(GET /api/environment) endpoint and returns
      the consolidated environment brief under C(ansible_facts.gludd_environment)
      so a playbook (or the model running a job) can see the environment it runs
      inside and how to optimize for the task.
    - Read-only and check-mode safe — it performs no writes.
    - Exposes C(models) (roster, NO secrets), C(routing), C(budget), C(compute),
      C(tools), C(skills), C(queues), C(system), and C(optimization) (advisor
      hints + per-work-type recommended profiles).
    - The model roster NEVER contains api keys, tokens, or credential aliases.
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
      default: 30

EXAMPLES:
  - name: Load gludd environment brief
    general_ludd.agent.gludd_environment:
    register: env

  - name: Prefer the recommended profile for mechanical work
    ansible.builtin.debug:
      msg: >-
        Use {{ ansible_facts.gludd_environment.optimization.recommended_profile_for.mechanical }}

  - name: Warn when budget pressure is flagged
    ansible.builtin.debug:
      msg: "Budget pressure detected"
    when: >-
      ansible_facts.gludd_environment.optimization.hints
      | selectattr('signal', 'equalto', 'budget') | list | length > 0

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd_environment) snapshot.
    type: dict
    returned: always
    contains:
      gludd_environment:
        description:
          - Environment brief with C(models), C(routing), C(budget), C(compute),
            C(tools), C(skills), C(queues), C(system), and C(optimization).
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

    resp = client.get("/api/environment")
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return
    if status_code not in (200, 201):
        module.fail_json(
            **error_result(
                f"gludd_environment failed (HTTP {status_code})", status=status_code
            )
        )
        return

    snapshot = {k: v for k, v in resp.items() if not k.startswith("_")}
    module.exit_json(
        **ok_result({"ansible_facts": {"gludd_environment": snapshot}}, changed=False)
    )


if __name__ == "__main__":
    main()
