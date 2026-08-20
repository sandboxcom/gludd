#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_metrics
  short_description: Inject live daemon metrics (agents/usage/cost/benchmarks) as ansible_facts
  description:
    - Queries the daemon's read-only C(GET /api/metrics) endpoint and returns the
      metrics snapshot under C(ansible_facts.gludd_metrics) so a playbook can
      branch on live cost/usage/benchmark data in C(when:) / C(vars:).
    - Read-only and check-mode safe — it performs no writes.
    - Exposes agent-level metrics, global per-model usage, per-project cost, and
      benchmark rankings (when benchmark data is available).
    - Reuses the daemon's MetricsCollector / BenchmarkRepository — no stat logic
      is recomputed client-side.
  options:
    agent_id:
      description: Optional agent filter (single agent's summary).
      type: str
    project_id:
      description: Optional project filter (per-project agents + cost).
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
  - name: Load gludd metrics
    general_ludd.agent.gludd_metrics:
    register: metrics

  - name: Branch on total agent count
    ansible.builtin.debug:
      msg: "Running agents: {{ ansible_facts.gludd_metrics.running_agents }}"

  - name: Per-project cost only
    general_ludd.agent.gludd_metrics:
      project_id: "alpha"
    register: alpha_metrics

  - name: Halt when a project blew its cost budget
    ansible.builtin.fail:
      msg: "Project cost too high"
    when: >-
      (ansible_facts.gludd_metrics.cost_by_project['alpha'] | default(0) | float) > 10.0

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd_metrics) snapshot.
    type: dict
    returned: always
    contains:
      gludd_metrics:
        description:
          - Metrics snapshot with C(agents), C(total_agents), C(running_agents),
            C(global_model_usage), C(cost_by_project), and C(benchmark_rankings).
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
            agent_id=dict(type="str", default=None),
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
    if module.params["agent_id"]:
        params["agent_id"] = module.params["agent_id"]
    if module.params["project_id"]:
        params["project_id"] = module.params["project_id"]

    resp = client.get("/api/metrics", params=params or None)
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return
    if status_code not in (200, 201):
        module.fail_json(**error_result(f"gludd_metrics failed (HTTP {status_code})", status=status_code))
        return

    snapshot = {k: v for k, v in resp.items() if not k.startswith("_")}
    module.exit_json(**ok_result({"ansible_facts": {"gludd_metrics": snapshot}}, changed=False))


if __name__ == "__main__":
    main()
