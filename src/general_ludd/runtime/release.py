"""Release artifact validator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from general_ludd.runtime.manifest_signer import ManifestSigner


@dataclass
class ReleaseValidationResult:
    valid: bool
    pip_bundle_valid: bool
    container_valid: bool
    manifest_valid: bool
    signature_valid: bool = False
    errors: list[str] = field(default_factory=list)


class ReleaseArtifactValidator:
    def validate_release(self, version: str, artifacts_dir: str) -> ReleaseValidationResult:
        errors: list[str] = []
        artifacts_path = Path(artifacts_dir)

        pip_bundle_valid = self._check_pip_bundle(artifacts_path, errors)
        container_valid = self._check_container(artifacts_path, version, errors)
        manifest_valid = self._check_manifest(artifacts_path, version, errors)
        signature_valid = self._check_signature(artifacts_path, errors)

        return ReleaseValidationResult(
            valid=len(errors) == 0,
            pip_bundle_valid=pip_bundle_valid,
            container_valid=container_valid,
            manifest_valid=manifest_valid,
            signature_valid=signature_valid,
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
            checksum_entries = self._parse_checksums_file(checksum_file)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            errors.append(f"Error reading bundle artifacts: {exc}")
            return False

        ok = True

        # Every manifest-listed file must (a) actually exist on disk, (b) hash
        # to the value recorded in the manifest, and (c) agree with the
        # co-located CHECKSUMS.sha256 authoritative hash list. A file that is
        # absent used to be silently skipped, letting an incomplete bundle pass.
        for fname, expected_hash in stored_checksums.items():
            fpath = artifacts_path / fname
            if not fpath.exists():
                errors.append(f"Manifest-listed file missing from bundle: {fname}")
                ok = False
                continue

            actual = f"sha256:{hashlib.sha256(fpath.read_bytes()).hexdigest()}"
            if actual != expected_hash:
                errors.append(f"Checksum mismatch for {fname}")
                ok = False
                continue

            if fname not in checksum_entries:
                errors.append(
                    f"File in MANIFEST.json but absent from CHECKSUMS.sha256: {fname}"
                )
                ok = False
                continue

            if checksum_entries[fname] != expected_hash:
                errors.append(
                    f"CHECKSUMS.sha256 disagrees with MANIFEST.json for {fname}"
                )
                ok = False

        # A file recorded in CHECKSUMS.sha256 that the manifest never declares
        # is also a bundle-integrity error (the two lists must be consistent).
        for fname in checksum_entries:
            if fname not in stored_checksums:
                errors.append(
                    f"File in CHECKSUMS.sha256 but absent from MANIFEST.json: {fname}"
                )
                ok = False

        return ok

    @staticmethod
    def _parse_checksums_file(checksum_file: Path) -> dict[str, str]:
        """Parse a CHECKSUMS.sha256 file into a ``{filename: hash}`` map.

        Each non-blank line has the form ``<hash>  <filename>`` (the ``sha256:``
        prefix on the hash matches the MANIFEST.json checksum encoding). A
        leading ``*`` on the filename (sha256sum binary-mode marker) is ignored.
        Malformed lines (no ``<hash> <name>`` split) are skipped rather than
        parsed into an entry; this is not a bypass because the manifest is the
        authoritative driver — any manifest-listed file that lacks a valid
        CHECKSUMS entry is flagged as an error by the caller.
        """
        entries: dict[str, str] = {}
        for raw_line in checksum_file.read_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            file_hash, fname = parts[0], parts[1].strip()
            if fname.startswith("*"):
                fname = fname[1:]
            entries[fname] = file_hash
        return entries

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

    def _check_signature(self, artifacts_path: Path, errors: list[str]) -> bool:
        manifest_file = artifacts_path / "MANIFEST.json"
        sig_file = artifacts_path / "MANIFEST.json.sig"

        if not sig_file.exists():
            return False

        result = ManifestSigner().verify(str(manifest_file), str(sig_file))
        if not result.success:
            errors.extend(result.errors)
            return False
        return True
