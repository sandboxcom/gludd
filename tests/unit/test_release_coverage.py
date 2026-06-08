from __future__ import annotations

import hashlib
import json
from pathlib import Path

from general_ludd.runtime.release import ReleaseArtifactValidator


def _write_file(path: Path, content: str) -> None:
    path.write_text(content)


def _sha256_of(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class TestReleaseAllValid:
    def test_all_valid(self, tmp_path: Path) -> None:
        artifact = b"artifact-bytes"
        checksum = _sha256_of(artifact)
        (tmp_path / "artifact.whl").write_bytes(artifact)

        manifest = {
            "version": "1.0.0",
            "commit": "abc",
            "files": ["artifact.whl"],
            "checksums": {"artifact.whl": checksum},
        }
        _write_file(tmp_path / "MANIFEST.json", json.dumps(manifest))
        _write_file(tmp_path / "CHECKSUMS.sha256", f"{checksum}  artifact.whl\n")
        _write_file(
            tmp_path / "container-image-tags.json",
            json.dumps({"tags": ["myimage:1.0.0"]}),
        )

        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.valid is True
        assert result.pip_bundle_valid is True
        assert result.container_valid is True
        assert result.manifest_valid is True
        assert result.errors == []


class TestReleaseMissingManifestForPipBundle:
    def test_missing_manifest_pip_bundle_invalid(self, tmp_path: Path) -> None:
        _write_file(tmp_path / "CHECKSUMS.sha256", "")

        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.pip_bundle_valid is False
        assert "MANIFEST.json not found" in result.errors[0]


class TestReleaseMissingChecksums:
    def test_missing_checksums_pip_bundle_invalid(self, tmp_path: Path) -> None:
        _write_file(tmp_path / "MANIFEST.json", json.dumps({"version": "1.0.0", "checksums": {}}))

        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.pip_bundle_valid is False
        assert "CHECKSUMS.sha256 not found" in result.errors[0]


class TestReleaseInvalidManifestJson:
    def test_invalid_json_manifest(self, tmp_path: Path) -> None:
        _write_file(tmp_path / "MANIFEST.json", "not json{{{")
        _write_file(tmp_path / "CHECKSUMS.sha256", "")

        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.pip_bundle_valid is False
        assert any("Error reading bundle" in e for e in result.errors)


class TestReleaseChecksumMismatch:
    def test_checksum_mismatch(self, tmp_path: Path) -> None:
        (tmp_path / "artifact.whl").write_bytes(b"real-content")

        manifest = {
            "version": "1.0.0",
            "checksums": {"artifact.whl": "sha256:badauthhash"},
        }
        _write_file(tmp_path / "MANIFEST.json", json.dumps(manifest))
        _write_file(tmp_path / "CHECKSUMS.sha256", "sha256:badauthhash  artifact.whl\n")

        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.pip_bundle_valid is False
        assert any("Checksum mismatch" in e for e in result.errors)


class TestReleaseContainerTagsWithoutVersion:
    def test_container_tags_missing_version(self, tmp_path: Path) -> None:
        (tmp_path / "artifact.whl").write_bytes(b"x")
        checksum = _sha256_of(b"x")
        manifest = {"version": "2.0.0", "checksums": {"artifact.whl": checksum}}
        _write_file(tmp_path / "MANIFEST.json", json.dumps(manifest))
        _write_file(tmp_path / "CHECKSUMS.sha256", f"{checksum}  artifact.whl\n")
        _write_file(
            tmp_path / "container-image-tags.json",
            json.dumps({"tags": ["myimage:other"]}),
        )

        result = ReleaseArtifactValidator().validate_release("2.0.0", str(tmp_path))

        assert result.container_valid is False
        assert any("do not reference version" in e for e in result.errors)


class TestReleaseContainerInvalidJson:
    def test_container_tags_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "artifact.whl").write_bytes(b"x")
        checksum = _sha256_of(b"x")
        manifest = {"version": "2.0.0", "checksums": {"artifact.whl": checksum}}
        _write_file(tmp_path / "MANIFEST.json", json.dumps(manifest))
        _write_file(tmp_path / "CHECKSUMS.sha256", f"{checksum}  artifact.whl\n")
        _write_file(tmp_path / "container-image-tags.json", "not json{")

        result = ReleaseArtifactValidator().validate_release("2.0.0", str(tmp_path))

        assert result.container_valid is False
        assert any("not valid JSON" in e for e in result.errors)


class TestReleaseNoContainerTagsFile:
    def test_no_container_file_is_valid(self, tmp_path: Path) -> None:
        (tmp_path / "artifact.whl").write_bytes(b"x")
        checksum = _sha256_of(b"x")
        manifest = {"version": "1.0.0", "checksums": {"artifact.whl": checksum}}
        _write_file(tmp_path / "MANIFEST.json", json.dumps(manifest))
        _write_file(tmp_path / "CHECKSUMS.sha256", f"{checksum}  artifact.whl\n")

        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.container_valid is True
        assert result.valid is True


class TestReleaseManifestVersionMismatch:
    def test_manifest_version_mismatch(self, tmp_path: Path) -> None:
        (tmp_path / "artifact.whl").write_bytes(b"x")
        checksum = _sha256_of(b"x")
        manifest = {"version": "0.9.0", "checksums": {"artifact.whl": checksum}}
        _write_file(tmp_path / "MANIFEST.json", json.dumps(manifest))
        _write_file(tmp_path / "CHECKSUMS.sha256", f"{checksum}  artifact.whl\n")

        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.manifest_valid is False
        assert any("version mismatch" in e for e in result.errors)


class TestReleaseManifestInvalidJson:
    def test_manifest_invalid_json_for_manifest_check(self, tmp_path: Path) -> None:
        _write_file(tmp_path / "MANIFEST.json", "broken{json")
        _write_file(tmp_path / "CHECKSUMS.sha256", "")

        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.manifest_valid is False


class TestReleaseMissingManifestForManifestCheck:
    def test_missing_manifest_manifest_invalid(self, tmp_path: Path) -> None:
        result = ReleaseArtifactValidator().validate_release("1.0.0", str(tmp_path))

        assert result.manifest_valid is False
        assert result.pip_bundle_valid is False
