"""Structural and behavioral tests for the gludd_human_todo Ansible module.

Mirrors the structural checks in tests/integration/test_playbook_registry.py
(TestModuleSecurityProperties): DOCUMENTATION/EXAMPLES/RETURN present,
argument_spec complete, psk marked no_log.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import cast

import pytest

from tests.unit.test_agent_runtime_modules_http import _FakeClient, _run

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
    / "gludd_human_todo.py"
)


@pytest.fixture(scope="module")
def module_source() -> str:
    return MODULE_PATH.read_text()


def test_module_file_exists() -> None:
    assert MODULE_PATH.exists(), f"module file missing: {MODULE_PATH}"


def test_has_documentation_block(module_source: str) -> None:
    assert "DOCUMENTATION:" in module_source
    assert "module: gludd_human_todo" in module_source


def test_has_examples_block(module_source: str) -> None:
    assert "EXAMPLES:" in module_source
    assert "gludd_human_todo" in module_source


def test_has_return_block(module_source: str) -> None:
    assert "RETURN:" in module_source
    assert "human_todo" in module_source


def test_psk_is_no_log(module_source: str) -> None:
    """The psk parameter must be marked no_log=True (never leaked)."""
    assert 'psk=dict(type="str", default="", no_log=True)' in module_source, (
        "gludd_human_todo: psk parameter must have no_log=True"
    )


def test_argument_spec_has_required_choices(module_source: str) -> None:
    assert 'choices=["present", "done", "dismissed"]' in module_source
    assert "permission_escalation" in module_source
    assert "external_action" in module_source
    assert "decision" in module_source
    assert "input_request" in module_source
    assert "blocker" in module_source


def test_argument_spec_required_if(module_source: str) -> None:
    assert "required_if=" in module_source
    assert '"state", "present"' in module_source
    assert '"state", "done"' in module_source
    assert '"state", "dismissed"' in module_source


def test_supports_check_mode(module_source: str) -> None:
    assert "supports_check_mode=True" in module_source


def test_module_importable() -> None:
    """The module file must be syntactically valid Python (importable)."""
    spec = importlib.util.spec_from_file_location("gludd_human_todo_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    # importlib will execute the module; main() is only called under __main__ guard.
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")


def _execute(
    monkeypatch: pytest.MonkeyPatch, state: str, response: dict[str, object], *,
    check_mode: bool = False, parent: bool = False,
) -> tuple[dict[str, object] | None, dict[str, object] | None, _FakeClient]:
    """Reuse the existing canonical module/HTTP adapter fixture, with PATCH recording."""
    def patch(client: _FakeClient, path: str, body: dict[str, object]) -> dict[str, object]:
        client.posts.append(("PATCH " + path, body))
        return cast(dict[str, object], client.response)

    monkeypatch.setattr(_FakeClient, "patch", patch, raising=False)
    params: dict[str, object] = {
        "state": state, "title": "Fixture request", "body": "No real request",
        "category": "blocker", "priority": "high", "tags": ["fixture"],
        "parent_agent_todo_id": "TODO-parent" if parent else None,
        "id": "HTODO-target", "human_resolution": "Operator action",
        "reason": "Alternative selected", "human_resolver": "operator",
        "agent_id": "gludd-fixture", "daemon_url": "http://gludd.invalid",
        "psk": "request-test-token", "timeout": 5,
    }
    module, client = _run(monkeypatch, "gludd_human_todo", params, response, check_mode=check_mode)
    assert client.init == {"base_url": "http://gludd.invalid", "psk": "request-test-token", "timeout": 5}
    return (
        cast(dict[str, object] | None, module.exited),
        cast(dict[str, object] | None, module.failed),
        client,
    )


INVALID_RESPONSE_IDS: dict[str, object] = {
    "null": None, "empty": "", "whitespace": " \t\n", "surrounding": " HTODO-target ",
    "integer": 17, "boolean": True, "list": ["HTODO-target"], "mapping": {"id": "HTODO-target"},
}


@pytest.mark.parametrize("native_status", [200, 201])
@pytest.mark.parametrize("state", ["present", "done", "dismissed"])
@pytest.mark.parametrize("response_kind", ["missing", *INVALID_RESPONSE_IDS])
def test_success_without_usable_id_never_verifies_write(
    monkeypatch: pytest.MonkeyPatch, native_status: int, state: str, response_kind: str,
) -> None:
    response: dict[str, object] = {"_status": native_status}
    if response_kind != "missing":
        response["id"] = INVALID_RESPONSE_IDS[response_kind]
    exited, failed, client = _execute(monkeypatch, state, response)
    assert exited is None
    assert failed is not None
    assert failed["failed"] is True and failed["status"] == native_status
    assert failed["write_outcome"] == "unverified"
    assert "id" not in failed
    message = str(failed["msg"])
    assert "HumanTodo id" in message and "reconcile" in message
    assert "request-test-token" not in message
    assert len(client.posts) == 1


@pytest.mark.parametrize("native_status", [200, 201])
@pytest.mark.parametrize("state", ["done", "dismissed"])
def test_resolution_response_must_match_requested_identity(
    monkeypatch: pytest.MonkeyPatch, native_status: int, state: str,
) -> None:
    exited, failed, client = _execute(monkeypatch, state, {"_status": native_status, "id": "HTODO-other"})
    assert exited is None and failed is not None
    assert failed["write_outcome"] == "unverified"
    assert failed["status"] == native_status
    assert "does not match" in str(failed["msg"])
    assert "reconcile" in str(failed["msg"])
    assert len(client.posts) == 1


@pytest.mark.parametrize("native_status", [200, 201])
@pytest.mark.parametrize("state", ["present", "done", "dismissed"])
def test_valid_native_response_preserves_identity_payload_and_request(
    monkeypatch: pytest.MonkeyPatch, native_status: int, state: str,
) -> None:
    response: dict[str, object] = {
        "_status": native_status, "id": "HTODO-target", "status": "open" if state == "present" else state,
    }
    exited, failed, client = _execute(monkeypatch, state, response)
    assert failed is None
    assert exited == {"failed": False, "changed": True, "human_todo": response, "id": "HTODO-target"}
    assert len(client.posts) == 1
    path, body = client.posts[0]
    if state == "present":
        assert path == "/api/human-todos"
        assert body == {
            "agent_id": "gludd-fixture", "title": "Fixture request", "body": "No real request",
            "category": "blocker", "priority": "high", "tags": ["fixture"],
        }
    else:
        assert path == "PATCH /api/human-todos/HTODO-target"
        assert body == {
            "status": state, "human_resolver": "operator",
            "human_resolution": "Operator action" if state == "done" else "Alternative selected",
        }


@pytest.mark.parametrize("state", ["present", "done", "dismissed"])
@pytest.mark.parametrize("parent", [False, True])
def test_check_mode_keeps_no_request_and_no_filed_id(
    monkeypatch: pytest.MonkeyPatch, state: str, parent: bool,
) -> None:
    exited, failed, client = _execute(monkeypatch, state, {}, check_mode=True, parent=parent)
    assert failed is None and exited is not None
    assert exited["changed"] is True and client.posts == []
    if state == "present":
        assert exited["id"] is None
        payload = exited["human_todo"]
        assert isinstance(payload, dict)
        assert payload.get("parent_agent_todo_id") == ("TODO-parent" if parent else None)
    else:
        assert exited["id"] == "HTODO-target" and exited["status"] == state


@pytest.mark.parametrize("state", ["present", "done", "dismissed"])
@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"_error": "connection refused"}, "daemon error: connection refused"),
        ({"_status": 401, "detail": "authentication required"}, "authentication required"),
        ({"_status": 500}, "HTTP 500"),
        ({}, "HTTP 0"),
    ],
)
def test_original_transport_and_http_failures_still_win(
    monkeypatch: pytest.MonkeyPatch, state: str, response: dict[str, object], reason: str,
) -> None:
    exited, failed, client = _execute(monkeypatch, state, response)
    assert exited is None and failed is not None
    assert reason in str(failed["msg"])
    assert "write_outcome" not in failed and "id" not in failed
    assert len(client.posts) == 1
