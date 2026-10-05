#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_langgraph_decision
  short_description: Ask the model to choose one option from a fixed set
  description:
    - Sends a decision prompt plus a list of allowed option tokens to the
      daemon's POST /admin/models/call endpoint and asks the model to reply
      with JSON of the form {"decision": ..., "rationale": ...}.
    - The reply is fence-stripped and JSON-parsed; the chosen decision is
      validated against C(options). On any failure (bad JSON, decision not in
      the option set) the module falls back to the first option, marks
      C(valid)=false, and records a warning rather than crashing.
    - This module is stdlib-only; it never imports LangGraph/LangChain.
  options:
    prompt:
      description: The decision prompt / question to put to the model.
      type: str
      required: true
    options:
      description: Allowed decision tokens; the model must pick exactly one.
      type: list
      elements: str
      required: true
    system:
      description: System instruction steering the decision-engine behavior.
      type: str
      default: >-
        You are a decision engine; reply with exactly one of the option
        tokens and a short rationale as JSON
        {"decision":...,"rationale":...}
    model_profile:
      description: >
        Explicit model profile ID. Mutually exclusive with C(route_task_type).
      type: str
    route_task_type:
      description: >
        Task type for adaptive routing. Mutually exclusive with
        C(model_profile).
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
      default: 120
  notes:
    - Check mode skips the call and returns a placeholder.
    - C(model_profile) and C(route_task_type) are mutually exclusive.

EXAMPLES:
  - name: Choose a deployment strategy
    general_ludd.agent.gludd_langgraph_decision:
      prompt: "Given the diff is large and risky, pick a rollout strategy."
      options:
        - "canary"
        - "blue_green"
        - "all_at_once"
      model_profile: "default"
    register: decision_result

  - name: Use the decision token
    ansible.builtin.debug:
      msg: "Chose {{ decision_result.decision }} ({{ decision_result.rationale }})"

RETURN:
  decision:
    description: The chosen option token (always one of C(options)).
    type: str
    returned: success
  rationale:
    description: Short rationale string from the model (may be empty).
    type: str
    returned: success
  valid:
    description: >
      True if the model returned valid JSON with a decision in C(options);
      false if the module fell back to the first option.
    type: bool
    returned: success
  raw:
    description: Raw model text (fences intact) for debugging.
    type: str
    returned: success
  model_profile_id:
    description: Profile that was actually used.
    type: str
    returned: success
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
    parse_structured,
)

_DEFAULT_SYSTEM = (
    "You are a decision engine; reply with exactly one of the option tokens "
    'and a short rationale as JSON {"decision":...,"rationale":...}'
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            prompt=dict(type="str", required=True),
            options=dict(type="list", elements="str", required=True),
            system=dict(type="str", default=_DEFAULT_SYSTEM),
            model_profile=dict(type="str", default=None),
            route_task_type=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=120),
        ),
        mutually_exclusive=[["model_profile", "route_task_type"]],
        supports_check_mode=True,
    )

    options = module.params["options"]
    if not options:
        module.fail_json(**error_result("options must be a non-empty list"))
        return

    if module.check_mode:
        module.exit_json(**ok_result(
            {
                "decision": options[0],
                "rationale": "[check-mode]",
                "valid": False,
                "raw": "[check-mode]",
                "model_profile_id": None,
            },
            changed=False,
        ))
        return

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    payload: dict[str, Any] = {
        "prompt": module.params["prompt"],
        "system": module.params["system"],
        "response_format": "json",
        "options": options,
    }
    if module.params["model_profile"]:
        payload["model_profile"] = module.params["model_profile"]
    if module.params["route_task_type"]:
        payload["route_task_type"] = module.params["route_task_type"]

    resp = client.post("/admin/models/call", payload)

    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon unreachable: {resp['_error']}"))
        return

    status = resp.get("_status", 0)
    if status not in (200, 201):
        msg = resp.get("detail") or resp.get("_raw") or f"HTTP {status}"
        module.fail_json(**error_result(f"decision call failed: {msg}", status=status))
        return

    raw = resp.get("text", "")
    obj, reason = parse_structured(raw)

    decision = None
    rationale = ""
    valid = True
    warnings: list[str] = []

    if reason is not None or not isinstance(obj, dict):
        valid = False
        warnings.append(
            f"could not parse decision JSON ({reason or 'not an object'}); "
            f"falling back to options[0]"
        )
    else:
        decision = obj.get("decision")
        rationale = obj.get("rationale", "") or ""
        if decision not in options:
            valid = False
            warnings.append(
                f"model decision {decision!r} not in allowed options; "
                f"falling back to options[0]"
            )

    if not valid:
        decision = options[0]

    result = ok_result(
        {
            "decision": decision,
            "rationale": rationale,
            "valid": valid,
            "raw": raw,
            "model_profile_id": resp.get("model_profile_id"),
        },
        changed=False,
    )
    if warnings:
        result["warnings"] = warnings
    module.exit_json(**result)


if __name__ == "__main__":
    main()
