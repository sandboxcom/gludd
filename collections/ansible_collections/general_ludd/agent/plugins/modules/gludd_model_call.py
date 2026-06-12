#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_model_call
  short_description: Run a model generation via the daemon API
  description:
    - Sends a prompt to the daemon's POST /admin/models/call endpoint.
    - Supports direct model profile selection or adaptive routing by task type.
    - Returns the generated text plus usage metadata.
  options:
    prompt:
      description: The prompt text to send to the model.
      type: str
      required: true
    model_profile:
      description: >
        Explicit model profile ID to use. Mutually exclusive with
        C(route_task_type).
      type: str
    route_task_type:
      description: >
        Task type string for adaptive routing (uses /admin/code/suggest-model
        logic). Mutually exclusive with C(model_profile).
      type: str
    max_tokens:
      description: Maximum tokens to generate.
      type: int
      default: 2048
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
    - Check mode skips the actual model call and returns a placeholder.
    - C(model_profile) and C(route_task_type) are mutually exclusive.

EXAMPLES:
  - name: Call model directly
    general_ludd.agent.gludd_model_call:
      prompt: "Write a docstring for function foo(x: int) -> str"
      model_profile: "default"
    register: model_result

  - name: Use adaptive routing
    general_ludd.agent.gludd_model_call:
      prompt: "Explain the test failure below"
      route_task_type: "code_review"
    register: routed_result

RETURN:
  text:
    description: Generated text from the model.
    type: str
    returned: success
  model_profile_id:
    description: Profile that was actually used.
    type: str
    returned: success
  usage:
    description: Token usage dict (prompt_tokens, completion_tokens, total_tokens).
    type: dict
    returned: success
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        GluddClient,
        error_result,
        ok_result,
    )
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import GluddClient, error_result, ok_result  # type: ignore[import]


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            prompt=dict(type="str", required=True),
            model_profile=dict(type="str", default=None),
            route_task_type=dict(type="str", default=None),
            max_tokens=dict(type="int", default=2048),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=120),
        ),
        mutually_exclusive=[["model_profile", "route_task_type"]],
        supports_check_mode=True,
    )

    if module.check_mode:
        module.exit_json(**ok_result(
            {"text": "[check-mode: model call skipped]", "model_profile_id": None, "usage": {}},
            changed=False,
        ))
        return

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    payload: dict = {
        "prompt": module.params["prompt"],
        "max_tokens": module.params["max_tokens"],
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
        module.fail_json(**error_result(f"model call failed: {msg}", status=status))
        return

    module.exit_json(**ok_result(
        {
            "text": resp.get("text", ""),
            "model_profile_id": resp.get("model_profile_id"),
            "usage": resp.get("usage", {}),
        },
        changed=True,
    ))


if __name__ == "__main__":
    main()
