#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_traces
  short_description: Inject recent execution traces (spans/cost/phase) as ansible_facts
  description:
    - Queries the daemon's read-only C(GET /api/traces) endpoint and returns the
      recent execution-trace snapshot under C(ansible_facts.gludd_traces) so a
      playbook can branch on live trace data in C(when:) / C(vars:).
    - Read-only and check-mode safe — it performs no writes.
    - Exposes a bounded list of recent traces (trace_id, todo_id, work_type,
      total_cost_usd, total_tokens, success_rate, span_count, spans), a
      by-phase aggregate summary, and the OpenTelemetry exporter status
      (C(available) / C(disabled)).
    - Traces are sourced ONLY from the daemon's in-process recent-traces buffer
      (genuinely-captured telemetry); no spans are fabricated. When no OTLP
      collector is configured the otel exporter status is honestly C(disabled).
  options:
    todo_id:
      description: Optional filter — only traces for this todo.
      type: str
    limit:
      description: Maximum number of recent traces to return.
      type: int
      default: 20
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
  - name: Load recent traces
    general_ludd.agent.gludd_traces:
    register: traces

  - name: Show how many traces were captured
    ansible.builtin.debug:
      msg: "Captured {{ ansible_facts.gludd_traces.count }} traces"

  - name: Only traces for a specific todo
    general_ludd.agent.gludd_traces:
      todo_id: "TODO-abc123"
      limit: 5
    register: todo_traces

  - name: Warn when the generate phase is expensive
    ansible.builtin.debug:
      msg: "generate phase cost is high"
    when: >-
      (ansible_facts.gludd_traces.by_phase['generate'].total_cost_usd
        | default(0) | float) > 1.0

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd_traces) snapshot.
    type: dict
    returned: always
    contains:
      gludd_traces:
        description:
          - Trace snapshot with C(count), C(total_recorded), C(recent) (bounded
            list of traces with spans), C(by_phase) aggregate, and
            C(otel_exporter_status).
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
            todo_id=dict(type="str", default=None),
            limit=dict(type="int", default=20),
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

    params: dict[str, object] = {"limit": module.params["limit"]}
    if module.params["todo_id"]:
        params["todo_id"] = module.params["todo_id"]

    resp = client.get("/api/traces", params=params)
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return
    if status_code not in (200, 201):
        module.fail_json(**error_result(f"gludd_traces failed (HTTP {status_code})", status=status_code))
        return

    snapshot = {k: v for k, v in resp.items() if not k.startswith("_")}
    module.exit_json(**ok_result({"ansible_facts": {"gludd_traces": snapshot}}, changed=False))


if __name__ == "__main__":
    main()
