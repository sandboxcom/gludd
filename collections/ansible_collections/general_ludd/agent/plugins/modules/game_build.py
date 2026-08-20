#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: game_build
  short_description: Generate game code through the Gludd model service
  description:
    - Sends a game prompt template to a model and returns generated Python code.
    - Uses the authenticated daemon HTTP transport so model weights are never
      loaded once per Ansible fork.
    - The dispatch path is C(POST /api/dispatch) with C(capability=game_logic,
      action=game_build).
  options:
    prompt:
      description: The game prompt template to send to the model.
      type: str
      required: true
    model_profile:
      description: >
        Explicit model profile ID to use (e.g. C(local.qwen2.5_0.5b)).
        When omitted the daemon's default model profile is used.
      type: str
    temperature:
      description: Model temperature (0.0 = deterministic).
      type: float
      default: 0.0
    daemon_url:
      description: Base URL of the daemon (for HTTP transport).
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth (HTTP transport).
      type: str
      no_log: true
      default: ""
    timeout:
      description: Request timeout in seconds.
      type: int
      default: 120
  notes:
    - Check mode skips the model call and returns a placeholder.

EXAMPLES:
  - name: Build a Snake game via the shared local-model service
    general_ludd.agent.game_build:
      prompt: "Write a complete Python Snake game..."
      model_profile: "local.qwen2.5_0.5b"
      daemon_url: "http://localhost:8000"
      psk: "{{ gludd_daemon_psk }}"
    register: game_result

RETURN:
  text:
    description: Generated Python code from the model.
    type: str
    returned: success
  transport_used:
    description: Which transport was used (always http).
    type: str
    returned: success
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            prompt=dict(type="str", required=True),
            model_profile=dict(type="str", default=None),
            temperature=dict(type="float", default=0.0),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=120),
        ),
        supports_check_mode=True,
    )

    if module.check_mode:
        module.exit_json(
            **ok_result(
                {"text": "[check-mode: game build skipped]", "transport_used": "none"},
                changed=False,
            )
        )
        return

    prompt: str = module.params["prompt"]
    model_profile: str | None = module.params["model_profile"] or None
    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )
    resp = client.call_model(prompt, model_profile=model_profile, max_tokens=4096)
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon unreachable: {resp['_error']}"))
        return

    status = resp.get("_status", 0)
    if status not in (200, 201):
        msg = resp.get("detail") or resp.get("_raw") or f"HTTP {status}"
        module.fail_json(**error_result(f"model call failed: {msg}", status=status))
        return

    module.exit_json(
        **ok_result(
            {
                "text": resp.get("text", ""),
                "transport_used": "http",
            },
            changed=True,
        )
    )


if __name__ == "__main__":
    main()
