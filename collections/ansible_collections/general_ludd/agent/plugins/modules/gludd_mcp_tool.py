#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_mcp_tool
  short_description: Invoke an MCP tool (honest placeholder — not yet wired)
  description:
    - Per the W3.9 decision in TASKS.md (MCP honestly fenced): the daemon
      loads C(mcp_servers) config but passes C(mcp_client=None) — no MCP
      tools can be called through the daemon today.
    - This module exists so playbooks can reference C(general_ludd.agent.gludd_mcp_tool)
      without import errors; it always returns C(not_implemented=true) and
      C(failed=false) so callers can C(when: not mcp_result.not_implemented)
      gate around it cleanly.
    - When MCP wiring (W3.9 option a) is completed, replace the body of this
      module and remove this note.
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
    - Returns C(not_implemented=true) until W3.9 option (a) is completed.
    - Never fails — callers should gate on C(not not_implemented).

EXAMPLES:
  - name: Attempt MCP tool call
    general_ludd.agent.gludd_mcp_tool:
      server: "filesystem"
      tool: "read_file"
      arguments:
        path: "/workspace/myfile.py"
    register: mcp_result

  - name: Only use result if MCP is wired
    ansible.builtin.debug:
      msg: "MCP result: {{ mcp_result.result }}"
    when: not mcp_result.not_implemented

RETURN:
  not_implemented:
    description: Always true until W3.9 option (a) is completed.
    type: bool
    returned: always
  reason:
    description: Explanation of why MCP is not yet available.
    type: str
    returned: always
  result:
    description: Tool result (empty until wired).
    type: dict
    returned: always
"""

from __future__ import annotations

import os

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        ok_result,
    )
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import ok_result  # type: ignore[import]

# W3.9 decision: MCP is honestly fenced.
# daemon.py:403 passes mcp_client=None; mcp_servers config is loaded but unused.
# When W3.9 option (a) wiring lands, implement this module for real.
_W3_9_REASON = (
    "MCP not yet wired: daemon.py passes mcp_client=None (W3.9 decision). "
    "See TASKS.md W3.9 for resolution path."
)


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

    module.exit_json(**ok_result(
        {
            "not_implemented": True,
            "reason": _W3_9_REASON,
            "result": {},
            "server": module.params["server"],
            "tool": module.params["tool"],
        },
        changed=False,
    ))


if __name__ == "__main__":
    main()
