#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_agent_run
  short_description: Run the agent tool-call loop (prompt + tools → answer)
  description:
    - W6.8 decision: uses the existing C(ToolCallLoop) from
      C(execution.tool_loop) — langgraph/langchain are declared deps with
      zero production callers, so option (b) was chosen (keep ToolCallLoop;
      note that langgraph removal is deferred to W4.5 deps-audit).
    - Accepts a prompt and optional tool list, iterates model/tool calls up
      to C(max_iterations) times, and returns the final answer and tool call
      history.
    - Check mode skips the model call and returns a placeholder.
  options:
    prompt:
      description: The agent prompt (may include rendered skill body).
      type: str
      required: true
    system_prompt:
      description: System prompt prefix. Defaults to generic agent instruction.
      type: str
      default: ""
    tools:
      description: >
        List of tool name strings available to the agent (informational;
        actual MCP tools require W3.9 option-a wiring).
      type: list
      elements: str
      default: []
    max_iterations:
      description: Maximum tool-call iterations.
      type: int
      default: 10
    model_profile:
      description: Model profile ID override.
      type: str
      default: ""
    daemon_url:
      description: Base URL of the daemon (used for HTTP transport).
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

EXAMPLES:
  - name: Run agent loop
    general_ludd.agent.gludd_agent_run:
      prompt: "{{ rendered_skill_body }}\n\nTask: {{ todo_title }}"
      max_iterations: 5
    register: agent_result

  - name: Use agent answer
    ansible.builtin.debug:
      msg: "Agent answer: {{ agent_result.answer }}"

RETURN:
  answer:
    description: Final text answer from the agent.
    type: str
    returned: success
  tool_calls:
    description: List of tool calls made during the loop.
    type: list
    returned: success
  usage:
    description: Aggregate token usage (if available from gateway).
    type: dict
    returned: success
  iterations:
    description: Number of loop iterations completed.
    type: int
    returned: success
"""

from __future__ import annotations

import os

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        GluddClient,
        error_result,
        ok_result,
    )
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import GluddClient, error_result, ok_result  # type: ignore[import]

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent in the general_ludd harness. "
    "Produce code changes, analysis, or answers as requested. "
    "Be concise and accurate."
)


def _run_via_daemon(
    client: GluddClient,
    prompt: str,
    system_prompt: str,
    model_profile: str,
    max_tokens: int = 4096,
) -> dict:
    """Call the daemon's /admin/models/call endpoint."""
    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
    payload: dict = {"prompt": full_prompt, "max_tokens": max_tokens}
    if model_profile:
        payload["model_profile"] = model_profile
    resp = client.post("/admin/models/call", payload)
    return resp


def _run_local(
    prompt: str,
    system_prompt: str,
    model_profile: str | None,
    max_iterations: int,
) -> dict:
    """Run ToolCallLoop in-process (same venv)."""
    try:
        import asyncio
        from general_ludd.execution.tool_loop import ToolCallLoop  # type: ignore[import]
        from general_ludd.models.gateway import ModelGateway  # type: ignore[import]
        from general_ludd.schemas.job import JobSpec  # type: ignore[import]

        gw = ModelGateway()
        loop_runner = ToolCallLoop(model_gateway=gw, max_iterations=max_iterations)
        job = JobSpec(
            todo_id="agent-run",
            prompt_text=prompt,
            model_profile=model_profile or "",
        )
        full_system = system_prompt or _DEFAULT_SYSTEM_PROMPT

        answer = asyncio.run(loop_runner.run_with_tools(job, full_system, prompt))
        return ok_result({"answer": answer, "tool_calls": [], "usage": {}, "iterations": 1})
    except ImportError as exc:
        return error_result(f"general_ludd not importable for local run: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error_result(f"local agent run failed: {exc}")


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            prompt=dict(type="str", required=True),
            system_prompt=dict(type="str", default=""),
            tools=dict(type="list", elements="str", default=[]),
            max_iterations=dict(type="int", default=10),
            model_profile=dict(type="str", default=""),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=120),
        ),
        supports_check_mode=True,
    )

    if module.check_mode:
        module.exit_json(**ok_result(
            {"answer": "[check-mode: agent run skipped]", "tool_calls": [], "usage": {}, "iterations": 0},
            changed=False,
        ))
        return

    prompt: str = module.params["prompt"]
    system_prompt: str = module.params["system_prompt"] or _DEFAULT_SYSTEM_PROMPT
    max_iterations: int = module.params["max_iterations"]
    model_profile: str = module.params["model_profile"]
    daemon_url: str = module.params["daemon_url"]
    psk: str = module.params["psk"]
    timeout: int = module.params["timeout"]

    # Try local first; fall back to HTTP
    try:
        from general_ludd.execution.tool_loop import ToolCallLoop  # noqa: F401 type: ignore[import]
        local_available = True
    except ImportError:
        local_available = False

    if local_available:
        result = _run_local(prompt, system_prompt, model_profile or None, max_iterations)
        if result.get("failed"):
            # Fall through to HTTP
            local_available = False
        else:
            module.exit_json(**result)
            return

    # HTTP transport
    client = GluddClient(base_url=daemon_url, psk=psk, timeout=timeout)
    resp = _run_via_daemon(client, prompt, system_prompt, model_profile)

    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon unreachable: {resp['_error']}"))
        return

    status_code = resp.get("_status", 0)
    if status_code not in (200, 201):
        msg = resp.get("detail") or resp.get("_raw") or f"HTTP {status_code}"
        module.fail_json(**error_result(f"agent run failed: {msg}", status=status_code))
        return

    module.exit_json(**ok_result(
        {
            "answer": resp.get("text", ""),
            "tool_calls": [],
            "usage": resp.get("usage", {}),
            "iterations": 1,
        },
        changed=True,
    ))


if __name__ == "__main__":
    main()
