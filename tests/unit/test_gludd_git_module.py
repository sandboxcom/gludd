"""HTTP compatibility tests for the daemon-backed ``gludd_git`` module."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_git.py"
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
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "_status": 200,
            "result": {"sha": "abc1234", "success": True},
            "changed": True,
        }
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.init: dict[str, Any] = {}

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        return self.response


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_git", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _params(module: ModuleType, **overrides: Any) -> dict[str, Any]:
    params = {
        name: option.get("default")
        for name, option in module._argument_spec().items()
    }
    params.update(
        {
            "path": "/repo",
            "op": "commit",
            "daemon_url": "http://daemon:8000",
            "psk": "secret",
            "timeout": 45,
        }
    )
    params.update(overrides)
    return params


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: dict[str, Any] | None = None,
    check_mode: bool = False,
    **overrides: Any,
) -> tuple[_FakeAnsibleModule, _FakeClient, ModuleType]:
    module = _load_module()
    ansible = _FakeAnsibleModule(_params(module, **overrides), check_mode=check_mode)
    client = _FakeClient(response)

    def _client_factory(**kwargs: Any) -> _FakeClient:
        client.init = kwargs
        return client

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: ansible)
    monkeypatch.setattr(module, "GluddClient", _client_factory)
    module.main()
    return ansible, client, module


def test_commit_posts_typed_request_and_preserves_result_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ansible, client, _ = _run(
        monkeypatch,
        message="boundary migration",
        files=["src/app.py"],
        idempotency_key="commit:boundary",
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["changed"] is True
    assert ansible.exited["sha"] == "abc1234"
    assert ansible.exited["result"] == {"sha": "abc1234", "success": True}
    assert client.init == {"base_url": "http://daemon:8000", "psk": "secret", "timeout": 45}
    assert client.posts[0][0] == "/admin/git/operation"
    assert client.posts[0][1]["op"] == "commit"
    assert client.posts[0][1]["idempotency_key"] == "commit:boundary"
    assert "psk" not in client.posts[0][1]


def test_read_only_operation_runs_in_check_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {"_status": 200, "result": {"branch": "development"}, "changed": False}
    ansible, client, _ = _run(
        monkeypatch,
        response=response,
        check_mode=True,
        op="current_branch",
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["changed"] is False
    assert ansible.exited["branch"] == "development"
    assert len(client.posts) == 1


def test_mutation_check_mode_has_no_http_side_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    ansible, client, _ = _run(
        monkeypatch,
        check_mode=True,
        op="push",
        branch="development",
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["result"]["would_change"] is True
    assert ansible.exited["changed"] is True
    assert client.posts == []


@pytest.mark.parametrize("op", ["gated_commit", "gated_merge"])
def test_gated_operations_require_make_argv_before_http(
    monkeypatch: pytest.MonkeyPatch,
    op: str,
) -> None:
    ansible, client, _ = _run(monkeypatch, op=op, gate_cmd=[])

    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["msg"] == f"{op} requires non-empty gate_cmd"
    assert client.posts == []


@pytest.mark.parametrize(
    "response",
    [
        {"_status": 401, "detail": "unauthorized"},
        {"_status": 0, "_error": "connection refused"},
        {"_status": 503, "detail": "git service unavailable"},
    ],
)
def test_http_failures_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
) -> None:
    ansible, _, _ = _run(monkeypatch, response=response, message="x")

    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["changed"] is False
    assert ansible.failed["status"] == response["_status"]


def test_server_changed_flag_controls_compatibility_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "_status": 200,
        "result": {"already_present": True, "branch": "feature/x"},
        "changed": False,
    }
    ansible, _, _ = _run(
        monkeypatch,
        response=response,
        op="branch",
        branch="feature/x",
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["changed"] is False
    assert ansible.exited["branch"] == "feature/x"


def test_module_has_complete_allowlist_and_no_core_import() -> None:
    module = _load_module()
    choices = set(module._argument_spec()["op"]["choices"])

    assert {"commit", "worktree_create", "state", "release_cut", "ci_cancel"} <= choices
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "from general_ludd" not in source
    assert "import general_ludd" not in source
