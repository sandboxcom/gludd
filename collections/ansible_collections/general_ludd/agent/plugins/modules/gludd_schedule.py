#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_schedule
  short_description: Compute concurrency-safe execution batches via the daemon scheduler
  description:
    - Posts a list of work-item descriptors to C(POST /api/schedule) and returns
      the ordered concurrency-safe batches as C(ansible_facts.gludd_schedule).
    - Each work item specifies its exclusive resource requirements, upstream
      dependencies, and whether it is greenfield (no shared-resource conflicts).
    - The daemon scheduler topologically sorts items, then groups them into
      batches where items within a batch may run concurrently and all
      dependencies are satisfied by strictly earlier batches.
    - PSK-authed; check-mode safe (skips the API call and returns empty batches).
  options:
    items:
      description: >
        List of work-item descriptor dicts.  Each dict must contain C(id)
        (str, required) and may contain C(resources) (list[str], default []),
        C(depends_on) (list[str], default []), and C(is_greenfield) (bool,
        default false).
      type: list
      elements: dict
      required: true
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
  - name: Compute execution plan for work items
    general_ludd.agent.gludd_schedule:
      items:
        - id: "TASK-001"
          resources: ["db"]
          depends_on: []
          is_greenfield: false
        - id: "TASK-002"
          resources: ["db"]
          depends_on: ["TASK-001"]
          is_greenfield: false
        - id: "TASK-003"
          resources: []
          depends_on: []
          is_greenfield: true
    register: schedule_result

  - name: Show the execution batches
    ansible.builtin.debug:
      msg: "Batches: {{ schedule_result.batches }}"

RETURN:
  ansible_facts:
    description: Contains C(gludd_schedule) with the ordered batches.
    type: dict
    returned: always
  batches:
    description: >
      Ordered list of batches.  Each batch is a list of work-item ids that
      may run concurrently.  Batches are ordered so that all dependencies
      of items in batch N appear in batches 0..N-1.
    type: list
    returned: always
  item_count:
    description: Number of work items submitted.
    type: int
    returned: always
  batch_count:
    description: Number of batches in the plan.
    type: int
    returned: always
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def _check_status(
    module: AnsibleModule,
    resp: dict[str, Any],
    label: str,
    ok_codes: tuple[int, ...] = (200, 201),
) -> bool:
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return False
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return False
    if status_code == 409:
        detail = resp.get("detail") or resp.get("error") or "dependency cycle detected"
        module.fail_json(**error_result(f"{label} rejected: {detail}", status=409))
        return False
    if status_code not in ok_codes:
        msg = resp.get("detail") or f"HTTP {status_code}"
        module.fail_json(**error_result(f"{label} failed: {msg}", status=status_code))
        return False
    return True


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            items=dict(
                type="list",
                elements="dict",
                required=True,
            ),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        supports_check_mode=True,
    )

    raw_items: list[dict[str, Any]] = module.params["items"] or []

    # Normalise each item so the API body is well-formed.
    normalised = []
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("id"):
            module.fail_json(**error_result("each work item must be a dict with a non-empty 'id'"))
            return
        normalised.append(
            {
                "id": str(item["id"]),
                "resources": list(item.get("resources") or []),
                "depends_on": list(item.get("depends_on") or []),
                "is_greenfield": bool(item.get("is_greenfield", False)),
            }
        )

    if module.check_mode:
        module.exit_json(
            **ok_result(
                {
                    "batches": [],
                    "item_count": len(normalised),
                    "batch_count": 0,
                    "ansible_facts": {"gludd_schedule": []},
                    "msg": "check_mode: schedule skipped",
                },
                changed=False,
            )
        )
        return

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    resp = client.post("/api/schedule", body={"items": normalised})
    if not _check_status(module, resp, "gludd_schedule", ok_codes=(200, 201)):
        return

    batches: list[list[str]] = resp.get("batches", [])
    module.exit_json(
        **ok_result(
            {
                "batches": batches,
                "item_count": len(normalised),
                "batch_count": len(batches),
                "ansible_facts": {"gludd_schedule": batches},
            },
            changed=False,
        )
    )


if __name__ == "__main__":
    main()
