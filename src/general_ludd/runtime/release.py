"""Release artifact validator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ReleaseValidationResult:
    valid: bool
    pip_bundle_valid: bool
    container_valid: bool
    manifest_valid: bool
    errors: list[str] = field(default_factory=list)


class ReleaseArtifactValidator:
    def validate_release(self, version: str, artifacts_dir: str) -> ReleaseValidationResult:
        errors: list[str] = []
        artifacts_path = Path(artifacts_dir)

        pip_bundle_valid = self._check_pip_bundle(artifacts_path, errors)
        container_valid = self._check_container(artifacts_path, version, errors)
        manifest_valid = self._check_manifest(artifacts_path, version, errors)

        return ReleaseValidationResult(
            valid=len(errors) == 0,
            pip_bundle_valid=pip_bundle_valid,
            container_valid=container_valid,
            manifest_valid=manifest_valid,
            errors=errors,
        )

    def _check_pip_bundle(self, artifacts_path: Path, errors: list[str]) -> bool:
        manifest_file = artifacts_path / "MANIFEST.json"
        checksum_file = artifacts_path / "CHECKSUMS.sha256"

        if not manifest_file.exists():
            errors.append("MANIFEST.json not found in artifacts dir")
            return False

        if not checksum_file.exists():
            errors.append("CHECKSUMS.sha256 not found in artifacts dir")
            return False

        try:
            manifest_data = json.loads(manifest_file.read_text())
            stored_checksums = manifest_data.get("checksums", {})
            for fname, expected_hash in stored_checksums.items():
                fpath = artifacts_path / fname
                if fpath.exists():
                    actual = f"sha256:{hashlib.sha256(fpath.read_bytes()).hexdigest()}"
                    if actual != expected_hash:
                        errors.append(f"Checksum mismatch for {fname}")
                        return False
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"Error reading bundle artifacts: {exc}")
            return False

        return True

    def _check_container(self, artifacts_path: Path, version: str, errors: list[str]) -> bool:
        image_tags_file = artifacts_path / "container-image-tags.json"
        if image_tags_file.exists():
            try:
                tags_data = json.loads(image_tags_file.read_text())
                if version not in str(tags_data):
                    errors.append(f"Container image tags do not reference version {version}")
                    return False
            except json.JSONDecodeError:
                errors.append("container-image-tags.json is not valid JSON")
                return False
        return True

    def _check_manifest(self, artifacts_path: Path, version: str, errors: list[str]) -> bool:
        manifest_file = artifacts_path / "MANIFEST.json"
        if not manifest_file.exists():
            return False

        try:
            manifest_data = json.loads(manifest_file.read_text())
            if manifest_data.get("version") != version:
                errors.append(f"Manifest version mismatch: expected {version}")
                return False
        except json.JSONDecodeError:
            errors.append("MANIFEST.json is not valid JSON")
            return False

        return True
