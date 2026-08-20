#!/usr/bin/env python3
"""Fail-closed validation for the staged beta4 release artifact matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import yaml

FOUNDATION_RELEASE_NAMES = {
    "execution-environment.yml": "ansible-ee-execution-environment.yml",
    "requirements.yml": "ansible-ee-requirements.yml",
    "requirements.txt": "ansible-ee-requirements.txt",
    "bindep.txt": "ansible-ee-bindep.txt",
    "runtime-lock.json": "ansible-ee-runtime-lock.json",
    "managed-host-python.lock.json": "ansible-managed-host-python.lock.json",
    "collection-python-boundary-inventory.json": (
        "ansible-collection-python-boundary-inventory.json"
    ),
}

REQUIRED_SMOKE_CHECKS = frozenset(
    {
        "linux_tar",
        "linux_deb",
        "linux_rpm",
        "macos_tar",
        "macos_dmg",
        "windows_zip",
        "windows_nsis",
        "linux_aarch64_tar",
        "wheel",
        "sdist",
        "collections",
        "ansible_ee",
        "container",
        "sbom",
        "install_script",
    }
)

IMAGE_REFERENCE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$"
)
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def distribution_version(version: str) -> str:
    """Return the normalized PEP 440 version used in wheel/sdist filenames."""
    normalized = re.sub(r"-alpha\.", "a", version, flags=re.IGNORECASE)
    normalized = re.sub(r"-beta\.", "b", normalized, flags=re.IGNORECASE)
    return re.sub(r"-rc\.", "rc", normalized, flags=re.IGNORECASE)


def referenced_collection_artifacts(repository_root: Path) -> set[str]:
    """Return collection tarball basenames locked by the canonical EE requirements."""
    requirements = repository_root / "config" / "ansible" / "requirements.yml"
    if not requirements.is_file():
        return set()
    raw: object = yaml.safe_load(requirements.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return set()
    collections: object = raw.get("collections")
    if not isinstance(collections, list):
        return set()
    artifacts: set[str] = set()
    for entry in collections:
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.endswith(".tar.gz"):
            artifacts.add(Path(name).name)
    return artifacts


def _json_object(path: Path) -> tuple[dict[str, object], str | None]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, f"{path.name} is not valid JSON: {exc}"
    if not isinstance(raw, dict):
        return {}, f"{path.name} JSON root must be an object"
    return raw, None


def _required_names(version: str) -> dict[str, str]:
    dist_version = distribution_version(version)
    return {
        "linux tar": f"gludd-{version}-linux-x86_64.tar.gz",
        "linux deb": f"gludd_{version}_amd64.deb",
        "linux rpm": f"gludd-{version}-1.x86_64.rpm",
        "macos tar": f"gludd-{version}-macos-arm64.tar.gz",
        "macos dmg": f"gludd-{version}-macos-arm64.dmg",
        "windows zip": f"gludd-{version}-windows-x86_64.zip",
        "windows nsis": f"gludd-{version}-setup-x86_64.exe",
        "linux aarch64 tar": f"gludd-{version}-linux-aarch64.tar.gz",
        "wheel": f"general_ludd_agent-{dist_version}-py3-none-any.whl",
        "sdist": f"general_ludd_agent-{dist_version}.tar.gz",
        "execution-environment image metadata": f"gludd-ee-image-{version}.json",
        "container image metadata": f"gludd-container-{version}.json",
        "collection manifest": f"gludd-collections-{version}.json",
        "release manifest": f"gludd-release-manifest-{version}.json",
        "SBOM": "sbom.json",
        "installer": "install.sh",
        "license": "LICENSE",
        "third-party licenses": "THIRD_PARTY_LICENSES.md",
        "checksums": "SHA256SUMS",
    }


def _verify_collection(
    path: Path, expected_filename: str
) -> list[str]:
    errors: list[str] = []
    try:
        with tarfile.open(path, "r:gz") as archive:
            member = archive.getmember("MANIFEST.json")
            extracted = archive.extractfile(member)
            if extracted is None:
                return [f"{expected_filename}: MANIFEST.json is unreadable"]
            raw: object = json.loads(extracted.read().decode("utf-8"))
    except (OSError, tarfile.TarError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"{expected_filename}: invalid collection archive: {exc}"]
    if not isinstance(raw, dict):
        return [f"{expected_filename}: collection manifest root must be an object"]
    info = raw.get("collection_info")
    if not isinstance(info, dict):
        return [f"{expected_filename}: collection_info is missing"]
    namespace, name, version = info.get("namespace"), info.get("name"), info.get("version")
    actual_filename = f"{namespace}-{name}-{version}.tar.gz"
    if actual_filename != expected_filename:
        errors.append(
            f"{expected_filename}: collection identity produced {actual_filename}"
        )
    return errors


def _verify_native_archives(asset_dir: Path, version: str) -> list[str]:
    """Verify unpacked native archives expose the files used by installers."""
    errors: list[str] = []
    tarballs = (
        ("linux tar", f"gludd-{version}-linux-x86_64.tar.gz"),
        ("macos tar", f"gludd-{version}-macos-arm64.tar.gz"),
        ("linux aarch64 tar", f"gludd-{version}-linux-aarch64.tar.gz"),
    )
    for label, filename in tarballs:
        path = asset_dir / filename
        if not path.is_file():
            continue
        try:
            with tarfile.open(path, "r:gz") as archive:
                members = [member for member in archive.getmembers() if member.isfile()]
        except (OSError, tarfile.TarError) as exc:
            errors.append(f"{label} archive is invalid: {exc}")
            continue
        by_basename: dict[str, list[tarfile.TarInfo]] = {}
        for member in members:
            by_basename.setdefault(Path(member.name).name, []).append(member)
        for required in ("gludd", "install.sh"):
            candidates = by_basename.get(required, [])
            if not candidates:
                errors.append(f"{label} does not contain {required}")
            elif len(candidates) != 1:
                errors.append(f"{label} contains multiple {required} files")
            elif candidates[0].mode & stat.S_IXUSR == 0:
                errors.append(f"{label} executable {required} must be executable")

    windows_zip = asset_dir / f"gludd-{version}-windows-x86_64.zip"
    if windows_zip.is_file():
        try:
            with zipfile.ZipFile(windows_zip) as archive:
                executable_names = [
                    name
                    for name in archive.namelist()
                    if not name.endswith("/") and Path(name).name == "gludd.exe"
                ]
                if not executable_names:
                    errors.append("windows zip does not contain gludd.exe")
                elif len(executable_names) != 1:
                    errors.append("windows zip contains multiple gludd.exe files")
                elif archive.getinfo(executable_names[0]).file_size == 0:
                    errors.append("windows zip gludd.exe is empty")
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append(f"windows zip archive is invalid: {exc}")
    return errors


def _verify_distributions(asset_dir: Path, version: str) -> list[str]:
    errors: list[str] = []
    normalized = distribution_version(version)
    wheel = asset_dir / f"general_ludd_agent-{normalized}-py3-none-any.whl"
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            if "general_ludd/__init__.py" not in names:
                errors.append("wheel does not contain general_ludd/__init__.py")
            if "general_ludd/cli.py" not in names:
                errors.append("wheel does not contain general_ludd/cli.py")
            if not any(name.endswith(".dist-info/METADATA") for name in names):
                errors.append("wheel does not contain distribution METADATA")
            entry_points = [
                name for name in names if name.endswith(".dist-info/entry_points.txt")
            ]
            if len(entry_points) != 1:
                errors.append("wheel does not declare the gludd console entrypoint")
            else:
                contents = archive.read(entry_points[0]).decode("utf-8")
                if re.search(
                    r"(?m)^\s*gludd\s*=\s*general_ludd\.cli:main\s*$", contents
                ) is None:
                    errors.append("wheel does not declare the gludd console entrypoint")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        errors.append(f"wheel is invalid: {exc}")

    sdist = asset_dir / f"general_ludd_agent-{normalized}.tar.gz"
    try:
        with tarfile.open(sdist, "r:gz") as archive:
            names = set(archive.getnames())
        if not any(name.endswith("/PKG-INFO") for name in names):
            errors.append("sdist does not contain PKG-INFO")
        if not any(name.endswith("/src/general_ludd/__init__.py") for name in names):
            errors.append("sdist does not contain src/general_ludd/__init__.py")
        if not any(name.endswith("/src/general_ludd/cli.py") for name in names):
            errors.append("sdist does not contain src/general_ludd/cli.py")
        if not any(name.endswith("/pyproject.toml") for name in names):
            errors.append("sdist does not contain pyproject.toml")
    except (OSError, tarfile.TarError) as exc:
        errors.append(f"sdist is invalid: {exc}")
    return errors


def _verify_smoke_attestations(asset_dir: Path, version: str) -> list[str]:
    errors: list[str] = []
    observed: dict[str, str] = {}
    attestations = sorted(asset_dir.glob(f"gludd-smoke-*-{version}.json"))
    if not attestations:
        return ["smoke attestations are missing"]
    for path in attestations:
        payload, error = _json_object(path)
        if error:
            errors.append(error)
            continue
        if payload.get("version") != version:
            errors.append(f"{path.name}: smoke version mismatch")
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            errors.append(f"{path.name}: checks must be an object")
            continue
        for name, status_value in checks.items():
            if not isinstance(name, str) or not isinstance(status_value, str):
                errors.append(f"{path.name}: smoke check names/statuses must be strings")
                continue
            prior = observed.get(name)
            if prior is not None and prior != status_value:
                errors.append(f"conflicting smoke status for {name}")
            observed[name] = status_value
    missing = sorted(REQUIRED_SMOKE_CHECKS - observed.keys())
    if missing:
        errors.append("smoke checks missing: " + ", ".join(missing))
    failed = sorted(name for name in REQUIRED_SMOKE_CHECKS if observed.get(name) != "passed")
    if failed:
        errors.append("smoke checks not passed: " + ", ".join(failed))
    return errors


def _verify_checksums(asset_dir: Path) -> list[str]:
    checksum_file = asset_dir / "SHA256SUMS"
    if not checksum_file.is_file():
        return ["SHA256SUMS is missing"]
    errors: list[str] = []
    recorded: dict[str, str] = {}
    for number, raw_line in enumerate(
        checksum_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        parts = raw_line.split(maxsplit=1)
        if len(parts) != 2 or SHA_RE.fullmatch(parts[0]) is None:
            errors.append(f"SHA256SUMS line {number} is malformed")
            continue
        name = parts[1].lstrip("*")
        if Path(name).name != name or name in recorded:
            errors.append(f"SHA256SUMS line {number} has unsafe or duplicate name")
            continue
        recorded[name] = parts[0]
    expected = {path.name for path in asset_dir.iterdir() if path.is_file()}
    expected.discard("SHA256SUMS")
    missing = sorted(expected - recorded.keys())
    extra = sorted(recorded.keys() - expected)
    if missing:
        errors.append("checksums missing: " + ", ".join(missing))
    if extra:
        errors.append("checksums reference absent assets: " + ", ".join(extra))
    for name in sorted(expected & recorded.keys()):
        digest = hashlib.sha256((asset_dir / name).read_bytes()).hexdigest()
        if digest != recorded[name]:
            errors.append(f"checksum mismatch: {name}")
    return errors


def verify_release_asset_matrix(
    asset_dir: Path, version: str, repository_root: Path
) -> list[str]:
    """Return every release-matrix error; an empty list is the only passing result."""
    errors: list[str] = []
    if not asset_dir.is_dir():
        return [f"asset directory does not exist: {asset_dir}"]

    for label, name in _required_names(version).items():
        path = asset_dir / name
        if not path.is_file():
            errors.append(f"missing {label}: {name}")
        elif path.stat().st_size == 0:
            errors.append(f"empty {label}: {name}")

    config_root = repository_root / "config" / "ansible"
    for source_name, release_name in FOUNDATION_RELEASE_NAMES.items():
        source = config_root / source_name
        artifact = asset_dir / release_name
        if not source.is_file():
            errors.append(f"missing canonical foundation input: config/ansible/{source_name}")
        elif not artifact.is_file():
            errors.append(f"missing execution-environment metadata: {release_name}")
        elif source.read_bytes() != artifact.read_bytes():
            errors.append(f"stale execution-environment metadata: {release_name}")

    collections = referenced_collection_artifacts(repository_root)
    if not collections:
        errors.append("canonical execution environment references no collection artifacts")
    for filename in sorted(collections):
        path = asset_dir / filename
        if not path.is_file():
            errors.append(f"missing runtime collection: {filename}")
        else:
            errors.extend(_verify_collection(path, filename))
    collection_manifest = asset_dir / f"gludd-collections-{version}.json"
    if collection_manifest.is_file():
        payload, error = _json_object(collection_manifest)
        if error:
            errors.append(error)
        else:
            listed = payload.get("artifacts")
            expected_list = sorted(collections)
            if payload.get("version") != version or listed != expected_list:
                errors.append("collection artifact manifest is stale or incomplete")

    errors.extend(_verify_native_archives(asset_dir, version))
    errors.extend(_verify_distributions(asset_dir, version))
    errors.extend(_verify_smoke_attestations(asset_dir, version))

    for prefix in ("gludd-ee-image", "gludd-container"):
        metadata = asset_dir / f"{prefix}-{version}.json"
        if not metadata.is_file():
            continue
        payload, error = _json_object(metadata)
        if error:
            errors.append(error)
        else:
            image = payload.get("image")
            if payload.get("version") != version:
                errors.append(f"{metadata.name}: version mismatch")
            if not isinstance(image, str) or IMAGE_REFERENCE_RE.fullmatch(image) is None:
                errors.append(f"{metadata.name}: image must be digest-pinned")

    sbom = asset_dir / "sbom.json"
    if sbom.is_file():
        payload, error = _json_object(sbom)
        if error:
            errors.append(error)
        elif (
            payload.get("bomFormat") != "CycloneDX"
            or not isinstance(payload.get("specVersion"), str)
            or not isinstance(payload.get("components"), list)
        ):
            errors.append("sbom.json is not a CycloneDX component inventory")

    install = asset_dir / "install.sh"
    if install.is_file():
        mode = install.stat().st_mode
        if mode & stat.S_IXUSR == 0:
            errors.append("install.sh must be executable")
        text = install.read_text(encoding="utf-8")
        if not text.startswith("#!/usr/bin/env bash"):
            errors.append("install.sh must use the repository bash entrypoint")
        if "set -euo pipefail" not in text:
            errors.append("install.sh must enable set -euo pipefail")

    manifest = asset_dir / f"gludd-release-manifest-{version}.json"
    if manifest.is_file():
        payload, error = _json_object(manifest)
        if error:
            errors.append(error)
        elif (
            payload.get("schema_version") != 1
            or payload.get("version") != version
            or not isinstance(payload.get("source_sha"), str)
            or SOURCE_SHA_RE.fullmatch(str(payload.get("source_sha"))) is None
        ):
            errors.append("release manifest schema/version/source SHA is invalid")
        else:
            expected_assets = sorted(
                path.name
                for path in asset_dir.iterdir()
                if path.is_file()
                and path.name not in {manifest.name, "SHA256SUMS"}
            )
            if payload.get("assets") != expected_assets:
                errors.append("release manifest asset inventory is stale or incomplete")

    errors.extend(_verify_checksums(asset_dir))
    return sorted(set(errors))


def write_release_manifest(
    asset_dir: Path, version: str, source_sha: str
) -> Path:
    """Write deterministic release provenance before aggregate checksum generation."""
    if SOURCE_SHA_RE.fullmatch(source_sha) is None:
        raise ValueError("source SHA must be 40 lowercase hexadecimal characters")
    path = asset_dir / f"gludd-release-manifest-{version}.json"
    assets = sorted(
        item.name
        for item in asset_dir.iterdir()
        if item.is_file() and item.name not in {path.name, "SHA256SUMS"}
    )
    payload = {
        "schema_version": 1,
        "version": version,
        "source_sha": source_sha,
        "assets": assets,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """Validate staged assets or write their deterministic provenance manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("asset_dir", type=Path)
    verify.add_argument("version")
    verify.add_argument("--repository-root", type=Path, default=Path.cwd())
    manifest = subparsers.add_parser("write-manifest")
    manifest.add_argument("asset_dir", type=Path)
    manifest.add_argument("version")
    manifest.add_argument("--source-sha", required=True)
    args = parser.parse_args(argv)

    if args.command == "write-manifest":
        written = write_release_manifest(args.asset_dir, args.version, args.source_sha)
        print(f"RELEASE_MANIFEST_WRITTEN path={written}", flush=True)
        return 0

    errors = verify_release_asset_matrix(
        args.asset_dir.resolve(), args.version, args.repository_root.resolve()
    )
    for error in errors:
        print(f"FAIL {error}", file=sys.stderr)
    if errors:
        print(f"RELEASE_ASSET_MATRIX_FAIL errors={len(errors)}", flush=True)
        return 1
    print(
        f"RELEASE_ASSET_MATRIX_PASS smoke={len(REQUIRED_SMOKE_CHECKS)} "
        f"assets={len(list(args.asset_dir.iterdir()))}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
