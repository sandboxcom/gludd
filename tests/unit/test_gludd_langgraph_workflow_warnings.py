"""Behavioral contract for non-deprecated workflow warnings."""

from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
    / "gludd_langgraph_workflow.py"
)


class _Unwind(Exception):
    pass


class FakeAnsibleModule:
    _provided: ClassVar[dict[str, Any]] = {}

    def __init__(self, *, argument_spec: dict, **_: Any) -> None:
        self.params = {
            name: option.get("default") for name, option in argument_spec.items()
        }
        self.params.update(self._provided)
        self.check_mode = False
        self.result: dict[str, Any] | None = None
        self.emitted_warnings: list[str] = []

    def warn(self, warning: str) -> None:
        self.emitted_warnings.append(warning)

    def exit_json(self, **kwargs: Any) -> None:
        self.result = kwargs
        raise _Unwind()

    def fail_json(self, **kwargs: Any) -> None:
        self.result = kwargs
        raise _Unwind()


class FakeClient:
    response: ClassVar[dict[str, Any]] = {}

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def post(self, *_: Any, **__: Any) -> dict[str, Any]:
        return dict(self.response)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_gludd_langgraph_workflow_under_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_warnings_use_ansible_warning_api_and_non_reserved_result_key() -> None:
    module = _load_module()
    FakeAnsibleModule._provided = {
        "prompt": "draft a plan",
        "daemon_url": "http://localhost:8000",
        "psk": "",
        "timeout": 5,
    }
    FakeClient.response = {
        "_status": 200,
        "content": "plan",
        "warnings": ["quality threshold not reached"],
    }
    captured: dict[str, FakeAnsibleModule] = {}

    def factory(**kwargs: Any) -> FakeAnsibleModule:
        instance = FakeAnsibleModule(**kwargs)
        captured["module"] = instance
        return instance

    module.AnsibleModule = factory
    module.GluddClient = FakeClient

    with contextlib.suppress(_Unwind):
        module.main()

    instance = captured["module"]
    assert instance.emitted_warnings == ["quality threshold not reached"]
    assert instance.result is not None
    assert instance.result["workflow_warnings"] == ["quality threshold not reached"]
    assert "warnings" not in instance.result
