"""Require deployable runtime metadata for every locally built EE collection."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[2]
EE_COLLECTIONS = ("agent", "ai_ml", "azure", "infrastructure", "language", "networking", "travel", "xml")


@pytest.mark.parametrize("collection", EE_COLLECTIONS)
def test_owned_ee_collection_declares_supported_runtime_metadata(collection: str) -> None:
    """Reject missing metadata before a real archive reaches native admission."""
    runtime = ROOT / "collections" / "ansible_collections" / "general_ludd" / collection / "meta" / "runtime.yml"
    assert runtime.is_file() and not runtime.is_symlink(), f"missing regular runtime metadata: {collection}"
    document = yaml.safe_load(runtime.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"runtime metadata must be a mapping: {collection}"
    requires = document.get("requires_ansible")
    assert isinstance(requires, str) and requires, f"missing requires_ansible: {collection}"
    supported = SpecifierSet(requires)
    for core_version in ("2.19.12", "2.19.14", "2.21.5"):
        assert supported.contains(core_version), f"unsupported admitted Core {core_version}: {collection}"


def test_owned_collection_metadata_is_checked_before_publication() -> None:
    """Bind the real archive failure to the existing bounded admission owner."""
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "_pytest-runtime-compatibility-admission"],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assignments = [
        argument.removeprefix("TESTFILES=")
        for argument in shlex.split(result.stdout) if argument.startswith("TESTFILES=")
    ]
    assert assignments
    assert "tests/unit/test_ee_owned_collection_runtime_metadata.py" in assignments[0].split()
    manifest = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    family = next(entry for entry in manifest["families"] if entry["id"] == "ee-owned-collection-runtime-metadata")
    assert family == {
        "id": "ee-owned-collection-runtime-metadata",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": "tests/unit/test_ee_owned_collection_runtime_metadata.py::"
                "test_owned_ee_collection_declares_supported_runtime_metadata",
        "full_gate_phase": "test",
    }
