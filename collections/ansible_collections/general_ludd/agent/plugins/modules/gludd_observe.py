#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""Run cross-source observability workflows through the Gludd daemon."""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.capability_policy import (
        CapabilityError,
        extract_host,
        for_role,
    )
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        GluddClient,
        error_result,
        ok_result,
    )
except ImportError:
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from capability_policy import (  # type: ignore[import]
        CapabilityError,
        extract_host,
        for_role,
    )
    from gludd import GluddClient, error_result, ok_result  # type: ignore[import]

from general_ludd.connectors.normalize import correlate
from general_ludd.observe.facade import GluddObserve

DOCUMENTATION = r"""
---
module: gludd_observe
short_description: Correlate telemetry across daemon-registered observability sources
description:
  - Discovers operator-configured sources through the Gludd daemon.
  - Adapts those named sources to C(GluddObserve) without accepting arbitrary URLs.
  - Runs query, timeline, incident-correlation, and topology workflows.
  - Isolates a failing source so healthy source results still return.
  - All operations are read-only and check-mode safe.
options:
  op:
    description: Cross-source observability operation.
    type: str
    required: true
    choices:
      - query_sources
      - correlate_incident
      - timeline
      - topology
  role:
    description:
      - Calling capability role, returned as result metadata.
      - The default-deny policy must grant access to the daemon host.
    type: str
    default: ""
  kinds:
    description: Connector kinds selected for query, topology, or incident correlation.
    type: list
    elements: str
    default: []
  window:
    description: Connector kinds selected for a timeline operation.
    type: list
    elements: str
    default: []
  seed:
    description: Normalized incident record for C(correlate_incident).
    type: dict
    default: {}
  by:
    description: Canonical join key used for incident correlation.
    type: str
    default: trace_id
  window_s:
    description: Seconds before and after the incident seed to query.
    type: float
    default: 300.0
  spec:
    description: Bounded connector query specification.
    type: dict
    default: {}
  start:
    description: Optional inclusive start time as epoch seconds.
    type: float
  end:
    description: Optional inclusive end time as epoch seconds.
    type: float
  correlate_by:
    description: Optional join key used to group C(query_sources) records.
    type: str
    default: ""
  daemon_url:
    description: Base URL of the Gludd daemon.
    type: str
    default: http://localhost:8000
  psk:
    description: Pre-shared key for daemon authentication.
    type: str
    default: ""
    no_log: true
  timeout:
    description: Per-request timeout in seconds.
    type: int
    default: 30
author:
  - Agentic Harness
"""

EXAMPLES = r"""
- name: Build a bounded telemetry timeline
  general_ludd.agent.gludd_observe:
    op: timeline
    window:
      - logs
      - traces
    start: 1750000000
    end: 1750000300
    spec:
      service: checkout
  register: timeline

- name: Correlate an incident by trace ID
  general_ludd.agent.gludd_observe:
    op: correlate_incident
    seed: "{{ incident_seed }}"
    kinds:
      - logs
      - traces
    by: trace_id
"""

RETURN = r"""
ansible_facts:
  description: Cross-source observability result.
  returned: always
  type: dict
  contains:
    gludd_observe:
      description: Records, groups, topology, isolated errors, and operation metadata.
      type: dict
      returned: always
"""


_SUCCESS_STATUSES = frozenset({200, 201})


class _DaemonObserveSource:
    """Source-shaped adapter for one daemon-registered connector name."""

    def __init__(
        self,
        *,
        name: str,
        kind: str,
        client: GluddClient,
    ) -> None:
        self.name = name
        self.KIND = kind
        self._client = client

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        response = self._client.post(
            "/api/observe/query",
            {
                "source": self.name,
                "spec": spec,
            },
        )
        if (
            response.get("_error")
            or response.get("_status", 0) not in _SUCCESS_STATUSES
        ):
            raise RuntimeError(f"source {self.name!r} query failed")
        records = response.get("records", [])
        if not isinstance(records, list):
            raise TypeError(f"source {self.name!r} returned invalid records")
        return [record for record in records if isinstance(record, dict)]


