"""Unit test for the gludd_environment Ansible module.

Mirrors the gludd_facts module shape: loads the real shipped module via
importlib, drives main() with a mocked AnsibleModule + GluddClient, and asserts
it GETs ``/api/environment`` and injects the snapshot under
``ansible_facts.gludd_environment`` (read-only, never changed=True).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent.parent
MODULE_PATH = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
    / "plugins" / "modules" / "gludd_environment.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_environment", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAnsibleModule:
    """Stand-in for ansible.module_utils.basic.AnsibleModule."""

    def __init__(self, argument_spec: dict[str, Any], supports_check_mode: bool) -> None:
        self.argument_spec = argument_spec
        self.supports_check_mode = supports_check_mode
        self.params = {
            "daemon_url": "http://localhost:8000",
            "psk": "test-psk",
            "timeout": 30,
        }
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class _FakeClient:
    """Records the path GET-ed and returns a canned environment brief."""

    last_path: str | None = None

    def __init__(self, base_url: str, psk: str, timeout: int) -> None:
        self.base_url = base_url
        self.psk = psk
        self.timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        type(self).last_path = path
        return {
            "models": [{"profile_id": "flagship", "enabled": True}],
            "routing": {"default_profile": "flagship"},
            "budget": {"run_remaining_usd": None},
            "compute": {"providers": ["aws"], "gpu_types": ["h100"]},
            "tools": [{"name": "gludd_environment", "source": "ansible"}],
            "skills": [],
            "queues": [],
            "system": {"cpu_count": 8},
            "optimization": {"hints": [], "recommended_profile_for": {}},
            "_status": 200,
        }


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def test_module_importable_and_has_main(module: ModuleType) -> None:
    assert hasattr(module, "main")


def test_main_gets_api_environment_and_injects_facts(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    _FakeClient.last_path = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _FakeClient)

    module.main()

    # It hit the environment endpoint, not facts/metrics.
    assert _FakeClient.last_path == "/api/environment"

    # It injected the snapshot under ansible_facts.gludd_environment, read-only.
    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    assert fake_mod.exited["changed"] is False
    facts = fake_mod.exited["ansible_facts"]
    assert "gludd_environment" in facts
    env = facts["gludd_environment"]
    # Transport keys (leading underscore) are stripped from the snapshot.
    assert "_status" not in env
    assert env["routing"]["default_profile"] == "flagship"
    assert env["system"]["cpu_count"] == 8


def test_unauthorized_status_fails(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)

    class _UnauthClient(_FakeClient):
        def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            type(self).last_path = path
            return {"_status": 401}

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _UnauthClient)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert fake_mod.failed["status"] == 401
