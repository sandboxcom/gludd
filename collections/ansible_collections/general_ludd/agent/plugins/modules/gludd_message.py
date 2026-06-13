#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_message
  short_description: Inter-agent message queue — send, receive, or ack messages via the daemon
  description:
    - Talks to the daemon message-queue API so agents/roles can coordinate.
    - C(state=send) posts a message to a recipient role/agent (or C(broadcast)).
    - C(state=receive) fetches the inbox for C(recipient); messages are returned
      both as C(ansible_facts.gludd_inbox) and a C(messages) list. Pass
      C(ack=true) to mark every received message read in the same task.
    - C(state=ack) marks a single C(message_id) read.
    - Check mode skips the write side of C(send)/C(ack); C(receive) is always safe.
  options:
    state:
      description: Operation to perform.
      type: str
      required: true
      choices: [send, receive, ack]
    sender:
      description: Sender role/agent name (required for send).
      type: str
    recipient:
      description: Recipient role/agent name, or C(broadcast) (send/receive).
      type: str
    topic:
      description: Message topic (send).
      type: str
      default: ""
    body:
      description: Message body (send). May carry sensitive content — not logged.
      type: str
      default: ""
      no_log: true
    priority:
      description: Message priority (send).
      type: str
      default: normal
      choices: [low, normal, high, urgent]
    ttl_seconds:
      description: Optional time-to-live in seconds (send); expired messages are purged.
      type: int
    message_id:
      description: Message id to ack (required for ack).
      type: str
    unread:
      description: When receiving, only return unread messages.
      type: bool
      default: true
    ack:
      description: When receiving, also mark each returned message read.
      type: bool
      default: false
    project_id:
      description: Optional project scope.
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
  - name: Send a message to the reviewer role
    general_ludd.agent.gludd_message:
      state: send
      sender: coder
      recipient: reviewer
      topic: ready-for-review
      body: "PR #42 is ready"

  - name: Broadcast to everyone
    general_ludd.agent.gludd_message:
      state: send
      sender: planner
      recipient: broadcast
      topic: standup

  - name: Receive and ack my inbox
    general_ludd.agent.gludd_message:
      state: receive
      recipient: coder
      ack: true
    register: mail

  - name: Ack one message
    general_ludd.agent.gludd_message:
      state: ack
      message_id: "MSG-ABC123"

RETURN:
  message:
    description: The created message (send).
    type: dict
    returned: when state=send
  messages:
    description: List of inbox messages (receive).
    type: list
    returned: when state=receive
  ansible_facts:
    description: Contains C(gludd_inbox) with the received messages (receive).
    type: dict
    returned: when state=receive
  acked:
    description: Whether the message was acked (ack, or receive with ack=true).
    type: bool
    returned: when state=ack or state=receive
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


def _check_status(module, resp, label, ok_codes=(200, 201)):
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return False
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return False
    if status_code == 404:
        module.fail_json(**error_result(f"{label}: not found", status=404))
        return False
    if status_code not in ok_codes:
        msg = resp.get("detail") or f"HTTP {status_code}"
        module.fail_json(**error_result(f"{label} failed: {msg}", status=status_code))
        return False
    return True


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(type="str", required=True, choices=["send", "receive", "ack"]),
            sender=dict(type="str", default=None),
            recipient=dict(type="str", default=None),
            topic=dict(type="str", default=""),
            body=dict(type="str", default="", no_log=True),
            priority=dict(type="str", default="normal", choices=["low", "normal", "high", "urgent"]),
            ttl_seconds=dict(type="int", default=None),
            message_id=dict(type="str", default=None),
            unread=dict(type="bool", default=True),
            ack=dict(type="bool", default=False),
            project_id=dict(type="str", default=None),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        required_if=[
            ("state", "send", ["sender", "recipient"]),
            ("state", "receive", ["recipient"]),
            ("state", "ack", ["message_id"]),
        ],
        supports_check_mode=True,
    )

    state: str = module.params["state"]
    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    if state == "send":
        payload = {
            "sender": module.params["sender"],
            "recipient": module.params["recipient"],
            "topic": module.params["topic"],
            "body": module.params["body"],
            "priority": module.params["priority"],
        }
        if module.params["ttl_seconds"] is not None:
            payload["ttl_seconds"] = module.params["ttl_seconds"]
        if module.params["project_id"]:
            payload["project_id"] = module.params["project_id"]
        if module.check_mode:
            module.exit_json(**ok_result({"message": payload, "msg": "check_mode: not sent"}, changed=True))
            return
        resp = client.post("/api/messages", payload)
        if not _check_status(module, resp, "send", ok_codes=(200, 201)):
            return
        msg = {k: v for k, v in resp.items() if not k.startswith("_")}
        module.exit_json(**ok_result({"message": msg}, changed=True))

    elif state == "receive":
        params = {
            "recipient": module.params["recipient"],
            "unread": module.params["unread"],
        }
        if module.params["project_id"]:
            params["project_id"] = module.params["project_id"]
        resp = client.get("/api/messages", params=params)
        if not _check_status(module, resp, "receive", ok_codes=(200,)):
            return
        messages = resp.get("messages", [])
        changed = False
        if module.params["ack"] and not module.check_mode:
            for m in messages:
                mid = m.get("id")
                if mid:
                    ack_resp = client.post(f"/api/messages/{mid}/ack")
                    if ack_resp.get("_status", 0) in (200, 201):
                        changed = True
        module.exit_json(
            **ok_result(
                {
                    "messages": messages,
                    "ansible_facts": {"gludd_inbox": messages},
                    "acked": changed,
                },
                changed=changed,
            )
        )

    elif state == "ack":
        message_id: str = module.params["message_id"]
        if module.check_mode:
            module.exit_json(**ok_result({"acked": True, "message_id": message_id}, changed=True))
            return
        resp = client.post(f"/api/messages/{message_id}/ack")
        if not _check_status(module, resp, "ack", ok_codes=(200, 201)):
            return
        module.exit_json(**ok_result({"acked": True, "message_id": message_id}, changed=True))


if __name__ == "__main__":
    main()