def _discover_sources(client: GluddClient) -> dict[str, _DaemonObserveSource]:
    response = client.get("/api/observe/sources")
    if response.get("_error"):
        raise ValueError(f"observe source discovery failed: {response['_error']}")
    status = response.get("_status", 0)
    if status == 401:
        raise ValueError("observe source discovery failed: unauthorized")
    if status not in _SUCCESS_STATUSES:
        raise ValueError(f"observe source discovery failed (HTTP {status})")

    metadata = response.get("sources", [])
    if not isinstance(metadata, list):
        raise ValueError("invalid sources payload from daemon")

    sources: dict[str, _DaemonObserveSource] = {}
    for item in metadata:
        if not isinstance(item, dict):
            raise ValueError("invalid source metadata from daemon")
        name = item.get("name")
        kind = item.get("kind")
        if not isinstance(name, str) or not name:
            raise ValueError("invalid source metadata from daemon: missing name")
        if not isinstance(kind, str) or not kind:
            raise ValueError("invalid source metadata from daemon: missing kind")
        if name in sources:
            raise ValueError(f"invalid source metadata from daemon: duplicate {name!r}")
        sources[name] = _DaemonObserveSource(
            name=name,
            kind=kind,
            client=client,
        )
    return sources


def _json_safe_topology(
    topology: dict[str, dict[str, set[str]]],
) -> dict[str, dict[str, list[str]]]:
    return {
        side: {
            node: sorted(neighbors)
            for node, neighbors in adjacency.items()
        }
        for side, adjacency in topology.items()
    }


def _execute(
    op: str,
    facade: GluddObserve,
    params: dict[str, Any],
    *,
    source_count: int,
) -> dict[str, Any]:
    kinds = list(params.get("kinds") or [])
    spec = dict(params.get("spec") or {})
    start = params.get("start")
    end = params.get("end")
    facts: dict[str, Any] = {
        "op": op,
        "role": params.get("role") or "",
        "records": [],
        "groups": {},
        "topology": {"services": {}, "hosts": {}},
        "errors": [],
        "source_count": source_count,
    }

    if op == "query_sources":
        records = facade.query_sources(
            kinds,
            spec,
            start=start,
            end=end,
        )
        facts["records"] = records
        correlate_by = params.get("correlate_by") or ""
        if correlate_by:
            facts["groups"] = correlate(records, correlate_by)
    elif op == "timeline":
        window = list(params.get("window") or kinds)
        facts["records"] = facade.timeline(
            window,
            spec=spec,
            start=start,
            end=end,
        )
    elif op == "topology":
        topology = facade.topology(
            kinds=kinds,
            spec=spec,
        )
        facts["topology"] = _json_safe_topology(topology)
    elif op == "correlate_incident":
        seed = params.get("seed")
        if not isinstance(seed, dict) or not seed:
            raise ValueError("correlate_incident requires a non-empty seed")
        facts["groups"] = facade.correlate_incident(
            seed,
            kinds=kinds,
            by=params.get("by") or "trace_id",
            window_s=float(params.get("window_s") or 300.0),
            spec=spec,
        )
    else:
        raise ValueError(f"unsupported operation: {op}")

    facts["errors"] = list(facade.errors)
    return facts


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "op": {
                "type": "str",
                "required": True,
                "choices": [
                    "query_sources",
                    "correlate_incident",
                    "timeline",
                    "topology",
                ],
            },
            "role": {"type": "str", "default": ""},
            "kinds": {"type": "list", "elements": "str", "default": []},
            "window": {"type": "list", "elements": "str", "default": []},
            "seed": {"type": "dict", "default": {}},
            "by": {"type": "str", "default": "trace_id"},
            "window_s": {"type": "float", "default": 300.0},
            "spec": {"type": "dict", "default": {}},
            "start": {"type": "float", "default": None},
            "end": {"type": "float", "default": None},
            "correlate_by": {"type": "str", "default": ""},
            "daemon_url": {
                "type": "str",
                "default": "http://localhost:8000",
            },
            "psk": {"type": "str", "default": "", "no_log": True},
            "timeout": {"type": "int", "default": 30},
        },
        supports_check_mode=True,
    )

    role = module.params["role"]
    daemon_url = module.params["daemon_url"]
    try:
        for_role(role).check_network_host(extract_host(daemon_url))
    except CapabilityError as exc:
        module.fail_json(
            **error_result(
                f"observe request denied by capability policy: {exc}",
                op=module.params["op"],
                role=role,
            )
        )
        return

    client = GluddClient(
        base_url=daemon_url,
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )
    try:
        sources = _discover_sources(client)
        facade = GluddObserve(sources)
        facts = _execute(
            module.params["op"],
            facade,
            module.params,
            source_count=len(sources),
        )
    except (TypeError, ValueError) as exc:
        module.fail_json(**error_result(str(exc)))
        return

    module.exit_json(
        **ok_result(
            {"ansible_facts": {"gludd_observe": facts}},
            changed=False,
        )
    )


if __name__ == "__main__":
    main()
