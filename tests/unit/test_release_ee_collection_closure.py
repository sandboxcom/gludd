"""Require the actual all16 native reviewed EE release closure downstream."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
import yaml
from scripts import ansible_runtime_artifacts as native
from scripts import verify_release_asset_matrix as owner

from tests.unit.test_release_asset_matrix import (
    FIXTURE_REVIEWED_LOCK_SHA256,
    VERSION,
    _complete_matrix,
    _refresh_checksums,
)
from tests.unit.test_release_asset_matrix import (
    trusted_fixture_review as trusted_fixture_review,
)


def refresh_outer_provenance(assets: Path) -> None:
    owner.write_release_manifest(assets, VERSION, "a" * 40)
    _refresh_checksums(assets)


def test_reviewed_lock_is_required_byte_equal_foundation_metadata() -> None:
    assert owner.FOUNDATION_RELEASE_NAMES["collections.lock.json"] == "ansible-ee-collections.lock.json"


def test_complete_release_closure_is_checked_before_publication() -> None:
    """Bind the downstream handoff to the original bounded native owner."""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "_pytest-runtime-compatibility-admission"],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    selections = [
        word.removeprefix("TESTFILES=").split()
        for word in shlex.split(result.stdout) if word.startswith("TESTFILES=")
    ]
    for file in ("test_release_asset_matrix.py", "test_release_ee_collection_closure.py"):
        assert selections[0].count(f"tests/unit/{file}") == 1
    manifest = json.loads((root / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    family = next(entry for entry in manifest["families"] if entry["id"] == "ee-release-reviewed-collection-closure")
    assert family == {
        "id": "ee-release-reviewed-collection-closure",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node",
        "node": "tests/unit/test_release_ee_collection_closure.py::"
                "test_workflow_manifest_is_the_exact_sorted_native_validator_closure",
        "full_gate_phase": "test",
    }
    assert manifest["full_gate_target"] == "gate" and manifest["full_gate_required"] is True


def test_workflow_manifest_is_the_exact_sorted_native_validator_closure(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    config = repo / "config/ansible"
    lock = cast(dict[str, object], json.loads((config / "collections.lock.json").read_text()))
    reviewed = cast(list[native.CollectionArtifactEntry], lock["collections"])
    expected = {entry["artifact"] for entry in reviewed} | {path.name for _, path in native.COLLECTION_ARTIFACTS}
    assert len(expected) == 16
    assert owner.referenced_collection_artifacts(repo) == expected
    root = Path(__file__).resolve().parents[2]
    workflow = cast(dict[str, object], yaml.safe_load((root / ".github/workflows/build.yml").read_text()))
    jobs = cast(dict[str, dict[str, object]], workflow["jobs"])
    steps = cast(list[dict[str, object]], jobs["ansible-ee"]["steps"])
    stage = str(next(step["run"] for step in steps
                     if step.get("name") == "Stage execution-environment release metadata"))
    declared = cast(dict[str, object], json.loads(next(line for line in stage.splitlines() if '"artifacts"' in line)))
    assert declared["artifacts"] == sorted(expected)
    declared["version"] = VERSION
    (assets / f"gludd-collections-{VERSION}.json").write_text(json.dumps(declared))
    refresh_outer_provenance(assets)
    assert owner.verify_release_asset_matrix(assets, VERSION, repo) == []


@pytest.mark.parametrize("defect", ["missing-source", "missing-package", "changed-source", "changed-package",
                                   "unbound", "broadened", "invalid-shape"])
def test_reviewed_lock_defects_collect_with_original_image_checksum_and_metadata_errors(
    tmp_path: Path, defect: str,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    lock = repo / "config/ansible/collections.lock.json"
    packaged = assets / "ansible-ee-collections.lock.json"
    if defect == "missing-source":
        lock.unlink()
    elif defect == "missing-package":
        packaged.unlink()
    elif defect == "changed-source":
        lock.write_bytes(lock.read_bytes() + b" ")
    elif defect == "changed-package":
        packaged.write_bytes(packaged.read_bytes() + b" ")
    elif defect == "unbound":
        (repo / "config/ansible/runtime-lock.json").write_text("{}\n")
    elif defect == "broadened":
        document = cast(dict[str, object], json.loads(lock.read_text()))
        cast(list[object], document["collections"]).append({"name": "unreviewed.extra", "version": "1.0.0"})
        lock.write_text(json.dumps(document))
        packaged.write_bytes(lock.read_bytes())
    else:
        lock.write_text("[]\n")
    (assets / f"gludd-ee-image-{VERSION}.json").write_text(json.dumps({"version": VERSION, "image": "unbound:tag"}))
    (assets / "LICENSE").write_text("changed after checksumming")
    errors = owner.verify_release_asset_matrix(assets, VERSION, repo)
    assert any("collection" in error or "foundation" in error for error in errors)
    assert any("image must be digest-pinned" in error for error in errors)
    assert any("checksum mismatch: LICENSE" in error for error in errors)


@pytest.mark.parametrize("defect", ["missing", "tamper", "symlink", "identity", "runtime", "transitive"])
def test_owned_thirdparty_bytes_and_native_identity_are_never_self_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    path = assets / "community-general-13.5.0.tar.gz"
    if defect == "missing":
        path.unlink()
    elif defect == "symlink":
        retained = tmp_path / "retained-archive"
        path.rename(retained)
        path.symlink_to(retained)
    elif defect == "tamper":
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        original = native._collection_archive_inventory

        def changed(state: Path, entries: list[native.CollectionArtifactEntry], artifact_root: Path,
                    *, controller_version: str | None = None) -> dict[str, tuple[str, dict[str, str]]]:
            inventory = original(state, entries, artifact_root, controller_version=controller_version)
            if "community.general" in inventory:
                if defect == "identity":
                    raise ValueError("native artifact identity differs: community.general")
                if defect == "runtime":
                    raise ValueError("native artifact requires_ansible is unsupported: community.general")
                inventory["community.general"] = ("13.5.0", {"unreviewed.extra": ">=1.0.0"})
            return inventory

        monkeypatch.setattr(native, "_collection_archive_inventory", changed)
    refresh_outer_provenance(assets)
    errors = owner.verify_release_asset_matrix(assets, VERSION, repo)
    assert any("community.general" in error or path.name in error for error in errors)
    assert not any("checksum mismatch" in error for error in errors)


def test_invalid_closure_never_becomes_cli_success_after_refreshed_checksums(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    archive = assets / "community-general-13.5.0.tar.gz"
    archive.write_bytes(archive.read_bytes() + b"tamper")
    refresh_outer_provenance(assets)
    assert owner.main(["verify", str(assets), VERSION, "--repository-root", str(repo)]) == 1
    output = capsys.readouterr()
    assert "RELEASE_ASSET_MATRIX_PASS" not in output.out
    assert "RELEASE_ASSET_MATRIX_FAIL" in output.out
    assert "community.general" in output.err


def test_fixture_approval_is_independent_and_cannot_be_recomputed_from_tampered_lock(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    assert native.bound_collection_lock_sha256() == FIXTURE_REVIEWED_LOCK_SHA256
    lock = repo / "config/ansible/collections.lock.json"
    lock.write_bytes(lock.read_bytes() + b"tamper")
    refresh_outer_provenance(assets)
    errors = owner.verify_release_asset_matrix(assets, VERSION, repo)
    assert any("reviewed input" in error for error in errors)
    assert native.bound_collection_lock_sha256() == FIXTURE_REVIEWED_LOCK_SHA256


def test_release_cli_retains_standalone_entrypoint_without_ambient_pythonpath(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-W", "error", str(Path(owner.__file__).resolve()), "--help"],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verify" in result.stdout and result.stderr == ""


def test_native_archive_owner_teardown_failure_is_collected_and_never_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets, repo = _complete_matrix(tmp_path)

    def fail(_assets: Path, _repository: Path, _entries: list[native.CollectionArtifactEntry]) -> list[str]:
        raise OSError("owned native release state close failed")

    monkeypatch.setattr(owner, "_verify_reviewed_release_archives", fail)
    (assets / "LICENSE").write_text("changed after checksumming")
    errors = owner.verify_release_asset_matrix(assets, VERSION, repo)
    assert any("owned native release state close failed" in error for error in errors)
    assert any("checksum mismatch: LICENSE" in error for error in errors)
    assert owner.main(["verify", str(assets), VERSION, "--repository-root", str(repo)]) == 1
