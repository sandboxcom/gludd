"""Tests for the exact collection Python-boundary migration inventory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.check_collection_python_boundary import (
    Finding,
    load_inventory,
    main,
    scan_collections,
    validate_inventory,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scanner_finds_core_import_path_mutation_and_ambient_python(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    _write(root / "ns/coll/plugins/modules/bad.py", "from general_ludd.models import gateway\n")
    _write(root / "ns/coll/plugins/module_utils/path.py", "sys.path.insert(0, '/checkout/src')\n")
    _write(root / "ns/coll/roles/demo/tasks/main.yml", "command: python3 /tmp/tool.py\n")
    findings = scan_collections(root)
    assert {finding.rule for finding in findings} == {
        "ambient-python",
        "core-import",
        "sys-path-mutation",
    }


def test_scanner_ignores_docs_tests_and_fqcn_collection_imports(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    _write(
        root / "ns/coll/plugins/modules/good.py",
        "from ansible_collections.ns.other.plugins.module_utils.x import y\n",
    )
    _write(root / "ns/coll/tests/unit/test_fake.py", "from general_ludd import fake\n")
    _write(root / "ns/coll/README.md", "python3 example.py\n")
    assert scan_collections(root) == []


def test_inventory_is_exact_not_a_path_allowlist(tmp_path: Path) -> None:
    finding = Finding(path="ns/coll/plugins/modules/bad.py", line=1, rule="core-import", text_hash="a" * 64)
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(
        json.dumps({"schema_version": 1, "findings": [finding.as_dict()]}),
        encoding="utf-8",
    )
    inventory = load_inventory(inventory_path)
    assert validate_inventory([finding], inventory, strict_zero=False) == []
    changed = Finding(path=finding.path, line=1, rule=finding.rule, text_hash="b" * 64)
    errors = validate_inventory([changed], inventory, strict_zero=False)
    assert any("new finding" in error for error in errors)
    assert any("stale inventory" in error for error in errors)


def test_strict_zero_rejects_every_migration_finding() -> None:
    finding = Finding(path="ns/coll/plugins/modules/bad.py", line=1, rule="core-import", text_hash="a" * 64)
    errors = validate_inventory([finding], {finding.key(): finding}, strict_zero=True)
    assert errors == ["strict-zero violation: 1 collection Python boundary finding(s) remain"]


def test_inventory_rejects_duplicates(tmp_path: Path) -> None:
    raw = {
        "schema_version": 1,
        "findings": [
            {"path": "a.py", "line": 1, "rule": "core-import", "text_hash": "a" * 64},
            {"path": "a.py", "line": 1, "rule": "core-import", "text_hash": "a" * 64},
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_inventory(path)


def test_cli_writes_then_validates_exact_inventory(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    inventory = tmp_path / "inventory.json"
    _write(root / "ns/coll/plugins/modules/bad.py", "from general_ludd import models\n")
    assert main(["--collections-root", str(root), "--inventory", str(inventory), "--write-inventory"]) == 0
    assert main(["--collections-root", str(root), "--inventory", str(inventory)]) == 0
    assert main(["--collections-root", str(root), "--inventory", str(inventory), "--strict-zero"]) == 1


def test_cli_fails_closed_when_inventory_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    root.mkdir()
    assert main(["--collections-root", str(root), "--inventory", str(tmp_path / "missing.json")]) == 2


def test_scanner_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        scan_collections(tmp_path / "missing")
