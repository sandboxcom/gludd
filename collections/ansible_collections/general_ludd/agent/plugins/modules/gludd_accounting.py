#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_accounting
  short_description: Fetch per-project accounting snapshots via the daemon
  description:
    - C(state=all) calls C(GET /api/accounting) and returns accounting
      snapshots for ALL known projects as C(ansible_facts.gludd_accounting).
    - C(state=project) calls C(GET /api/accounting/{project_id}) and returns
      the accounting snapshot for a single project.
    - Both operations are read-only. PSK-authed; check-mode safe.
  options:
    state:
      description: Operation to perform.
      type: str
      default: all
      choices: [all, project]
    project_id:
      description: Project ID to fetch when C(state=project).
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
  - name: Load accounting for all projects
    general_ludd.agent.gludd_accounting:
    register: acct_result

  - name: Load accounting for a specific project
    general_ludd.agent.gludd_accounting:
      state: project
      project_id: "my-project"
    register: acct_proj

  - name: Show accounting snapshot
    ansible.builtin.debug:
      msg: "{{ acct_result.accounting }}"

RETURN:
  ansible_facts:
    description: Contains C(gludd_accounting) with the accounting list or snapshot.
    type: dict
    returned: always
  accounting:
    description: List of accounting snapshots (state=all) or single snapshot dict (state=project).
    type: raw
    returned: always
  project_id:
    description: Project ID (state=project only).
    type: str
    returned: when state=project
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
    module: Any,
    resp: dict[str, Any],
    label: str,
    ok_codes: tuple[int, ...] = (200,),
) -> bool:
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return False
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return False
    if status_code not in ok_codes:
        msg = resp.get("detail") or f"HTTP {status_code}"
        module.fail_json(**error_result(f"{label} failed: {msg}", status=status_code))
        return False
    return True


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(type="str", default="all", choices=["all", "project"]),
            project_id=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        supports_check_mode=True,
    )

    state: str = module.params["state"]
    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    if state == "all":
        if module.check_mode:
            module.exit_json(
                **ok_result(
                    {
                        "accounting": [],
                        "ansible_facts": {"gludd_accounting": []},
                        "msg": "check_mode: accounting fetch skipped",
                    },
                    changed=False,
                )
            )
            return

        resp = client.get("/api/accounting")
        if not _check_status(module, resp, "gludd_accounting all", ok_codes=(200,)):
            return

        # The mock daemon wraps the list as {"accounting": [...], "total": N}
        # (GluddClient cannot return a raw list; the status code is injected as _status)
        accounting: list[Any] = resp.get("accounting", [])
        module.exit_json(
            **ok_result(
                {
                    "accounting": accounting,
                    "ansible_facts": {"gludd_accounting": accounting},
                },
                changed=False,
            )
        )

    elif state == "project":
        project_id = module.params["project_id"]
        if not project_id:
            module.fail_json(**error_result("project_id is required when state=project"))
            return

        if module.check_mode:
            module.exit_json(
                **ok_result(
                    {
                        "accounting": {},
                        "project_id": project_id,
                        "ansible_facts": {"gludd_accounting": {}},
                        "msg": "check_mode: accounting fetch skipped",
                    },
                    changed=False,
                )
            )
            return

        resp = client.get(f"/api/accounting/{project_id}")
        if not _check_status(module, resp, f"gludd_accounting project {project_id}", ok_codes=(200,)):
            return

        module.exit_json(
            **ok_result(
                {
                    "accounting": resp,
                    "project_id": project_id,
                    "ansible_facts": {"gludd_accounting": resp},
                },
                changed=False,
            )
        )


if __name__ == "__main__":
    main()
