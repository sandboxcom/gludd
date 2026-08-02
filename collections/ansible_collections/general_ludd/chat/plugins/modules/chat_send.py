"""Ansible module: send a chat message through the gludd daemon.

Wraps ``POST /api/chat/completions/sync``.
"""

from __future__ import annotations

import httpx
from ansible.module_utils.basic import AnsibleModule


def run_module() -> None:
    module_args = {
        "messages": {"type": "list", "elements": "dict", "required": True},
        "daemon_url": {"type": "str", "default": "http://localhost:8000"},
        "model_profile_id": {"type": "str", "default": "default"},
        "temperature": {"type": "float", "required": False, "default": None},
        "max_tokens": {"type": "int", "required": False, "default": None},
        "stream": {"type": "bool", "default": False},
    }
    module = AnsibleModule(argument_spec=module_args)

    messages = module.params["messages"]
    daemon_url = module.params["daemon_url"]
    model_profile_id = module.params["model_profile_id"]
    temperature = module.params["temperature"]
    max_tokens = module.params["max_tokens"]
    stream = module.params["stream"]

    payload: dict[str, object] = {
        "messages": messages,
        "model_profile_id": model_profile_id,
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    endpoint = "/api/chat/completions/sync"
    url = f"{daemon_url.rstrip('/')}{endpoint}"

    try:
        resp = httpx.post(url, json=payload, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        module.exit_json(
            changed=True,
            response=str(data.get("response", "") or ""),
            model_profile_id=str(data.get("model_profile_id", model_profile_id)),
        )
    except httpx.ConnectError:
        module.fail_json(msg=f"Could not connect to daemon at {daemon_url}")
    except httpx.HTTPStatusError as exc:
        module.fail_json(msg=f"Daemon returned {exc.response.status_code}: {exc.response.text[:500]}")
    except Exception as exc:
        module.fail_json(msg=str(exc))


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
