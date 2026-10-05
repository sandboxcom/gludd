#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_langchain_generate
  short_description: Generate text (optionally structured JSON) via the daemon
  description:
    - Sends a prompt to the daemon's POST /admin/models/call endpoint, which
      runs the generation through the LangChain-backed model gateway.
    - When C(response_schema) is supplied the returned text is fence-stripped
      and parsed as JSON; a parse failure is returned as a clean module
      failure rather than a traceback.
    - This module is stdlib-only; it never imports LangChain itself. All
      LangChain work happens server-side in the daemon.
  options:
    prompt:
      description: The prompt text to send to the model.
      type: str
      required: true
    system:
      description: Optional system instruction prepended to the request.
      type: str
      default: ""
    model_profile:
      description: >
        Explicit model profile ID to use. Mutually exclusive with
        C(route_task_type).
      type: str
    route_task_type:
      description: >
        Task type string for adaptive routing. Mutually exclusive with
        C(model_profile).
      type: str
    response_schema:
      description: >
        When set, the model output is parsed as JSON (after stripping any
        Markdown code fences). The dict itself documents the expected shape.
      type: dict
      default: null
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
  - name: Generate free-form text
    general_ludd.agent.gludd_langchain_generate:
      prompt: "Summarize the CI failure below in one sentence."
      model_profile: "default"
    register: gen_result

  - name: Generate structured JSON
    general_ludd.agent.gludd_langchain_generate:
      prompt: "Return the package name and version as JSON."
      response_schema:
        name: str
        version: str
    register: structured_result

RETURN:
  text:
    description: Generated text from the model (raw, fences intact).
    type: str
    returned: success
  structured:
    description: >
      Parsed JSON object when C(response_schema) was provided; otherwise null.
    type: dict
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

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
    parse_structured,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            prompt=dict(type="str", required=True),
            system=dict(type="str", default=""),
            model_profile=dict(type="str", default=None),
            route_task_type=dict(type="str", default=None),
            response_schema=dict(type="dict", default=None),
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
            {
                "text": "[check-mode]",
                "structured": None,
                "model_profile_id": None,
                "usage": {},
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

    text = resp.get("text", "")
    structured = None
    if module.params["response_schema"] is not None:
        structured, reason = parse_structured(text, module.params["response_schema"])
        if reason is not None:
            module.fail_json(**error_result(
                "structured output not valid JSON",
                raw=text,
                reason=reason,
            ))
            return

    module.exit_json(**ok_result(
        {
            "text": text,
            "structured": structured,
            "model_profile_id": resp.get("model_profile_id"),
            "usage": resp.get("usage", {}),
        },
        changed=False,
    ))


if __name__ == "__main__":
    main()
