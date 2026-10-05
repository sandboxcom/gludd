#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_db
  short_description: Todo/resource CRUD via daemon HTTP API (never raw SQLite)
  description:
    - Performs todo and resource operations against the daemon's REST API.
    - Supported ops: C(todo_get), C(todo_create), C(todo_update_status), C(resource_preference).
    - C(todo_create) issues POST /api/todos on the daemon (never raw SQLite).
    - NEVER opens the SQLite file directly (single-writer rule).
    - Check mode skips write operations.
  options:
    op:
      description: Operation to perform.
      type: str
      required: true
      choices: [todo_get, todo_create, todo_update_status, resource_preference]
    todo_id:
      description: Todo identifier (required for todo_get/todo_update_status).
      type: str
    status:
      description: New status string (required for todo_update_status).
      type: str
    resource_type:
      description: Resource type (required for resource_preference).
      type: str
    title:
      description: Todo title (required for todo_create).
      type: str
    description:
      description: Todo description (todo_create).
      type: str
      default: ""
    work_type:
      description: Work type for the todo (todo_create).
      type: str
      default: code
    queue:
      description: Queue to place the todo in (todo_create).
      type: str
      default: core
    priority:
      description: Todo priority — low, medium, high, critical (todo_create).
      type: str
      default: medium
    project_id:
      description: Optional project identifier (todo_create).
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
    role:
      description:
        - Capability role on whose behalf the op runs.
        - The per-role capability policy (default-DENY) gates which ops are
          permitted; an unknown role may perform no ops.
      type: str
      default: ""

EXAMPLES:
  - name: Fetch a todo
    general_ludd.agent.gludd_db:
      op: todo_get
      todo_id: "TODO-abc123"
    register: todo

  - name: Create a todo
    general_ludd.agent.gludd_db:
      op: todo_create
      title: "Implement feature X"
      description: "As a developer, I want X."
      work_type: "code"
      queue: "core"
    register: created

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
    description: Todo record (todo_get, todo_create).
    type: dict
    returned: when op=todo_get or op=todo_create
  created:
    description: Whether a todo was created.
    type: bool
    returned: when op=todo_create
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

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.capability_policy import (
    CapabilityError,
    for_role,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            op=dict(
                type="str",
                required=True,
                choices=[
                    "todo_get",
                    "todo_create",
                    "todo_update_status",
                    "resource_preference",
                ],
            ),
            todo_id=dict(type="str", default=None),
            status=dict(type="str", default=None),
            resource_type=dict(type="str", default=None),
            title=dict(type="str", default=None),
            description=dict(type="str", default=""),
            work_type=dict(type="str", default="code"),
            queue=dict(type="str", default="core"),
            priority=dict(type="str", default="medium"),
            project_id=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
            role=dict(type="str", default=""),
        ),
        required_if=[
            ("op", "todo_get", ["todo_id"]),
            ("op", "todo_create", ["title"]),
            ("op", "todo_update_status", ["todo_id", "status"]),
            ("op", "resource_preference", ["resource_type"]),
        ],
        supports_check_mode=True,
    )

    op: str = module.params["op"]

    # Capability gate (fail-closed): the role's per-role policy must explicitly
    # grant this db op.  An unknown/empty role grants nothing (default-DENY), so
    # the op is refused before any daemon call is made.
    cap = for_role(module.params["role"])
    try:
        cap.check_db_op(op)
    except CapabilityError as exc:
        module.fail_json(**error_result(
            f"db op denied by capability policy: {exc}",
            op=op,
            role=module.params["role"],
        ))
        return

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

    elif op == "todo_create":
        title: str = module.params["title"]
        body = {
            "title": title,
            "description": module.params["description"],
            "queue": module.params["queue"],
            "priority": module.params["priority"],
            "work_type": module.params["work_type"],
        }
        if module.params["project_id"] is not None:
            body["project_id"] = module.params["project_id"]
        if module.check_mode:
            module.exit_json(**ok_result({"todo": body, "created": True}, changed=True))
            return
        resp = client.post("/api/todos", body)
        if resp.get("_error"):
            module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
            return
        status_code = resp.get("_status", 0)
        if status_code not in (200, 201):
            msg = resp.get("detail") or f"HTTP {status_code}"
            module.fail_json(**error_result(f"todo_create failed: {msg}", status=status_code))
            return
        module.exit_json(**ok_result({"todo": resp, "created": True}, changed=True))

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
