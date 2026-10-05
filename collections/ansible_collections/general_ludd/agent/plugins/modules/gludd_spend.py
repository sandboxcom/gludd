#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_spend
  short_description: Fetch and configure the daemon spend-limiter
  description:
    - C(state=get) calls C(GET /api/spend) and returns the current spend
      snapshot as C(ansible_facts.gludd_spend).
    - C(state=configure) calls C(POST /api/spend/configure) to update the
      spend-limiter settings (limit_usd and/or window_seconds).
    - PSK-authed; check-mode safe on C(state=get). C(state=configure) skips
      the API call in check mode and returns an empty diff.
  options:
    state:
      description: Operation to perform.
      type: str
      default: get
      choices: [get, configure]
    limit_usd:
      description: Spend limit in USD (configure only).
      type: float
    window_seconds:
      description: Rolling window size in seconds (configure only).
      type: int
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
  - name: Fetch current spend snapshot as facts
    general_ludd.agent.gludd_spend:
    register: spend_result

  - name: Show remaining budget
    ansible.builtin.debug:
      msg: "Remaining: ${{ ansible_facts.gludd_spend.remaining_usd }}"

  - name: Configure spend limit
    general_ludd.agent.gludd_spend:
      state: configure
      limit_usd: 10.0
      window_seconds: 86400
    register: configure_result

RETURN:
  ansible_facts:
    description: Contains C(gludd_spend) with the spend snapshot (state=get).
    type: dict
    returned: when state=get
  spend:
    description: Spend snapshot dict (state=get).
    type: dict
    returned: when state=get
  configured:
    description: Updated configuration dict (state=configure).
    type: dict
    returned: when state=configure
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
            state=dict(type="str", default="get", choices=["get", "configure"]),
            limit_usd=dict(type="float", default=None),
            window_seconds=dict(type="int", default=None),
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

    if state == "get":
        resp = client.get("/api/spend")
        if not _check_status(module, resp, "gludd_spend get", ok_codes=(200,)):
            return

        spend = {k: v for k, v in resp.items() if not k.startswith("_")}
        module.exit_json(
            **ok_result(
                {
                    "spend": spend,
                    "ansible_facts": {"gludd_spend": spend},
                },
                changed=False,
            )
        )

    elif state == "configure":
        if module.check_mode:
            module.exit_json(
                **ok_result(
                    {
                        "configured": {},
                        "msg": "check_mode: configure skipped",
                    },
                    changed=False,
                )
            )
            return

        body: dict[str, Any] = {}
        if module.params["limit_usd"] is not None:
            body["limit_usd"] = module.params["limit_usd"]
        if module.params["window_seconds"] is not None:
            body["window_seconds"] = module.params["window_seconds"]

        resp = client.post("/api/spend/configure", body=body or None)
        if not _check_status(module, resp, "gludd_spend configure", ok_codes=(200, 201)):
            return

        configured = {k: v for k, v in resp.items() if not k.startswith("_")}
        module.exit_json(
            **ok_result(
                {"configured": configured},
                changed=True,
            )
        )


if __name__ == "__main__":
    main()
