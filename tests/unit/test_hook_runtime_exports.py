"""Behavioral contracts for one authoritative hook-runtime export registry."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_export_registry_preserves_every_case_in_inventory_order() -> None:
    """The compatibility facade binds each owned case once without wrappers."""
    facade = importlib.import_module("scripts.test_hook_runtime")
    exports = importlib.import_module("hook_runtime.facade_exports")
    manifest = json.loads((ROOT / "config/hook_runtime_case_inventory.json").read_text())
    expected = [record["name"] for record in manifest["cases"]]
    assert list(exports._CASE_EXPORTS) == expected
    assert [name for name in vars(facade) if name.startswith("test_")] == expected
    assert all(getattr(facade, name) is exports._CASE_EXPORTS[name] for name in expected)
    assert set(exports.__all__) >= set(expected)
    assert exports.__all__ == sorted(set(exports.__all__))


def test_case_registry_uses_only_owned_functions_and_retains_definition_order() -> None:
    """Imported cases, callable objects, helpers and constants are not new cases."""
    exports = importlib.import_module("hook_runtime.facade_exports")
    component = ModuleType("gludd_export_component")
    exec("def test_second(): pass\ndef test_first(): pass\ndef _helper(): pass\n", component.__dict__)
    foreign = ModuleType("gludd_export_foreign")
    exec("def test_imported(): pass\n", foreign.__dict__)
    vars(component).update(test_imported=vars(foreign)["test_imported"],
                           test_constant="not a function", test_callable=len)
    actual = exports._case_exports((component,))
    assert list(actual) == ["test_second", "test_first"]
    assert actual["test_second"] is component.test_second
    assert actual["test_first"] is component.test_first


def test_case_registry_rejects_duplicate_owned_names() -> None:
    """Conflicting owners must fail before one case silently replaces another."""
    exports = importlib.import_module("hook_runtime.facade_exports")
    first = ModuleType("gludd_export_first")
    second = ModuleType("gludd_export_second")
    for component in (first, second):
        exec("def test_conflict(): pass\n", component.__dict__)
    with pytest.raises(ValueError, match="duplicate hook-runtime case: test_conflict"):
        exports._case_exports((first, second))
