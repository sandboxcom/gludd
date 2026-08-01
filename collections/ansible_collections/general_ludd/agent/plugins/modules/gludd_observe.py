#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_observe
  short_description: Correlate registered observability sources through the Gludd daemon
  description:
    - Provides the Ansible seam for the cross-source C(GluddObserve) facade.
    - Discovers only operator-registered sources from C(GET /api/observe/sources)
      and queries them by registered name through C(POST /api/observe/query).
      Callers cannot supply connector URLs, preserving the daemon's SSRF boundary.
    - Supports merged queries, timelines, incident correlation, and service/host
      topology without duplicating the canonical normalization logic.
    - Read-only and check-mode safe. A failing connector is isolated into the
      returned C(errors) list instead of aborting successful sources.
  options:
    op:
      description: Cross-source operation to run.
      type: str
      required: true
      choices: [query_sources, timeline, correlate_incident, topology]
    role:
      description: Capability identity recorded with the result for operator attribution.
      type: str
      default: observe
    seed:
      description: Normalized incident record used by C(correlate_incident).
      type: dict
      default: {}
    kinds:
      description: Connector kinds admitted to the operation.
      type: list
      elements: str
      default: [logs, traces, metrics, events]
    by:
      description: Canonical join key for C(correlate_incident).
      type: str
      default: trace_id
    window_s:
      description: Half-window around the incident seed timestamp, in seconds.
      type: float
      default: 300.0
    spec:
      description: Backend query specification forwarded to each registered source.
      type: dict
      default: {}
    start:
      description: Optional inclusive start epoch for query and timeline operations.
      type: float
    end:
      description: Optional inclusive end epoch for query and timeline operations.
      type: float
    daemon_url:
      description: Base URL of the Gludd daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon authentication.
      type: str
      no_log: true
      default: ""
    timeout:
      description: Per-request timeout in seconds.
      type: int
      default: 30

EXAMPLES:
  - name: Build a time-ordered incident timeline
    general_ludd.agent.gludd_observe:
      op: timeline
      kinds: [logs, traces, metrics]
      start: 1785580800
      end: 1785581100
      psk: "{{ vault_gludd_psk }}"
    register: timeline

  - name: Correlate an incident by trace ID
    general_ludd.agent.gludd_observe:
      op: correlate_incident
      seed:
        ts: 1785580950
        labels:
          trace_id: trace-123
      by: trace_id

RETURN:
  ansible_facts:
    description: Facts containing the selected C(gludd_observe) operation result.
    type: dict
    returned: always
    contains:
      gludd_observe:
        description:
          - Result payload. It contains C(records), C(groups), or C(topology)
            depending on C(op), plus isolated connector C(errors).
        type: dict
        returned: always
"""

from __future__ import annotations

import os
from typing import Any

from ansible.module_utils.basic import AnsibleModule

from general_ludd.observe.facade import GluddObserve

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        GluddClient,
        error_result,
        ok_result,
    )
except ImportError:
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import (  # type: ignore[import-not-found]
        GluddClient,
        error_result,
        ok_result,
    )


_MAX_REGISTERED_SOURCES = 1000


class _RemoteSource:
    """Source adapter constrained to a daemon-registered name."""

    __slots__ = ("KIND", "client", "name")

    def __init__(self, *, name: str, kind: str, client: GluddClient) -> None:
        self.name = name
        self.KIND = kind
        self.client = client

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        response = self.client.post(
            "/api/observe/query",
            {"source": self.name, "spec": dict(spec)},
        )
        status = int(response.get("_status", 0) or 0)
        if response.get("_error") or status not in (200, 201):
            raise RuntimeError("registered source query failed")
        records = response.get("records")
        if not isinstance(records, list):
            raise TypeError("registered source returned a non-list record payload")
        return [record for record in records if isinstance(record, dict)]


def _source_provider(response: dict[str, Any], client: GluddClient) -> dict[str, _RemoteSource]:
    rows = response.get("sources")
    if not isinstance(rows, list) or len(rows) > _MAX_REGISTERED_SOURCES:
        raise ValueError("invalid registered source catalog")

    sources: dict[str, _RemoteSource] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid registered source catalog")
        name = row.get("name")
        kind = row.get("kind")
        if not isinstance(name, str) or not name or not isinstance(kind, str) or not kind:
            raise ValueError("invalid registered source catalog")
        if name in sources:
            raise ValueError("duplicate registered source name")
        sources[name] = _RemoteSource(name=name, kind=kind, client=client)
    return sources


def _json_topology(topology: dict[str, dict[str, set[str]]]) -> dict[str, dict[str, list[str]]]:
    return {
        side: {name: sorted(values) for name, values in sorted(adjacency.items())}
        for side, adjacency in topology.items()
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            op=dict(
                type="str",
                required=True,
                choices=["query_sources", "timeline", "correlate_incident", "topology"],
            ),
            role=dict(type="str", default="observe"),
            seed=dict(type="dict", default={}),
            kinds=dict(
                type="list",
                elements="str",
                default=["logs", "traces", "metrics", "events"],
            ),
            by=dict(type="str", default="trace_id"),
            window_s=dict(type="float", default=300.0),
            spec=dict(type="dict", default={}),
            start=dict(type="float", default=None),
            end=dict(type="float", default=None),
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
    source_response = client.get("/api/observe/sources")
    status = int(source_response.get("_status", 0) or 0)
    if status == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)"))
        return
    if source_response.get("_error") or status not in (200, 201):
        module.fail_json(**error_result("unable to discover registered observability sources"))
        return

    try:
        observe = GluddObserve(_source_provider(source_response, client))
        operation: str = module.params["op"]
        kinds: list[str] = module.params["kinds"]
        spec: dict[str, Any] = module.params["spec"]
        payload: dict[str, Any] = {
            "operation": operation,
            "role": module.params["role"],
        }

        if operation == "query_sources":
            payload["records"] = observe.query_sources(
                kinds,
                spec,
                start=module.params["start"],
                end=module.params["end"],
            )
        elif operation == "timeline":
            payload["records"] = observe.timeline(
                kinds,
                spec=spec,
                start=module.params["start"],
                end=module.params["end"],
            )
        elif operation == "correlate_incident":
            seed = module.params["seed"]
            if not seed:
                module.fail_json(**error_result("correlate_incident requires a non-empty seed"))
                return
            payload["groups"] = observe.correlate_incident(
                seed,
                kinds=kinds,
                by=module.params["by"],
                window_s=module.params["window_s"],
                spec=spec,
            )
        else:
            payload["topology"] = _json_topology(
                observe.topology(kinds=kinds, spec=spec)
            )
        payload["errors"] = observe.errors
    except (TypeError, ValueError):
        module.fail_json(**error_result("invalid observability response or request"))
        return

    module.exit_json(
        **ok_result({"ansible_facts": {"gludd_observe": payload}}, changed=False)
    )


if __name__ == "__main__":
    main()
