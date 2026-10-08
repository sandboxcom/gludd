#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_human_todo
  short_description: File or resolve a bot→human request (HumanTodo) via the daemon
  description:
    - Agents use this module to ask a human for something they cannot get on
      their own — a permission escalation, an external action, a decision,
      missing input, or another blocker. It is the structured replacement for
      "I gave up" log lines and event errors.
    - C(state=present) files a new human-todo via POST /api/human-todos.
      When C(parent_agent_todo_id) is set, the parent agent todo is moved to
      C(blocked_on_human) and will not be dispatched until the human resolves
      the request.
    - C(state=done) marks an existing human-todo done (human provided what
      was asked). C(state=dismissed) records that the human declined, so the
      agent knows to try a different approach.
    - Check mode skips write operations.
  options:
    state:
      description: Desired state.
      type: str
      default: present
      choices: [present, done, dismissed]
    title:
      description: Short human-readable summary (state=present).
      type: str
    body:
      description: Full markdown context — what's needed, why, links (state=present).
      type: str
    category:
      description: Request category (state=present).
      type: str
      choices:
        - permission_escalation
        - external_action
        - decision
        - input_request
        - blocker
      default: blocker
    priority:
      description: Request priority (state=present).
      type: str
      choices: [low, medium, high, urgent]
      default: medium
    parent_agent_todo_id:
      description: Agent todo id that's blocked on this request (optional).
      type: str
    tags:
      description: Optional tags for the request (state=present).
      type: list
      elements: str
      default: []
    id:
      description: Existing human-todo id (required for state=done/dismissed).
      type: str
    human_resolution:
      description: What the human did/said (required for state=done).
      type: str
    reason:
      description: Why the request is dismissed (required for state=dismissed).
      type: str
    human_resolver:
      description: Identifier of the human acting (done/dismissed).
      type: str
      default: operator
    agent_id:
      description: Filing agent's identifier (state=present).
      type: str
      default: agent
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
  - name: Agent blocks on a permission escalation
    general_ludd.agent.gludd_human_todo:
      state: present
      title: "Need write access to /etc/gludd/prod.conf"
      body: |
        I need to update the prod config but the current capability policy
        denies filesystem writes outside the workspace. Tried: gludd_db
        resource_preference, env-var override. Both rejected.
      category: permission_escalation
      priority: high
      parent_agent_todo_id: "TODO-abc123"
      agent_id: "implement_change_role"
    register: human_req

  - name: Agent asks for a missing API key
    general_ludd.agent.gludd_human_todo:
      state: present
      title: "OPENAI_API_KEY not set in environment"
      body: "Model gateway returns 401 on every call; key is not in OpenBao or env."
      category: input_request
      priority: urgent

  - name: Human marks the request done (the key is now set)
    general_ludd.agent.gludd_human_todo:
      state: done
      id: "HTODO-..."
      human_resolution: "OPENAI_API_KEY rotated and loaded into OpenBao at secret/gludd/openai."
      human_resolver: "shawn"

RETURN:
  human_todo:
    description: The created/updated human-todo record.
    type: dict
    returned: always
  id:
    description: The human-todo id.
    type: str
    returned: always
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
            state=dict(type="str", default="present", choices=["present", "done", "dismissed"]),
            title=dict(type="str", default=None),
            body=dict(type="str", default=None),
            category=dict(
                type="str",
                default="blocker",
                choices=[
                    "permission_escalation",
                    "external_action",
                    "decision",
                    "input_request",
                    "blocker",
                ],
            ),
            priority=dict(type="str", default="medium", choices=["low", "medium", "high", "urgent"]),
            parent_agent_todo_id=dict(type="str", default=None),
            tags=dict(type="list", elements="str", default=[]),
            id=dict(type="str", default=None),
            human_resolution=dict(type="str", default=None),
            reason=dict(type="str", default=None),
            human_resolver=dict(type="str", default="operator"),
            agent_id=dict(type="str", default="agent"),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        required_if=[
            ("state", "present", ["title", "body"]),
            ("state", "done", ["id", "human_resolution"]),
            ("state", "dismissed", ["id", "reason"]),
        ],
        supports_check_mode=True,
    )

    state: str = module.params["state"]
    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    if state == "present":
        body_payload = {
            "agent_id": module.params["agent_id"],
            "title": module.params["title"],
            "body": module.params["body"],
            "category": module.params["category"],
            "priority": module.params["priority"],
            "tags": module.params["tags"],
        }
        if module.params["parent_agent_todo_id"] is not None:
            body_payload["parent_agent_todo_id"] = module.params["parent_agent_todo_id"]
        if module.check_mode:
            module.exit_json(**ok_result({"human_todo": body_payload, "id": None}, changed=True))
            return
        resp = client.post("/api/human-todos", body_payload)
    elif state == "done":
        if module.check_mode:
            module.exit_json(**ok_result({"id": module.params["id"], "status": "done"}, changed=True))
            return
        resp = client.patch(
            f"/api/human-todos/{module.params['id']}",
            {
                "status": "done",
                "human_resolution": module.params["human_resolution"],
                "human_resolver": module.params["human_resolver"],
            },
        )
    else:  # dismissed
        if module.check_mode:
            module.exit_json(**ok_result({"id": module.params["id"], "status": "dismissed"}, changed=True))
            return
        resp = client.patch(
            f"/api/human-todos/{module.params['id']}",
            {
                "status": "dismissed",
                "human_resolution": module.params["reason"],
                "human_resolver": module.params["human_resolver"],
            },
        )

    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code not in (200, 201):
        msg = resp.get("detail") or f"HTTP {status_code}"
        module.fail_json(**error_result(f"gludd_human_todo {state} failed: {msg}", status=status_code))
        return
    module.exit_json(**ok_result({"human_todo": resp, "id": resp.get("id")}, changed=True))


if __name__ == "__main__":
    main()
