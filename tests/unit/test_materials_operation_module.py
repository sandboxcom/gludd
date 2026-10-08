"""Tests for the materials operation Ansible module transport contract."""

from __future__ import annotations

import runpy
import sys
from typing import Any

from ansible.module_utils import basic as ansible_basic
from ansible_collections.general_ludd.materials.plugins.modules import (
    materials_operation,
)


class FakeModule:
    def __init__(self, *, check_mode: bool = False, timeout: int = 12) -> None:
        self.params = {
            "operation": "material_select",
            "request": {"requirements": {}, "candidates": ["abs"]},
            "daemon_url": "http://daemon:8000",
            "psk": "secret",
            "timeout": timeout,
            "idempotency_key": "",
        }
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        return self.response


def test_module_calls_bounded_idempotent_endpoint(monkeypatch: Any) -> None:
    client = FakeClient({"_status": 200, "verdict": "candidate"})
    monkeypatch.setattr(materials_operation, "GluddClient", lambda **_: client)
    module = FakeModule()

    materials_operation.run(module)

    assert module.failed is None
    assert module.exited == {
        "changed": False,
        "operation": "material_select",
        "result": {"verdict": "candidate"},
    }
    path, body = client.posts[0]
    assert path == "/api/materials/resolve"
    assert body["idempotency_key"].startswith("materials:")
    assert body["timeout_seconds"] == 12.0


def test_module_check_mode_returns_exact_plan_without_network(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        materials_operation,
        "GluddClient",
        lambda **_: (_ for _ in ()).throw(AssertionError("network used")),
    )
    module = FakeModule(check_mode=True)

    materials_operation.run(module)

    assert module.failed is None
    assert module.exited is not None
    assert module.exited["changed"] is False
    assert module.exited["result"]["planned"] is True
    assert module.exited["result"]["request"] == module.params["request"]


def test_module_propagates_http_failure_and_rejects_unbounded_timeout(monkeypatch: Any) -> None:
    invalid = FakeModule(timeout=31)
    materials_operation.run(invalid)
    assert invalid.failed is not None
    assert "timeout" in invalid.failed["msg"]

    client = FakeClient({"_status": 422, "detail": "invalid material input"})
    monkeypatch.setattr(materials_operation, "GluddClient", lambda **_: client)
    failed = FakeModule()
    materials_operation.run(failed)
    assert failed.failed is not None
    assert failed.failed["status"] == 422


def test_typed_operation_module_entrypoints_are_check_mode_safe(monkeypatch: Any) -> None:
    """Exercise both executable entrypoints without making a daemon request."""
    captured_specs: list[dict[str, Any]] = []

    class EntrypointModule(FakeModule):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(check_mode=True)
            captured_specs.append(kwargs["argument_spec"])

    monkeypatch.setattr(ansible_basic, "AnsibleModule", EntrypointModule)
    module_names = (
        "ansible_collections.general_ludd.materials.plugins.modules.materials_operation",
        "ansible_collections.general_ludd.chemistry.plugins.modules.chemistry_operation",
    )

    for module_name in module_names:
        monkeypatch.delitem(sys.modules, module_name, raising=False)
        runpy.run_module(module_name, run_name="__main__")

    assert len(captured_specs) == 2
    assert all(spec["operation"]["choices"] for spec in captured_specs)
    assert all(spec["psk"]["no_log"] is True for spec in captured_specs)
