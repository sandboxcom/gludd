"""Compatibility contract for the explicit hook-runtime facade split."""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import runpy
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FACADE = ROOT / "scripts" / "test_hook_runtime.py"
PACKAGE = ROOT / "scripts" / "hook_runtime"
MANIFEST = ROOT / "config" / "hook_runtime_case_inventory.json"
MAX_COMPONENT_LINES = 2_000


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _function_records(path: Path) -> list[dict[str, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    records: list[dict[str, object]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        records.append(
            {
                "name": node.name,
                "arguments_ast": ast.dump(node.args, include_attributes=False),
                "returns_ast": ast.dump(node.returns, include_attributes=False) if node.returns else None,
                "decorators_ast": [
                    ast.dump(item, include_attributes=False)
                    for item in node.decorator_list
                ],
            }
        )
    return records


def _case_modules() -> list[tuple[str, Path]]:
    return [
        (name, PACKAGE / f"{name}.py")
        for name in _manifest()["case_modules"]
    ]


def test_split_modules_exist_and_leave_maintenance_headroom() -> None:
    """The facade and each cohesive component stay well below the hard gate."""
    expected = [
        PACKAGE / "__init__.py",
        PACKAGE / "fixtures.py",
        PACKAGE / "runner.py",
        *[path for _, path in _case_modules()],
    ]
    assert all(path.is_file() for path in expected)
    for path in [FACADE, *expected]:
        assert len(path.read_text(encoding="utf-8").splitlines()) < MAX_COMPONENT_LINES, path


def test_case_inventory_order_signatures_and_marks_match_pre_split_snapshot() -> None:
    """Extraction cannot rename, reorder, resignature, or unmark a runtime case."""
    actual: list[dict[str, object]] = []
    for _, path in _case_modules():
        actual.extend(
            record
            for record in _function_records(path)
            if str(record["name"]).startswith("test_")
        )
    assert actual == _manifest()["cases"]


def test_helper_signatures_match_pre_split_snapshot() -> None:
    """Shared fixture, runner, and lifecycle helpers retain their AST API."""
    paths = [
        PACKAGE / "fixtures.py",
        PACKAGE / "runner.py",
        PACKAGE / "cases_lifecycle.py",
    ]
    actual_by_name = {
        record["name"]: record
        for path in paths
        for record in _function_records(path)
    }
    expected = _manifest()["helpers"]
    assert [actual_by_name[item["name"]] for item in expected] == expected


def test_facade_reexports_every_case_explicitly_in_original_order() -> None:
    """Explicit aliases preserve historical pytest node names and order."""
    tree = ast.parse(FACADE.read_text(encoding="utf-8"))
    assignments = [
        node.targets[0].id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id.startswith("test_")
    ]
    expected = [item["name"] for item in _manifest()["cases"]]
    assert assignments == expected

    bridge_tree = ast.parse(
        (PACKAGE / "facade_exports.py").read_text(encoding="utf-8")
    )
    imported_cases = {
        alias.name
        for node in bridge_tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("cases_")
        for alias in node.names
        if alias.name.startswith("test_")
    }
    assert imported_cases == set(expected)


def test_facade_case_objects_are_component_identities_with_signature_parity() -> None:
    """Callers receive the exact extracted function objects, never wrappers."""
    facade = importlib.import_module("scripts.test_hook_runtime")
    for module_name, _ in _case_modules():
        component = importlib.import_module(f"hook_runtime.{module_name}")
        for record in _function_records(PACKAGE / f"{module_name}.py"):
            name = str(record["name"])
            if not name.startswith("test_"):
                continue
            facade_case = getattr(facade, name)
            component_case = getattr(component, name)
            assert facade_case is component_case
            assert inspect.signature(facade_case) == inspect.signature(component_case)


def test_components_never_import_the_facade() -> None:
    """The extracted dependency graph points only toward fixtures and runner."""
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert "scripts.test_hook_runtime" not in imported, path


def test_facade_helper_imports_and_cli_remain_compatible() -> None:
    """Established direct imports and the executable pytest entrypoint remain."""
    facade: ModuleType = importlib.import_module("scripts.test_hook_runtime")
    fixtures = importlib.import_module("hook_runtime.fixtures")
    runner = importlib.import_module("hook_runtime.runner")
    lifecycle = importlib.import_module("hook_runtime.cases_lifecycle")

    assert facade.ROOT == ROOT
    assert fixtures.ROOT == ROOT
    assert facade.PLUGIN_DIR == ROOT / ".opencode" / "plugin"

    owners = {
        "_runtime_state_root": fixtures,
        "_runtime_state_path": fixtures,
        "_dirty_test_path": fixtures,
        "_remove_legacy_workspace_artifacts": fixtures,
        "_cleanup_legacy_workspace_artifacts": fixtures,
        "_clean_state_files": fixtures,
        "_with_open_work": fixtures,
        "_hermetic_project_root": fixtures,
        "_run_ts": runner,
        "_factory_plugin_code": runner,
        "_pluginapi_code": runner,
        "_fresh_session_state": lifecycle,
        "_session_start_dispatch_then_bash": lifecycle,
        "_enforce_make_bash_test": lifecycle,
    }
    for name, owner in owners.items():
        assert getattr(facade, name) is getattr(owner, name)

    source = FACADE.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert 'pytest.main([__file__, "-v", *sys.argv[1:]])' in source


def test_allow_results_cover_clean_tree_compatibility_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowed hook results retain the permissive side of conditional assertions."""
    core = importlib.import_module("hook_runtime.cases_core")
    safety = importlib.import_module("hook_runtime.cases_safety")

    monkeypatch.setattr(core, "_run_ts", lambda *_args, **_kwargs: None)
    core.test_clean_tree_hook_clean_tree_allows_dispatch()

    monkeypatch.setattr(safety, "_run_ts", lambda *_args, **_kwargs: None)
    safety.test_clean_tree_dispatch_allowed()
    monkeypatch.setattr(
        safety,
        "_run_ts",
        lambda *_args, **_kwargs: {"allowed": True},
    )
    safety.test_clean_tree_hook_throws_on_execsync_failure()


def test_facade_main_preserves_direct_cli_exit_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct execution forwards the historical argv and pytest exit status."""
    calls: list[list[str]] = []

    def fake_pytest_main(args: list[str]) -> int:
        calls.append(args)
        return 17

    monkeypatch.setattr(pytest, "main", fake_pytest_main)
    monkeypatch.setattr(sys, "argv", [str(FACADE)])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(FACADE), run_name="__main__")

    assert raised.value.code == 17
    assert calls == [[str(FACADE), "-v"]]
