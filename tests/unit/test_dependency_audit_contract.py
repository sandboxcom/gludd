"""Fail-closed contract for the repository dependency audit."""

from __future__ import annotations

import ast
import json
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dependency_audit_target_is_fail_closed_and_contract_tracked() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\ndeps-audit:", 1)[1].split("\n\n", 1)[0]

    assert "|| true" not in target
    assert "deptry src" in target

    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item for item in contract["targets"]
        if item["name"] == "deps-audit"
    )
    assert entry["make_variables"] == []
    assert entry["behavior"] == "make deps-audit"


def test_deptry_models_dev_groups_namespaces_and_import_names() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deptry = project["tool"]["deptry"]

    assert deptry["optional_dependencies_dev_groups"] == ["dev"]
    assert "ansible_collections" in deptry["known_first_party"]
    mappings = deptry["package_module_name_map"]
    assert mappings["llama-cpp-python"] == "llama_cpp"
    assert mappings["scikit-image"] == "skimage"
    assert mappings["opencv-python-headless"] == "cv2"
    assert mappings["google-api-python-client"] == "googleapiclient"
    assert mappings["azure-identity"] == "azure"


def test_hindsight_optional_dependency_is_statically_auditable() -> None:
    """Keep the optional Hindsight import visible without a DEP002 suppression."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ignored = project["tool"]["deptry"]["per_rule_ignores"]["DEP002"]
    assert "hindsight-client" not in ignored

    source = (
        ROOT / "src" / "general_ludd" / "memory" / "hindsight_adapter.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "hindsight_client"
        for node in ast.walk(tree)
    )

    stub = ROOT / "typings" / "hindsight_client" / "__init__.pyi"
    assert stub.is_file()
    assert "class Hindsight" in stub.read_text(encoding="utf-8")


def test_dependency_audit_evidence_documents_practitioner_and_zdd_contracts() -> None:
    evidence = (
        ROOT / "docs" / "features" / "DEPENDENCY_TRUTH_AUDIT.md"
    ).read_text(encoding="utf-8")

    assert "https://github.com/python-poetry/poetry/issues/4135" in evidence
    assert "https://www.reddit.com/r/Python/comments/x911kg" in evidence
    assert "ZDD" in evidence


def test_dependency_audit_behavior_is_green() -> None:
    result = subprocess.run(
        ["make", "deps-audit"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
