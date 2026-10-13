"""Portable controls for the reviewed canonical Galaxy inputs and native closure."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import ansible_runtime_artifacts as owner

REVIEWED_LOCK_SHA256 = "sha256:7d176b02362222535077a47b4a4f8191e6e9b4a00be0fbc6d3a1f93d4a7a9588"
ORIGINAL_INPUTS = {"galaxy", "python", "system", "definition", "smoke_settings", "smoke_limiter"}
ROOT_VERSIONS = {
    "azure.azcollection": "3.21.0", "cloud.terraform": "4.0.0",
    "community.crypto": "3.4.0", "community.general": "13.5.0",
    "ansible.windows": "3.8.0", "community.windows": "3.3.0", "ansible.posix": "2.2.2",
}
CLOSURE_VERSIONS = {**ROOT_VERSIONS, "community.library_inventory_filtering_v1": "1.1.5"}
# Frozen from the producer's native MANIFEST.json entries; no replacement resolver.
NATIVE_DEPENDENCIES = {
    "community.general": {"community.library_inventory_filtering_v1": ">=1.0.0"},
    "community.windows": {"ansible.windows": ">=3.0.0,<4.0.0"},
}


@pytest.fixture
def frozen_runtime_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy reviewed canonical bytes and their independent manifest into scratch."""
    inputs = {name: tmp_path / path.name for name, path in owner.INPUTS.items()}
    for name, path in owner.INPUTS.items():
        shutil.copyfile(path, inputs[name])
    canonical_lock = tmp_path / "collections.lock.json"
    if not canonical_lock.is_file():
        shutil.copyfile(owner.CONFIG_ROOT / "collections.lock.json", canonical_lock)
    manifest = tmp_path / "runtime-lock.json"
    shutil.copyfile(owner.LOCK, manifest)
    monkeypatch.setattr(owner, "INPUTS", inputs)
    monkeypatch.setattr(owner, "DEFINITION", inputs["definition"])
    monkeypatch.setattr(owner, "LOCK", manifest)
    return canonical_lock


def test_canonical_manifest_retains_six_inputs_and_binds_collection_lock() -> None:
    assert set(owner.INPUTS) == ORIGINAL_INPUTS | {"collection_lock"}
    assert owner.INPUTS["collection_lock"] == owner.CONFIG_ROOT / "collections.lock.json"
    assert owner.expected_input_hashes()["collection_lock"] == REVIEWED_LOCK_SHA256


def test_canonical_requirements_keep_exact_roots_and_all_eight_local_artifacts() -> None:
    assert owner._required_collection_versions(owner.INPUTS["galaxy"]) == ROOT_VERSIONS
    document = yaml.safe_load(owner.INPUTS["galaxy"].read_text(encoding="utf-8"))
    local = [entry for entry in document["collections"] if entry.get("type") == "file"]
    assert local == [
        {"name": f"collections/{artifact.name}", "type": "file"}
        for _source, artifact in owner.COLLECTION_ARTIFACTS
    ]
    assert len(local) == 8


def test_reviewed_lock_keeps_complete_eight_artifact_native_closure() -> None:
    entries = owner._collection_lock_entries(
        owner.CONFIG_ROOT / "collections.lock.json", REVIEWED_LOCK_SHA256, ROOT_VERSIONS,
    )
    assert {entry["name"]: entry["version"] for entry in entries} == CLOSURE_VERSIONS
    inventory = {
        name: (version, NATIVE_DEPENDENCIES.get(name, {})) for name, version in CLOSURE_VERSIONS.items()
    }
    owner._validate_collection_dependencies(inventory, ROOT_VERSIONS)
    del inventory["community.library_inventory_filtering_v1"]
    with pytest.raises(
        ValueError, match=r"missing staged collection manifest: community.library_inventory_filtering_v1",
    ):
        owner._validate_collection_dependencies(inventory, ROOT_VERSIONS)


def test_missing_canonical_lock_fails_existing_runtime_admission(frozen_runtime_inputs: Path) -> None:
    frozen_runtime_inputs.unlink()
    assert owner.validate_files() == [f"missing runtime artifact: {frozen_runtime_inputs}"]


def test_changed_lock_cannot_rebind_its_own_expected_digest(frozen_runtime_inputs: Path) -> None:
    frozen_runtime_inputs.write_bytes(frozen_runtime_inputs.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="collection lock digest does not match the reviewed input"):
        owner.bound_collection_lock_sha256()
    assert "runtime lock input hashes are stale; run make update-ansible-runtime-lock" in owner.validate_files()


@pytest.mark.parametrize("binding", [None, "", "sha256:unreviewed", 12])
def test_missing_or_invalid_independent_binding_is_rejected(
    frozen_runtime_inputs: Path, binding: object,
) -> None:
    document: dict[str, Any] = json.loads(owner.LOCK.read_text(encoding="utf-8"))
    if binding is None:
        document["inputs"].pop("collection_lock", None)
    else:
        document["inputs"]["collection_lock"] = binding
    owner.LOCK.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="runtime lock must independently bind collection_lock"):
        owner.bound_collection_lock_sha256()
    assert any("independently bind collection_lock" in error for error in owner.validate_files())


def test_accessors_return_reviewed_expectation_and_canonical_exact_roots(frozen_runtime_inputs: Path) -> None:
    assert owner.required_collection_versions() == ROOT_VERSIONS
    assert owner.bound_collection_lock_sha256() == REVIEWED_LOCK_SHA256
    assert json.loads(owner.LOCK.read_text())["inputs"]["collection_lock"] == REVIEWED_LOCK_SHA256


def test_changed_requirements_cannot_replace_reviewed_versions(frozen_runtime_inputs: Path) -> None:
    requirements = owner.INPUTS["galaxy"]
    requirements.write_bytes(requirements.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="canonical collection requirements differ from the reviewed runtime input"):
        owner.required_collection_versions()


@pytest.mark.parametrize("name", sorted(ORIGINAL_INPUTS))
def test_original_six_inputs_remain_independently_bound(frozen_runtime_inputs: Path, name: str) -> None:
    path = owner.INPUTS[name]
    expected = json.loads(owner.LOCK.read_text())["inputs"][name]
    assert owner.expected_input_hashes()[name] == expected
    path.write_bytes(path.read_bytes() + b"\n")
    assert owner.expected_input_hashes()[name] != expected
