"""Structural tests for the gludd_human_todo Ansible module.

Mirrors the structural checks in tests/integration/test_playbook_registry.py
(TestModuleSecurityProperties): DOCUMENTATION/EXAMPLES/RETURN present,
argument_spec complete, psk marked no_log.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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


def test_module_file_exists():
    assert MODULE_PATH.exists(), f"module file missing: {MODULE_PATH}"


def test_has_documentation_block(module_source: str):
    assert "DOCUMENTATION:" in module_source
    assert "module: gludd_human_todo" in module_source


def test_has_examples_block(module_source: str):
    assert "EXAMPLES:" in module_source
    assert "gludd_human_todo" in module_source


def test_has_return_block(module_source: str):
    assert "RETURN:" in module_source
    assert "human_todo" in module_source


def test_psk_is_no_log(module_source: str):
    """The psk parameter must be marked no_log=True (never leaked)."""
    assert 'psk=dict(type="str", default="", no_log=True)' in module_source, (
        "gludd_human_todo: psk parameter must have no_log=True"
    )


def test_argument_spec_has_required_choices(module_source: str):
    assert 'choices=["present", "done", "dismissed"]' in module_source
    assert "permission_escalation" in module_source
    assert "external_action" in module_source
    assert "decision" in module_source
    assert "input_request" in module_source
    assert "blocker" in module_source


def test_argument_spec_required_if(module_source: str):
    assert "required_if=" in module_source
    assert '"state", "present"' in module_source
    assert '"state", "done"' in module_source
    assert '"state", "dismissed"' in module_source


def test_supports_check_mode(module_source: str):
    assert "supports_check_mode=True" in module_source


def test_module_importable():
    """The module file must be syntactically valid Python (importable)."""
    spec = importlib.util.spec_from_file_location("gludd_human_todo_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    # importlib will execute the module; main() is only called under __main__ guard.
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")
