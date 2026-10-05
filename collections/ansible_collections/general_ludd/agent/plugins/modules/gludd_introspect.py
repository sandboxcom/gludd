#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_introspect
  short_description: Inject codebase self-knowledge facts (churn/complexity/coverage/debt) as ansible_facts
  description:
    - Queries the daemon's read-only C(GET /api/facts) endpoint and returns the
      C(codebase) self-introspection block under C(ansible_facts.gludd.codebase)
      so a self-improvement playbook can pick a high-value target (low coverage
      intersect high churn intersect debt).
    - Read-only and check-mode safe — performs no writes.
    - Exposes C(gludd.codebase.churn), C(gludd.codebase.complexity),
      C(gludd.codebase.coverage), C(gludd.codebase.debt),
      C(gludd.codebase.dead_code), C(gludd.codebase.missing_tests),
      C(gludd.codebase.perf_cost), and C(gludd.codebase.recent_failures).
      Each facet is C(null) when its source is unavailable — nothing is faked.
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
  - name: Load codebase introspection facts
    general_ludd.agent.gludd_introspect:
    register: introspect

  - name: Pick a low-coverage target
    ansible.builtin.debug:
      msg: "{{ gludd.codebase.coverage.low_coverage }}"
    when: gludd.codebase.coverage is not none

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd.codebase) snapshot.
    type: dict
    returned: always
    contains:
      gludd:
        description: Dict with a C(codebase) key holding the introspection snapshot.
        type: dict
        returned: always
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


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

    resp = client.get("/api/facts")
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return
    if status_code not in (200, 201):
        module.fail_json(
            **error_result(f"gludd_introspect failed (HTTP {status_code})", status=status_code)
        )
        return

    codebase = resp.get("codebase", {})
    module.exit_json(
        **ok_result({"ansible_facts": {"gludd": {"codebase": codebase}}}, changed=False)
    )


if __name__ == "__main__":
    main()
