#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_dispatch
  short_description: Interact with the daemon's dynamic-dispatch API
  description:
    - C(state=dispatch) POSTs a tool-call to C(POST /api/dispatch) with a
      C(kind)/C(name)/C(args) body and returns the dispatch result as
      C(ansible_facts.gludd_dispatch).
    - C(state=available) calls C(GET /api/dispatch/available) and returns
      the list of dispatchable tool handlers.
    - C(state=recent) calls C(GET /api/dispatch/recent) and returns the
      most recent dispatch records.
    - PSK-authed; check-mode safe on C(state=available) and C(state=recent).
      C(state=dispatch) skips the API call in check mode and returns an empty result.
  options:
    state:
      description: Operation to perform.
      type: str
      default: available
      choices: [dispatch, available, recent]
    kind:
      description: Tool-call kind (required for state=dispatch).
      type: str
    name:
      description: Tool-call name / handler identifier (required for state=dispatch).
      type: str
    args:
      description: Tool-call arguments dict (required for state=dispatch).
      type: dict
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
  - name: Dispatch a tool call
    general_ludd.agent.gludd_dispatch:
      state: dispatch
      kind: "tool"
      name: "shell"
      args:
        command: "echo hello"
    register: dispatch_result

  - name: List available dispatch handlers
    general_ludd.agent.gludd_dispatch:
      state: available
    register: available_result

  - name: Fetch recent dispatch records
    general_ludd.agent.gludd_dispatch:
      state: recent
    register: recent_result

RETURN:
  ansible_facts:
    description: Contains C(gludd_dispatch) with the dispatch result or records.
    type: dict
    returned: always
  result:
    description: The dispatch outcome (state=dispatch).
    type: dict
    returned: when state=dispatch
  handlers:
    description: List of available dispatch handler dicts (state=available).
    type: list
    returned: when state=available
  records:
    description: List of recent dispatch record dicts (state=recent).
    type: list
    returned: when state=recent
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
    ok_codes: tuple[int, ...] = (200, 201),
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
            state=dict(type="str", default="available", choices=["dispatch", "available", "recent"]),
            kind=dict(type="str", default=None),
            name=dict(type="str", default=None),
            args=dict(type="dict", default=None),
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

    if state == "dispatch":
        # Validate required params
        for param in ("kind", "name", "args"):
            if not module.params[param] and module.params[param] is not None:
                # Allow empty string kind/name to fail at API level
                pass
        if module.params["kind"] is None:
            module.fail_json(**error_result("state=dispatch requires 'kind'"))
            return
        if module.params["name"] is None:
            module.fail_json(**error_result("state=dispatch requires 'name'"))
            return

        if module.check_mode:
            module.exit_json(
                **ok_result(
                    {
                        "result": {},
                        "msg": "check_mode: dispatch skipped",
                        "ansible_facts": {"gludd_dispatch": {}},
                    },
                    changed=False,
                )
            )
            return

        body = {
            "kind": module.params["kind"],
            "name": module.params["name"],
            "args": module.params["args"] or {},
        }
        resp = client.post("/api/dispatch", body=body)
        if not _check_status(module, resp, "gludd_dispatch dispatch", ok_codes=(200, 201)):
            return

        result = resp.get("result", resp)
        module.exit_json(
            **ok_result(
                {
                    "result": result,
                    "ansible_facts": {"gludd_dispatch": result},
                },
                changed=True,
            )
        )

    elif state == "available":
        resp = client.get("/api/dispatch/available")
        if not _check_status(module, resp, "gludd_dispatch available", ok_codes=(200,)):
            return

        handlers = resp.get("handlers", [])
        module.exit_json(
            **ok_result(
                {
                    "handlers": handlers,
                    "ansible_facts": {"gludd_dispatch": {"handlers": handlers}},
                },
                changed=False,
            )
        )

    elif state == "recent":
        resp = client.get("/api/dispatch/recent")
        if not _check_status(module, resp, "gludd_dispatch recent", ok_codes=(200,)):
            return

        records = resp.get("records", [])
        module.exit_json(
            **ok_result(
                {
                    "records": records,
                    "ansible_facts": {"gludd_dispatch": {"records": records}},
                },
                changed=False,
            )
        )


if __name__ == "__main__":
    main()
