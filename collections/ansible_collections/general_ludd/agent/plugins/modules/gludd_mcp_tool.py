#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_mcp_tool
  short_description: Invoke an MCP tool through the authenticated Gludd dispatcher
  description:
    - Sends one bounded C(kind=mcp) request to the daemon's C(/api/dispatch)
      endpoint. The daemon routes C(server/tool) to its live MCP client.
    - Transport, HTTP, dispatcher, capability, and handler failures propagate
      as Ansible task failures instead of being reported as successful no-ops.
    - Check mode validates arguments and returns the exact planned dispatch
      without contacting the daemon.
  options:
    server:
      description: MCP server name.
      type: str
      required: true
    tool:
      description: Tool name to invoke on the server.
      type: str
      required: true
    arguments:
      description: Arguments dict to pass to the tool.
      type: dict
      default: {}
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
  notes:
    - Supports C(check_mode) without executing the MCP tool.
    - Tool calls are read or mutating according to the selected MCP tool; this
      module therefore reports C(changed=false) and returns the tool result.

EXAMPLES:
  - name: Invoke an MCP tool
    general_ludd.agent.gludd_mcp_tool:
      server: "filesystem"
      tool: "read_file"
      arguments:
        path: "/workspace/myfile.py"
    register: mcp_result

  - name: Use the returned MCP result
    ansible.builtin.debug:
      msg: "MCP result: {{ mcp_result.result }}"

RETURN:
  result:
    description: MCP handler output returned by the daemon dispatcher.
    type: raw
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

_MAX_TIMEOUT_SECONDS = 300


def _fail(module: Any, message: str, *, status: object = 0) -> None:
    module.fail_json(**error_result(message, status=status))


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            server=dict(type="str", required=True),
            tool=dict(type="str", required=True),
            arguments=dict(type="dict", default={}),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        supports_check_mode=True,
    )

    server: str = module.params["server"]
    tool: str = module.params["tool"]
    arguments: dict[str, Any] = module.params["arguments"] or {}
    timeout: int = module.params["timeout"]
    if timeout < 1 or timeout > _MAX_TIMEOUT_SECONDS:
        _fail(module, f"timeout must be between 1 and {_MAX_TIMEOUT_SECONDS} seconds")
        return

    call = {
        "kind": "mcp",
        "name": f"{server}/{tool}",
        "args": arguments,
    }
    if module.check_mode:
        module.exit_json(
            **ok_result(
                {
                    "check_mode": True,
                    "planned_call": call,
                    "result": {},
                    "server": server,
                    "tool": tool,
                },
                changed=False,
            )
        )
        return

    response = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=timeout,
    ).post("/api/dispatch", call)
    status = response.get("_status", 0)
    if response.get("_error") or status != 200:
        detail = response.get("detail") or response.get("_error") or f"HTTP {status}"
        _fail(module, f"MCP dispatch failed: {detail}", status=status)
        return

    results = response.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        _fail(module, "MCP dispatch returned an invalid response", status=status)
        return
    dispatch_result = results[0]
    if dispatch_result.get("ok") is not True:
        detail = dispatch_result.get("error") or "unknown dispatcher failure"
        _fail(module, f"MCP dispatch failed: {detail}", status=status)
        return

    module.exit_json(
        **ok_result(
            {
                "result": dispatch_result.get("output"),
                "server": server,
                "tool": tool,
            },
            changed=False,
        )
    )


if __name__ == "__main__":
    main()
