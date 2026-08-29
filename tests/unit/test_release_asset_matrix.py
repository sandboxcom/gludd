"""Tests for the fail-closed release asset matrix."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.verify_release_asset_matrix import (
    FOUNDATION_RELEASE_NAMES,
    REQUIRED_SMOKE_CHECKS,
    distribution_version,
    main,
    referenced_collection_artifacts,
    verify_release_asset_matrix,
    write_release_manifest,
)

VERSION = "0.1.0-beta.4"
DIST_VERSION = "0.1.0b4"


def _collection_tar(path: Path, name: str, version: str) -> None:
    payload = json.dumps(
        {"collection_info": {"namespace": "general_ludd", "name": name, "version": version}}
    ).encode()
    member = tarfile.TarInfo("MANIFEST.json")
    member.size = len(payload)
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(member, io.BytesIO(payload))


def _native_tar(path: Path, *, executable: bool = True) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in (
            ("gludd", b"#!/usr/bin/env sh\nexit 0\n"),
            ("install.sh", b"#!/usr/bin/env bash\nset -euo pipefail\n"),
        ):
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if executable else 0o644
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


def _windows_zip(path: Path, *, include_executable: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if include_executable:
            archive.writestr("gludd.exe", b"MZ")


def _python_distributions(
    assets: Path,
    *,
    metadata_name: str = "general-ludd-agent",
    metadata_version: str = DIST_VERSION,
) -> None:
    wheel = assets / f"general_ludd_agent-{DIST_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("general_ludd/__init__.py", "")
        archive.writestr("general_ludd/cli.py", "def main():\n    return 0\n")
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/METADATA",
            "Metadata-Version: 2.4\n"
            f"Name: {metadata_name}\n"
            f"Version: {metadata_version}\n",
        )
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/entry_points.txt",
            "[console_scripts]\ngludd = general_ludd.cli:main\n",
        )

    sdist = assets / f"general_ludd_agent-{DIST_VERSION}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        root = f"general_ludd_agent-{DIST_VERSION}"
        for name, payload in (
            (
                "PKG-INFO",
                (
                    "Metadata-Version: 2.4\n"
                    f"Name: {metadata_name}\n"
                    f"Version: {metadata_version}\n"
                ).encode(),
            ),
            ("src/general_ludd/__init__.py", b""),
            ("src/general_ludd/cli.py", b"def main():\n    return 0\n"),
            ("pyproject.toml", b"[project.scripts]\ngludd = 'general_ludd.cli:main'\n"),
        ):
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


def _refresh_checksums(assets: Path) -> None:
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(assets.iterdir())
        if path.name != "SHA256SUMS"
    ]
    (assets / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _complete_matrix(tmp_path: Path) -> tuple[Path, Path]:
    repo, assets = tmp_path / "repo", tmp_path / "assets"
    config = repo / "config" / "ansible"
    config.mkdir(parents=True)
    assets.mkdir()
    (config / "requirements.yml").write_text(
        "---\ncollections:\n"
        "  - name: ../../dist/collections/general_ludd-agent-0.2.0.tar.gz\n"
        "    type: file\n"
        "  - name: ../../dist/collections/general_ludd-language-0.1.0.tar.gz\n"
        "    type: file\n",
        encoding="utf-8",
    )
    for name in (
        "execution-environment.yml",
        "requirements.txt",
        "bindep.txt",
        "runtime-lock.json",
        "managed-host-python.lock.json",
        "collection-python-boundary-inventory.json",
    ):
        (config / name).write_text("{}\n", encoding="utf-8")

    for name in (
        f"gludd_{VERSION}_amd64.deb",
        f"gludd-{VERSION}-1.x86_64.rpm",
        f"gludd-{VERSION}-macos-arm64.dmg",
        f"gludd-{VERSION}-setup-x86_64.exe",
        "LICENSE",
        "THIRD_PARTY_LICENSES.md",
    ):
        (assets / name).write_bytes(b"artifact")
    for name in (
        f"gludd-{VERSION}-linux-x86_64.tar.gz",
        f"gludd-{VERSION}-macos-arm64.tar.gz",
        f"gludd-{VERSION}-linux-aarch64.tar.gz",
    ):
        _native_tar(assets / name)
    _windows_zip(assets / f"gludd-{VERSION}-windows-x86_64.zip")
    for source_name, release_name in FOUNDATION_RELEASE_NAMES.items():
        (assets / release_name).write_bytes((config / source_name).read_bytes())

    install = assets / "install.sh"
    install.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
    install.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    (assets / "sbom.json").write_text(
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [{}]}),
        encoding="utf-8",
    )
    digest_ref = "ghcr.io/sandboxcom/gludd@sha256:" + "a" * 64
    for prefix in ("gludd-ee-image", "gludd-container"):
        (assets / f"{prefix}-{VERSION}.json").write_text(
            json.dumps({"version": VERSION, "image": digest_ref}), encoding="utf-8"
        )
    (assets / f"gludd-smoke-all-{VERSION}.json").write_text(
        json.dumps(
            {"version": VERSION, "checks": {name: "passed" for name in REQUIRED_SMOKE_CHECKS}}
        ),
        encoding="utf-8",
    )

    for filename in referenced_collection_artifacts(repo):
        collection_name = filename.removeprefix("general_ludd-").split("-")[0]
        collection_version = filename.removesuffix(".tar.gz").rsplit("-", maxsplit=1)[1]
        _collection_tar(assets / filename, collection_name, collection_version)
    (assets / f"gludd-collections-{VERSION}.json").write_text(
        json.dumps({"version": VERSION, "artifacts": sorted(referenced_collection_artifacts(repo))}),
        encoding="utf-8",
    )

    _python_distributions(assets)

    manifest = assets / f"gludd-release-manifest-{VERSION}.json"
    manifest.write_text(
        json.dumps(
            {
                "version": VERSION,
                "schema_version": 1,
                "source_sha": "a" * 40,
                "assets": sorted(
                    path.name
                    for path in assets.iterdir()
                    if path.name not in {manifest.name, "SHA256SUMS"}
                ),
            }
        ),
        encoding="utf-8",
    )

    _refresh_checksums(assets)
    return assets, repo


def test_complete_release_asset_matrix_passes(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    assert verify_release_asset_matrix(assets, VERSION, repo) == []


def test_missing_platform_package_fails_closed(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-{VERSION}-1.x86_64.rpm").unlink()
    assert any("linux rpm" in error for error in verify_release_asset_matrix(assets, VERSION, repo))


def test_smoke_attestations_must_cover_every_check(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-smoke-all-{VERSION}.json").write_text(
        json.dumps({"version": VERSION, "checks": {"linux_tar": "passed"}}), encoding="utf-8"
    )
    assert any(
        "smoke checks missing" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_every_runtime_collection_tarball_is_required(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / "general_ludd-language-0.1.0.tar.gz").unlink()
    assert any(
        "general_ludd-language-0.1.0.tar.gz" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_checksums_must_cover_and_match_every_asset(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / "LICENSE").write_text("changed after checksum\n", encoding="utf-8")
    assert any(
        "checksum mismatch: LICENSE" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_image_metadata_requires_digest_pinned_reference(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-ee-image-{VERSION}.json").write_text(
        json.dumps({"version": VERSION, "image": "ghcr.io/sandboxcom/gludd:latest"}),
        encoding="utf-8",
    )
    assert any(
        "digest-pinned" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_install_script_must_be_executable_and_fail_fast(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    install = assets / "install.sh"
    install.write_text("#!/bin/sh\necho unsafe\n", encoding="utf-8")
    install.chmod(stat.S_IRUSR | stat.S_IWUSR)
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert "install.sh must be executable" in errors
    assert "install.sh must enable set -euo pipefail" in errors


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("0.1.0-alpha.2", "0.1.0a2"),
        ("0.1.0-beta.4", "0.1.0b4"),
        ("0.1.0-rc.1", "0.1.0rc1"),
        ("1.0.0", "1.0.0"),
    ],
)
def test_distribution_version_normalization(raw: str, normalized: str) -> None:
    assert distribution_version(raw) == normalized


@pytest.mark.parametrize(
    "contents",
    ["[]\n", "{}\n", "collections: nope\n", "collections: [invalid]\n"],
)
def test_malformed_collection_requirements_fail_closed(
    tmp_path: Path, contents: str
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (repo / "config" / "ansible" / "requirements.yml").write_text(contents, encoding="utf-8")
    assert referenced_collection_artifacts(repo) == set()
    assert any(
        "references no collection" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_invalid_collection_archive_and_identity_are_rejected(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    agent = assets / "general_ludd-agent-0.2.0.tar.gz"
    agent.write_bytes(b"not a tar")
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("invalid collection archive" in error for error in errors)

    _collection_tar(agent, "wrong_name", "0.2.0")
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("collection identity" in error for error in errors)


def test_invalid_python_distribution_archives_are_rejected(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"general_ludd_agent-{DIST_VERSION}-py3-none-any.whl").write_bytes(b"bad")
    (assets / f"general_ludd_agent-{DIST_VERSION}.tar.gz").write_bytes(b"bad")
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("wheel is invalid" in error for error in errors)
    assert any("sdist is invalid" in error for error in errors)


def test_python_distribution_metadata_identity_must_match_beta4_filename(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    _python_distributions(
        assets,
        metadata_name="renamed-project",
        metadata_version="9.9",
    )
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert any("wheel METADATA identity is stale" in error for error in errors)
    assert any("sdist PKG-INFO identity is stale" in error for error in errors)


def test_python_distribution_metadata_is_unique_utf8_and_complete(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    wheel = assets / f"general_ludd_agent-{DIST_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("general_ludd/__init__.py", "")
        archive.writestr("general_ludd/cli.py", "")
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/METADATA", b"\xff"
        )
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/entry_points.txt",
            "[console_scripts]\ngludd = general_ludd.cli:main\n",
        )
    sdist = assets / f"general_ludd_agent-{DIST_VERSION}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        root = f"general_ludd_agent-{DIST_VERSION}"
        for name, payload in (
            ("PKG-INFO", b"\xff"),
            ("src/general_ludd/__init__.py", b""),
            ("src/general_ludd/cli.py", b""),
            ("pyproject.toml", b""),
        ):
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert any("wheel METADATA is not UTF-8" in error for error in errors)
    assert any("sdist PKG-INFO is not UTF-8" in error for error in errors)

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("general_ludd/cli.py", "")
        archive.writestr("one.dist-info/METADATA", "Name: one\nVersion: 1\n")
        archive.writestr("two.dist-info/METADATA", "Name: two\nVersion: 2\n")
        archive.writestr("one.dist-info/entry_points.txt", "wrong = target\n")
    with tarfile.open(sdist, "w:gz") as archive:
        root = f"general_ludd_agent-{DIST_VERSION}"
        for name in ("one/PKG-INFO", "two/PKG-INFO", "pyproject.toml"):
            payload = b"Name: wrong\nVersion: 1\n"
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert "wheel does not contain general_ludd/__init__.py" in errors
    assert "wheel does not contain distribution METADATA" in errors
    assert "wheel does not declare the gludd console entrypoint" in errors
    assert "sdist does not contain PKG-INFO" in errors
    assert "sdist does not contain src/general_ludd/__init__.py" in errors
    assert "sdist does not contain src/general_ludd/cli.py" in errors


def test_native_archives_must_contain_installed_executables(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    _native_tar(assets / f"gludd-{VERSION}-linux-x86_64.tar.gz", executable=False)
    _windows_zip(
        assets / f"gludd-{VERSION}-windows-x86_64.zip", include_executable=False
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert "linux tar executable gludd must be executable" in errors
    assert "windows zip does not contain gludd.exe" in errors


def test_native_archives_reject_corrupt_empty_and_duplicate_payloads(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-{VERSION}-linux-x86_64.tar.gz").write_bytes(b"not-a-tar")
    windows_zip = assets / f"gludd-{VERSION}-windows-x86_64.zip"
    with zipfile.ZipFile(windows_zip, "w") as archive:
        archive.writestr("gludd.exe", b"")
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert any("linux tar archive is invalid" in error for error in errors)
    assert "windows zip gludd.exe is empty" in errors

    _native_tar(assets / f"gludd-{VERSION}-linux-x86_64.tar.gz")
    with zipfile.ZipFile(windows_zip, "w") as archive:
        archive.writestr("first/gludd.exe", b"MZ")
        archive.writestr("second/gludd.exe", b"MZ")
    _refresh_checksums(assets)

    assert "windows zip contains multiple gludd.exe files" in (
        verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_python_distributions_must_install_the_gludd_entrypoint(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    wheel = assets / f"general_ludd_agent-{DIST_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("general_ludd/__init__.py", "")
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/METADATA",
            f"Version: {DIST_VERSION}\n",
        )
    sdist = assets / f"general_ludd_agent-{DIST_VERSION}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        payload = f"Version: {DIST_VERSION}\n".encode()
        member = tarfile.TarInfo(f"general_ludd_agent-{DIST_VERSION}/PKG-INFO")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert "wheel does not contain general_ludd/cli.py" in errors
    assert "wheel does not declare the gludd console entrypoint" in errors
    assert "sdist does not contain src/general_ludd/cli.py" in errors
    assert "sdist does not contain pyproject.toml" in errors


def test_release_manifest_must_inventory_every_staged_asset(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    manifest = assets / f"gludd-release-manifest-{VERSION}.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": VERSION,
                "source_sha": "a" * 40,
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    assert "release manifest asset inventory is stale or incomplete" in (
        verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_smoke_attestation_schema_conflicts_and_failures_are_rejected(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    primary = assets / f"gludd-smoke-all-{VERSION}.json"
    primary.write_text(
        json.dumps({"version": "wrong", "checks": {"linux_tar": 3}}),
        encoding="utf-8",
    )
    secondary = assets / f"gludd-smoke-second-{VERSION}.json"
    secondary.write_text(
        json.dumps({"version": VERSION, "checks": {"linux_tar": "failed"}}),
        encoding="utf-8",
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("smoke version mismatch" in error for error in errors)
    assert any("names/statuses must be strings" in error for error in errors)
    assert any("smoke checks not passed" in error for error in errors)

    primary.write_text(
        json.dumps({"version": VERSION, "checks": {"linux_tar": "passed"}}),
        encoding="utf-8",
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("conflicting smoke status" in error for error in errors)


def test_missing_and_invalid_smoke_attestations_fail_closed(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    smoke = assets / f"gludd-smoke-all-{VERSION}.json"
    smoke.unlink()
    assert "smoke attestations are missing" in verify_release_asset_matrix(
        assets, VERSION, repo
    )
    smoke.write_text("{", encoding="utf-8")
    assert any(
        "not valid JSON" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )
    smoke.write_text(json.dumps({"version": VERSION, "checks": []}), encoding="utf-8")
    assert any(
        "checks must be an object" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_checksum_manifest_rejects_malformed_missing_extra_and_duplicate(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    checksums = assets / "SHA256SUMS"
    original = checksums.read_text(encoding="utf-8")
    checksums.write_text(original + "malformed\n", encoding="utf-8")
    assert any(
        "is malformed" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )

    new_asset = assets / "unexpected.txt"
    new_asset.write_text("new\n", encoding="utf-8")
    assert any(
        "checksums missing" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )

    new_asset.unlink()
    checksums.write_text(
        original + f"{'0' * 64}  absent.txt\n" + original.splitlines()[0] + "\n",
        encoding="utf-8",
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("reference absent assets" in error for error in errors)
    assert any("unsafe or duplicate" in error for error in errors)


def test_foundation_sbom_and_release_manifest_must_match_contract(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    foundation = repo / "config" / "ansible" / "runtime-lock.json"
    foundation.unlink()
    (assets / "sbom.json").write_text(json.dumps({"bomFormat": "other"}), encoding="utf-8")
    (assets / f"gludd-release-manifest-{VERSION}.json").write_text(
        json.dumps({"schema_version": 2, "version": "wrong", "source_sha": "bad"}),
        encoding="utf-8",
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("missing canonical foundation input" in error for error in errors)
    assert "sbom.json is not a CycloneDX component inventory" in errors
    assert "release manifest schema/version/source SHA is invalid" in errors


def test_empty_asset_stale_foundation_and_installer_shebang_are_rejected(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-{VERSION}-macos-arm64.dmg").write_bytes(b"")
    (assets / "ansible-ee-requirements.txt").write_text("stale\n", encoding="utf-8")
    install = assets / "install.sh"
    install.write_text("#!/bin/sh\nset -euo pipefail\n", encoding="utf-8")
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("empty macos dmg" in error for error in errors)
    assert any("stale execution-environment metadata" in error for error in errors)
    assert "install.sh must use the repository bash entrypoint" in errors


def test_write_manifest_and_cli_modes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assets, repo = _complete_matrix(tmp_path)
    manifest = write_release_manifest(assets, VERSION, "b" * 40)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["source_sha"] == "b" * 40
    assert "SHA256SUMS" not in payload["assets"]

    with pytest.raises(ValueError, match="source SHA"):
        write_release_manifest(assets, VERSION, "short")

    assert main(["write-manifest", str(assets), VERSION, "--source-sha", "c" * 40]) == 0
    assert "RELEASE_MANIFEST_WRITTEN" in capsys.readouterr().out

    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(assets.iterdir())
        if path.name != "SHA256SUMS"
    ]
    (assets / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main(["verify", str(assets), VERSION, "--repository-root", str(repo)]) == 0
    assert "RELEASE_ASSET_MATRIX_PASS" in capsys.readouterr().out

    (assets / "LICENSE").unlink()
    assert main(["verify", str(assets), VERSION, "--repository-root", str(repo)]) == 1
    assert "RELEASE_ASSET_MATRIX_FAIL" in capsys.readouterr().out
