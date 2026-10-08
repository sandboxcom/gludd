#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""Delegate a local-model lifecycle operation to the Gludd daemon."""

from __future__ import annotations

from typing import Any, Protocol

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)

DOCUMENTATION = r"""
---
module: gludd_local_model
short_description: Manage a daemon-owned local inference lifecycle
description:
  - Delegates download, serve, consume, and shutdown to the Gludd daemon.
  - Never installs packages or launches a model process inside the collection.
  - The daemon LocalInferenceManager owns process groups, diagnostics, and teardown.
options:
  action:
    type: str
    required: true
    choices: [download, serve, consume, shutdown]
  daemon_url:
    type: str
    default: http://127.0.0.1:8000
  psk:
    type: str
    no_log: true
    default: ""
  timeout:
    type: int
    default: 180
  model_id:
    type: str
    default: ""
  model_path:
    type: str
    default: ""
  filename:
    type: str
    default: ""
  source:
    type: str
    default: huggingface
  server_id:
    type: str
    default: ""
  prompt:
    type: str
    default: ""
  max_tokens:
    type: int
    default: 1024
  host:
    type: str
    default: 127.0.0.1
  port:
    type: int
    default: 9999
  startup_timeout:
    type: int
    default: 120
  gpu_layers:
    type: int
    default: 0
  context_size:
    type: int
    default: 2048
"""

EXAMPLES = r"""
- name: Start a daemon-owned local model
  general_ludd.agent.gludd_local_model:
    action: serve
    model_id: bartowski/Qwen2.5-0.5B-Instruct-GGUF
    model_path: /srv/models/qwen.gguf
    startup_timeout: 120
  register: local_model
"""

RETURN = r"""
server_id:
  description: Daemon-issued server identifier.
  returned: serve, consume, shutdown
  type: str
text:
  description: Generated text.
  returned: consume
  type: str
local_path:
  description: Downloaded model path.
  returned: download
  type: str
"""


class _Client(Protocol):
    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Post a JSON payload to one daemon path."""


_PATHS = {
    "download": "/admin/models/local/download",
    "serve": "/admin/models/local/serve",
    "consume": "/admin/models/local/consume",
    "shutdown": "/admin/models/local/shutdown",
}


def execute_action(
    client: _Client,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute one allowlisted daemon-owned local-model action."""
    try:
        path = _PATHS[action]
    except KeyError as exc:
        raise ValueError(f"unsupported local-model action: {action}") from exc
    return client.post(path, payload)


def _nonempty(params: dict[str, Any], *names: str) -> dict[str, Any]:
    """Return named values while omitting only empty strings and None."""
    return {
        name: params[name]
        for name in names
        if params.get(name) not in ("", None)
    }


def _payload(params: dict[str, Any]) -> dict[str, Any]:
    """Build the strict request payload for the selected action."""
    action = str(params["action"])
    if action == "download":
        return _nonempty(params, "model_id", "filename", "source")
    if action == "serve":
        return _nonempty(
            params,
            "model_id",
            "model_path",
            "host",
            "port",
            "startup_timeout",
            "gpu_layers",
            "context_size",
        )
    if action == "consume":
        return _nonempty(params, "server_id", "prompt", "max_tokens")
    if action == "shutdown":
        return _nonempty(params, "server_id")
    raise ValueError(f"unsupported local-model action: {action}")


def main() -> None:
    """Run the Ansible module."""
    module = AnsibleModule(
        argument_spec={
            "action": {
                "type": "str",
                "required": True,
                "choices": sorted(_PATHS),
            },
            "daemon_url": {"type": "str", "default": "http://127.0.0.1:8000"},
            "psk": {"type": "str", "default": "", "no_log": True},
            "timeout": {"type": "int", "default": 180},
            "model_id": {"type": "str", "default": ""},
            "model_path": {"type": "str", "default": ""},
            "filename": {"type": "str", "default": ""},
            "source": {"type": "str", "default": "huggingface"},
            "server_id": {"type": "str", "default": ""},
            "prompt": {"type": "str", "default": ""},
            "max_tokens": {"type": "int", "default": 1024},
            "host": {"type": "str", "default": "127.0.0.1"},
            "port": {"type": "int", "default": 9999},
            "startup_timeout": {"type": "int", "default": 120},
            "gpu_layers": {"type": "int", "default": 0},
            "context_size": {"type": "int", "default": 2048},
        },
        supports_check_mode=True,
    )
    action = str(module.params["action"])
    payload = _payload(module.params)
    if module.check_mode:
        module.exit_json(
            **ok_result(
                {"action": action, "request": payload, "check_mode": True},
                changed=False,
            )
        )
        return

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )
    response = execute_action(client, action, payload)
    if response.get("_error"):
        module.fail_json(
            **error_result(
                f"daemon local-model {action} failed: {response['_error']}"
            )
        )
        return

    status = response.get("_status", 0)
    if status not in (200, 201):
        detail = response.get("detail") or response.get("_raw") or f"HTTP {status}"
        module.fail_json(
            **error_result(
                f"daemon local-model {action} failed: {detail}",
                status=status,
            )
        )
        return

    result = {key: value for key, value in response.items() if key != "_status"}
    module.exit_json(**ok_result(result, changed=True))


if __name__ == "__main__":
    main()
