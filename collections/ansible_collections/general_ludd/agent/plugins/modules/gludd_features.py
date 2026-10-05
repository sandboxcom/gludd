#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_features
  short_description: Fetch and verify the feature database via the daemon
  description:
    - C(state=list) calls C(GET /api/features) and returns all features
      (optionally filtered by status/category) as C(ansible_facts.gludd_features).
    - C(state=verify) calls C(POST /api/features/verify) to trigger the
      server-side FeatureVerifier pass and returns the verification summary
      + per-feature results.
    - Both operations are read-only from the controller perspective; the daemon
      may persist verification results internally on a C(state=verify) call.
    - PSK-authed; check-mode safe on C(state=list). C(state=verify) skips the
      API call in check mode and returns an empty summary.
  options:
    state:
      description: Operation to perform.
      type: str
      default: list
      choices: [list, verify]
    status:
      description: Filter by feature status when C(state=list).
      type: str
      choices: [requested, implemented, verified, regressed]
    category:
      description: Filter by feature category when C(state=list).
      type: str
    project_id:
      description: Optional project scope for the verify call.
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
  - name: Load all features as facts
    general_ludd.agent.gludd_features:
    register: feat_result

  - name: List only verified features
    general_ludd.agent.gludd_features:
      state: list
      status: verified
    register: verified_feats

  - name: Trigger server-side verification pass
    general_ludd.agent.gludd_features:
      state: verify
    register: verify_result

  - name: Show verification summary
    ansible.builtin.debug:
      msg: "{{ verify_result.summary }}"

RETURN:
  ansible_facts:
    description: Contains C(gludd_features) with the feature list (state=list).
    type: dict
    returned: when state=list
  features:
    description: List of feature dicts (state=list).
    type: list
    returned: when state=list
  total:
    description: Total number of features returned (state=list).
    type: int
    returned: when state=list
  summary:
    description: Verification summary dict (state=verify).
    type: dict
    returned: when state=verify
  results:
    description: Per-feature verification result list (state=verify).
    type: list
    returned: when state=verify
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
            state=dict(type="str", default="list", choices=["list", "verify"]),
            status=dict(
                type="str",
                default=None,
                choices=["requested", "implemented", "verified", "regressed"],
            ),
            category=dict(type="str", default=None),
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

    if state == "list":
        params = {}
        if module.params["status"]:
            params["status"] = module.params["status"]
        if module.params["category"]:
            params["category"] = module.params["category"]

        resp = client.get("/api/features", params=params or None)
        if not _check_status(module, resp, "gludd_features list", ok_codes=(200,)):
            return

        features = resp.get("features", [])
        total = resp.get("total", len(features))
        module.exit_json(
            **ok_result(
                {
                    "features": features,
                    "total": total,
                    "ansible_facts": {"gludd_features": features},
                },
                changed=False,
            )
        )

    elif state == "verify":
        if module.check_mode:
            module.exit_json(
                **ok_result(
                    {
                        "summary": {},
                        "results": [],
                        "msg": "check_mode: verify skipped",
                    },
                    changed=False,
                )
            )
            return

        params = {}
        if module.params["project_id"]:
            params["project_id"] = module.params["project_id"]

        resp = client.post("/api/features/verify", body=params or None)
        if not _check_status(module, resp, "gludd_features verify", ok_codes=(200, 201)):
            return

        summary = resp.get("summary", {})
        results = resp.get("results", [])
        module.exit_json(
            **ok_result(
                {"summary": summary, "results": results},
                changed=True,
            )
        )


if __name__ == "__main__":
    main()
