#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_langgraph_workflow
  short_description: Run a multi-step LangGraph generate/review workflow
  description:
    - Sends a message list to the daemon's POST /admin/models/workflow
      endpoint, which executes a LangGraph generate -> review -> retry loop
      server-side and returns the best content plus quality metadata.
    - This module is stdlib-only; it never imports LangGraph itself. All
      graph execution happens in the daemon.
  options:
    prompt:
      description: The user prompt to send to the workflow.
      type: str
      required: true
    system:
      description: Optional system instruction; prepended as a system message.
      type: str
      default: ""
    work_type:
      description: Kind of work the graph is producing (e.g. code, docs, plan).
      type: str
      default: "code"
    model_profile:
      description: Model profile ID for the workflow.
      type: str
      default: "default"
    max_retries:
      description: Maximum generate/review retries before giving up.
      type: int
      default: 2
    quality_threshold:
      description: Minimum quality score (0-1) the output must reach.
      type: float
      default: 0.6
    enable_graph:
      description: >
        When false, the daemon runs a single-shot generation instead of the
        full graph (useful for debugging or when no review is wanted).
      type: bool
      default: true
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
      default: 300
  notes:
    - Check mode skips the workflow and returns a placeholder.

EXAMPLES:
  - name: Run a code-generation workflow
    general_ludd.agent.gludd_langgraph_workflow:
      prompt: "Implement a function that reverses a linked list."
      work_type: "code"
      quality_threshold: 0.7
    register: wf_result

  - name: Single-shot generation (no graph)
    general_ludd.agent.gludd_langgraph_workflow:
      prompt: "Draft a release note for v1.2.0."
      work_type: "docs"
      enable_graph: false
    register: wf_oneshot

RETURN:
  content:
    description: Best content the workflow produced.
    type: str
    returned: success
  model:
    description: Model/profile that produced the content.
    type: str
    returned: success
  prompt_profile:
    description: Prompt profile used by the graph.
    type: str
    returned: success
  quality_score:
    description: Quality score (0-1) of the returned content.
    type: float
    returned: success
  retries:
    description: Number of retries the graph performed.
    type: int
    returned: success
  workflow_warnings:
    description: Any warnings emitted by the workflow.
    type: list
    returned: success
"""

from __future__ import annotations

from typing import Any

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
            system=dict(type="str", default=""),
            work_type=dict(type="str", default="code"),
            model_profile=dict(type="str", default="default"),
            max_retries=dict(type="int", default=2),
            quality_threshold=dict(type="float", default=0.6),
            enable_graph=dict(type="bool", default=True),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=300),
        ),
        supports_check_mode=True,
    )

    if module.check_mode:
        module.exit_json(**ok_result(
            {
                "content": "[check-mode]",
                "model": None,
                "prompt_profile": None,
                "quality_score": 0.0,
                "retries": 0,
                "workflow_warnings": [],
            },
            changed=False,
        ))
        return

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    messages: list[dict[str, Any]] = []
    if module.params["system"]:
        messages.append({"role": "system", "content": module.params["system"]})
    messages.append({"role": "user", "content": module.params["prompt"]})

    payload: dict[str, Any] = {
        "messages": messages,
        "profile_id": module.params["model_profile"],
        "work_type": module.params["work_type"],
        "max_retries": module.params["max_retries"],
        "quality_threshold": module.params["quality_threshold"],
        "enable_graph": module.params["enable_graph"],
    }

    resp = client.post("/admin/models/workflow", payload)

    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon unreachable: {resp['_error']}"))
        return

    status = resp.get("_status", 0)
    if status not in (200, 201):
        msg = resp.get("detail") or resp.get("_raw") or f"HTTP {status}"
        module.fail_json(**error_result(f"workflow failed: {msg}", status=status))
        return

    workflow_warnings = [str(warning) for warning in resp.get("warnings", [])]
    for warning in workflow_warnings:
        module.warn(warning)

    module.exit_json(**ok_result(
        {
            "content": resp.get("content", ""),
            "model": resp.get("model"),
            "prompt_profile": resp.get("prompt"),
            "quality_score": resp.get("quality_score"),
            "retries": resp.get("retries"),
            "workflow_warnings": workflow_warnings,
        },
        changed=False,
    ))


if __name__ == "__main__":
    main()
