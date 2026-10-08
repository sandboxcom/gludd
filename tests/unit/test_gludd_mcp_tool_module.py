"""Behavioral contracts for the live MCP Ansible module."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_mcp_tool.py"
)


class _FakeAnsibleModule:
    def __init__(self, params: dict[str, Any], *, check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class _FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        return self.response


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_mcp_tool", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "server": "filesystem",
        "tool": "read_file",
        "arguments": {"path": "/workspace/file.txt"},
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
        "timeout": 30,
    }
    params.update(overrides)
    return params


def _run(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
    *,
    check_mode: bool = False,
) -> tuple[_FakeAnsibleModule, _FakeClient]:
    module = _load_module()
    ansible = _FakeAnsibleModule(_params(), check_mode=check_mode)
    client = _FakeClient(response)
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: ansible)
    monkeypatch.setattr(module, "GluddClient", lambda **_: client)
    module.main()
    return ansible, client


def test_live_call_uses_bounded_authenticated_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "_status": 200,
        "results": [
            {
                "ok": True,
                "kind": "mcp",
                "name": "filesystem/read_file",
                "output": {"content": "hello"},
                "error": None,
            }
        ],
        "count": 1,
        "ok_count": 1,
        "error_count": 0,
    }

    ansible, client = _run(monkeypatch, response)

    assert ansible.failed is None
    assert ansible.exited == {
        "changed": False,
        "failed": False,
        "result": {"content": "hello"},
        "server": "filesystem",
        "tool": "read_file",
    }
    assert client.posts == [
        (
            "/api/dispatch",
            {
                "kind": "mcp",
                "name": "filesystem/read_file",
                "args": {"path": "/workspace/file.txt"},
            },
        )
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"_status": 503, "detail": "offline"}, "MCP dispatch failed: offline"),
        (
            {
                "_status": 200,
                "results": [
                    {
                        "ok": False,
                        "kind": "mcp",
                        "name": "filesystem/read_file",
                        "output": None,
                        "error": "handler_error",
                    }
                ],
            },
            "MCP dispatch failed: handler_error",
        ),
        ({"_status": 200, "results": []}, "MCP dispatch returned an invalid response"),
    ],
)
def test_failures_propagate_without_false_success(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
    message: str,
) -> None:
    ansible, _ = _run(monkeypatch, response)

    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["msg"] == message
    assert ansible.failed["changed"] is False


def test_check_mode_returns_plan_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ansible, client = _run(monkeypatch, {"_status": 500}, check_mode=True)

    assert ansible.failed is None
    assert ansible.exited == {
        "changed": False,
        "failed": False,
        "check_mode": True,
        "planned_call": {
            "kind": "mcp",
            "name": "filesystem/read_file",
            "args": {"path": "/workspace/file.txt"},
        },
        "result": {},
        "server": "filesystem",
        "tool": "read_file",
    }
    assert client.posts == []
