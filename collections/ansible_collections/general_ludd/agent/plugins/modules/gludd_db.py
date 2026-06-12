#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_db
  short_description: Todo/resource CRUD via daemon HTTP API (never raw SQLite)
  description:
    - Performs todo and resource operations against the daemon's REST API.
    - Supported ops: C(todo_get), C(todo_update_status), C(resource_preference).
    - NEVER opens the SQLite file directly (single-writer rule).
    - Check mode skips write operations.
  options:
    op:
      description: Operation to perform.
      type: str
      required: true
      choices: [todo_get, todo_update_status, resource_preference]
    todo_id:
      description: Todo identifier (required for todo ops).
      type: str
    status:
      description: New status string (required for todo_update_status).
      type: str
    resource_type:
      description: Resource type (required for resource_preference).
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
  - name: Fetch a todo
    general_ludd.agent.gludd_db:
      op: todo_get
      todo_id: "TODO-abc123"
    register: todo

  - name: Mark todo complete
    general_ludd.agent.gludd_db:
      op: todo_update_status
      todo_id: "TODO-abc123"
      status: "done"

  - name: Read resource preference
    general_ludd.agent.gludd_db:
      op: resource_preference
      resource_type: "model"
    register: pref

RETURN:
  todo:
    description: Todo record (todo_get).
    type: dict
    returned: when op=todo_get
  updated:
    description: Whether the status was changed.
    type: bool
    returned: when op=todo_update_status
  preference:
    description: Resource preference value.
    type: str
    returned: when op=resource_preference
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


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            op=dict(type="str", required=True, choices=["todo_get", "todo_update_status", "resource_preference"]),
            todo_id=dict(type="str", default=None),
            status=dict(type="str", default=None),
            resource_type=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        required_if=[
            ("op", "todo_get", ["todo_id"]),
            ("op", "todo_update_status", ["todo_id", "status"]),
            ("op", "resource_preference", ["resource_type"]),
        ],
        supports_check_mode=True,
    )

    op: str = module.params["op"]
    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    if op == "todo_get":
        todo_id: str = module.params["todo_id"]
        resp = client.get(f"/api/todos/{todo_id}")
        if resp.get("_error"):
            module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
            return
        status_code = resp.get("_status", 0)
        if status_code == 404:
            module.fail_json(**error_result(f"todo not found: {todo_id}", status=404))
            return
        if status_code not in (200, 201):
            module.fail_json(**error_result(
                f"todo_get failed (HTTP {status_code})",
                status=status_code,
            ))
            return
        module.exit_json(**ok_result({"todo": resp}, changed=False))

    elif op == "todo_update_status":
        todo_id = module.params["todo_id"]
        new_status: str = module.params["status"]
        if module.check_mode:
            module.exit_json(**ok_result({"updated": True, "todo_id": todo_id, "status": new_status}, changed=True))
            return
        resp = client.patch(f"/api/todos/{todo_id}", {"status": new_status})
        if resp.get("_error"):
            module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
            return
        status_code = resp.get("_status", 0)
        if status_code not in (200, 201, 204):
            msg = resp.get("detail") or f"HTTP {status_code}"
            module.fail_json(**error_result(f"todo_update_status failed: {msg}", status=status_code))
            return
        module.exit_json(**ok_result({"updated": True, "todo_id": todo_id, "status": new_status}, changed=True))

    elif op == "resource_preference":
        resource_type: str = module.params["resource_type"]
        resp = client.get("/api/resource-preferences", params={"resource_type": resource_type})
        if resp.get("_error"):
            module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
            return
        status_code = resp.get("_status", 0)
        if status_code not in (200, 201):
            msg = resp.get("detail") or f"HTTP {status_code}"
            module.fail_json(**error_result(f"resource_preference failed: {msg}", status=status_code))
            return
        preference = resp.get("preference") or resp.get("value") or resp.get("_raw", "")
        module.exit_json(**ok_result({"preference": preference, "resource_type": resource_type}, changed=False))


if __name__ == "__main__":
    main()
