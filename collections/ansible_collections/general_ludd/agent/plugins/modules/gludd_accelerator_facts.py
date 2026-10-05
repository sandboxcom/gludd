#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_accelerator_facts
  short_description: Expose Gludd local and Slurm accelerator inventory as facts
  description:
    - Reads Gludd's normalized, read-only accelerator discovery endpoints.
    - Returns Apple Metal, Intel XPU, JAX TPU, NVIDIA, AMD, and Slurm GRES
      inventory under C(ansible_facts.gludd_accelerators).
    - Hardware models are returned as observed values rather than schema keys.
    - Performs no provisioning and is safe in check mode.
  options:
    scope:
      description: Inventory sources to query.
      type: str
      choices: [local, slurm, all]
      default: all
    daemon_url:
      description: Base URL of the Gludd daemon.
      type: str
      default: http://localhost:8000
    psk:
      description: Pre-shared key for daemon authentication.
      type: str
      no_log: true
      default: ''
    timeout:
      description: Per-request timeout in seconds.
      type: int
      default: 30

EXAMPLES:
  - name: Discover every usable accelerator
    general_ludd.agent.gludd_accelerator_facts:
      scope: all

  - name: Schedule only when a Slurm accelerator is free
    ansible.builtin.debug:
      msg: "{{ ansible_facts.gludd_accelerators.resources }}"
    when: ansible_facts.gludd_accelerators.available_count | int > 0

RETURN:
  ansible_facts:
    description: Facts containing the normalized accelerator inventory.
    type: dict
    returned: always
"""

from __future__ import annotations

from typing import TypedDict

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)

_MAX_ACCELERATORS = 100_000
_PATHS = {
    "local": "/admin/hardware/accelerators",
    "slurm": "/admin/slurm/hardware",
}


class _Inventory(TypedDict):
    schema_version: int
    total_count: int
    available_count: int
    resources: list[dict[str, object]]


def _bounded_count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if not 0 <= value <= _MAX_ACCELERATORS:
        raise ValueError(f"{field_name} is outside its bounded range")
    return value


def _validated_inventory(response: dict[str, object], source: str) -> _Inventory:
    if response.get("_error"):
        raise RuntimeError(f"{source} accelerator inventory request failed")
    status = response.get("_status", 0)
    if status != 200:
        raise RuntimeError(f"{source} accelerator inventory returned HTTP {status}")
    if response.get("schema_version") != 1:
        raise ValueError(f"{source} accelerator inventory has an unsupported schema")
    total_count = _bounded_count(response.get("total_count"), "total_count")
    available_count = _bounded_count(response.get("available_count"), "available_count")
    if available_count > total_count:
        raise ValueError("available_count exceeds total_count")
    resources = response.get("resources")
    if not isinstance(resources, list) or any(not isinstance(resource, dict) for resource in resources):
        raise ValueError(f"{source} accelerator inventory resources must be a list of objects")
    if len(resources) > _MAX_ACCELERATORS:
        raise ValueError(f"{source} accelerator inventory is too large")
    return {
        "schema_version": 1,
        "total_count": total_count,
        "available_count": available_count,
        "resources": list(resources),
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            scope=dict(type="str", choices=["local", "slurm", "all"], default="all"),
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
    scope: str = module.params["scope"]
    requested = ("local", "slurm") if scope == "all" else (scope,)
    inventories: dict[str, _Inventory] = {}
    try:
        for source in requested:
            inventories[source] = _validated_inventory(
                client.get(_PATHS[source]),
                source,
            )
        resources = [
            resource
            for source in requested
            for resource in inventories[source]["resources"]
        ]
        facts = {
            "schema_version": 1,
            "scope": scope,
            "total_count": sum(inventories[source]["total_count"] for source in requested),
            "available_count": sum(
                inventories[source]["available_count"] for source in requested
            ),
            "resources": resources,
            "inventories": inventories,
        }
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        module.fail_json(**error_result(f"accelerator facts failed: {exc}"))
        return
    module.exit_json(
        **ok_result(
            {"ansible_facts": {"gludd_accelerators": facts}},
            changed=False,
        )
    )


if __name__ == "__main__":
    main()
