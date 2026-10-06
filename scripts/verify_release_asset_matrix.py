#!/usr/bin/env python3
"""Fail-closed validation for the staged beta4 release artifact matrix."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import stat
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

import yaml

from general_ludd.git_release.release_state import (
    ReleaseState,
    ReleaseStateMachine,
    TransitionError,
)

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

ROLLBACK_RECEIPT_SCHEMA_VERSION = 1

IMAGE_REFERENCE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$"
)
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_DISTRIBUTION_NAME = "general-ludd-agent"
MAX_INSTALL_SCRIPT_BYTES = 1_048_576
INSTALL_FAIL_FAST_RE = re.compile(
    r"(?m)^[ \t]*set[ \t]+-euo[ \t]+pipefail(?:[ \t]*(?:#.*)?)?$"
)


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
        "rollback receipt": f"gludd-rollback-receipt-{version}.json",
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
            metadata_names = sorted(
                name for name in names if name.endswith(".dist-info/METADATA")
            )
            if len(metadata_names) != 1:
                errors.append("wheel does not contain distribution METADATA")
            else:
                metadata_error = _distribution_metadata_error(
                    archive.read(metadata_names[0]),
                    label="wheel METADATA",
                    expected_version=normalized,
                )
                if metadata_error:
                    errors.append(metadata_error)
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
            metadata_members = [
                member
                for member in archive.getmembers()
                if member.isfile() and member.name.endswith("/PKG-INFO")
            ]
            if len(metadata_members) != 1:
                errors.append("sdist does not contain PKG-INFO")
            else:
                extracted = archive.extractfile(metadata_members[0])
                if extracted is None:
                    errors.append("sdist PKG-INFO is unreadable")
                else:
                    metadata_error = _distribution_metadata_error(
                        extracted.read(),
                        label="sdist PKG-INFO",
                        expected_version=normalized,
                    )
                    if metadata_error:
                        errors.append(metadata_error)
        if not any(name.endswith("/src/general_ludd/__init__.py") for name in names):
            errors.append("sdist does not contain src/general_ludd/__init__.py")
        if not any(name.endswith("/src/general_ludd/cli.py") for name in names):
            errors.append("sdist does not contain src/general_ludd/cli.py")
        if not any(name.endswith("/pyproject.toml") for name in names):
            errors.append("sdist does not contain pyproject.toml")
    except (OSError, tarfile.TarError) as exc:
        errors.append(f"sdist is invalid: {exc}")
    return errors


def _distribution_metadata_error(
    raw: bytes,
    *,
    label: str,
    expected_version: str,
) -> str | None:
    """Return a fail-closed error when package payload identity is stale."""
    try:
        metadata = Parser().parsestr(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        return f"{label} is not UTF-8: {exc}"
    actual_name = metadata.get("Name")
    actual_version = metadata.get("Version")
    if (
        actual_name != EXPECTED_DISTRIBUTION_NAME
        or actual_version != expected_version
    ):
        return (
            f"{label} identity is stale: "
            f"expected {EXPECTED_DISTRIBUTION_NAME}=={expected_version}, "
            f"got {actual_name!r}=={actual_version!r}"
        )
    return None


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


def _canonical_json_sha256(payload: object) -> str:
    """Hash one deterministic JSON representation used by release receipts."""
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _smoke_attestation_digests(asset_dir: Path, version: str) -> dict[str, str]:
    """Bind every version-scoped smoke attestation by its staged bytes."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(asset_dir.glob(f"gludd-smoke-*-{version}.json"))
    }


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _exercise_rollback_state_machine(
    *, source_sha: str, candidate_digest: str, prior_digest: str
) -> None:
    """Exercise the canonical ZDD state graph through candidate rollback."""
    machine = ReleaseStateMachine(
        source_sha=source_sha,
        artifact_digest=candidate_digest,
    )
    targets = (
        ReleaseState.PLAN,
        ReleaseState.BUILD_ONCE,
        ReleaseState.VERIFY_OFFLINE,
        ReleaseState.STAGE,
        ReleaseState.CANARY,
    )
    results = (
        machine.advance(target=ReleaseState.PLAN),
        machine.advance(target=ReleaseState.BUILD_ONCE),
        machine.advance(
            target=ReleaseState.VERIFY_OFFLINE,
            gate_evidence=[("release-matrix", "passed", "local://assets")],
        ),
        machine.advance(
            target=ReleaseState.STAGE,
            artifact_digest=candidate_digest,
        ),
        machine.advance(
            target=ReleaseState.CANARY,
            health_gate_passed=True,
            prior_digest=prior_digest,
        ),
    )
    for target, result in zip(targets, results, strict=True):
        if result.blocked:
            raise ValueError(
                "rollback rehearsal blocked at "
                f"{target.value}: {','.join(result.reasons)}"
            )
    machine.rollback(reason="release rollback receipt rehearsal")
    if machine.serving_digest != prior_digest:
        raise ValueError("rollback rehearsal did not restore the prior digest")


def write_rollback_receipt(
    asset_dir: Path,
    version: str,
    *,
    source_sha: str,
    prior_version: str,
    candidate_asset: str,
    candidate_observed_version: str,
    restored_observed_version: str,
    candidate_health: str,
    restored_health: str,
    prior_route_before_sha256: str,
    prior_route_after_sha256: str,
    active_work_before_sha256: str,
    active_work_after_sha256: str,
) -> Path:
    """Write a deterministic receipt only for a complete hermetic rollback proof."""
    if SOURCE_SHA_RE.fullmatch(source_sha) is None:
        raise ValueError("source SHA must be 40 lowercase hexadecimal characters")
    if not prior_version or prior_version == version:
        raise ValueError("prior version must be non-empty and distinct from candidate")
    if candidate_observed_version != version:
        raise ValueError("candidate observed version does not match release version")
    if restored_observed_version != prior_version:
        raise ValueError("restored observed version does not match prior version")
    if candidate_health != "passed" or restored_health != "passed":
        raise ValueError("candidate and restored health must both be passed")
    if Path(candidate_asset).name != candidate_asset:
        raise ValueError("candidate asset must be a safe basename")
    candidate_path = asset_dir / candidate_asset
    if not candidate_path.is_file() or candidate_path.stat().st_size == 0:
        raise ValueError("candidate asset must be a non-empty staged file")
    candidate_digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()

    route_digests = (prior_route_before_sha256, prior_route_after_sha256)
    if not all(SHA_RE.fullmatch(value) is not None for value in route_digests):
        raise ValueError("prior route evidence must use lowercase SHA-256 digests")
    if not hmac.compare_digest(*route_digests):
        raise ValueError("prior route changed; restoration is not immutable")
    if hmac.compare_digest(candidate_digest, prior_route_before_sha256):
        raise ValueError("candidate and prior route digests must be distinct")

    work_digests = (active_work_before_sha256, active_work_after_sha256)
    if not all(SHA_RE.fullmatch(value) is not None for value in work_digests):
        raise ValueError("active work evidence must use lowercase SHA-256 digests")
    if not hmac.compare_digest(*work_digests):
        raise ValueError("active work changed during rollback rehearsal")

    smoke_errors = _verify_smoke_attestations(asset_dir, version)
    if smoke_errors:
        raise ValueError("platform smoke fan-in incomplete: " + "; ".join(smoke_errors))
    attestations = _smoke_attestation_digests(asset_dir, version)
    if not attestations:
        raise ValueError("platform smoke fan-in has no attestations")

    _exercise_rollback_state_machine(
        source_sha=source_sha,
        candidate_digest=candidate_digest,
        prior_digest=prior_route_before_sha256,
    )
    payload: dict[str, object] = {
        "schema_version": ROLLBACK_RECEIPT_SCHEMA_VERSION,
        "version": version,
        "source_sha": source_sha,
        "candidate": {
            "activation": "passed",
            "asset": candidate_asset,
            "health": candidate_health,
            "observed_version": candidate_observed_version,
            "sha256": candidate_digest,
        },
        "rollback": {
            "health": restored_health,
            "immutable": True,
            "observed_version": restored_observed_version,
            "prior_route_sha256": prior_route_before_sha256,
            "prior_version": prior_version,
            "restoration": "passed",
            "restored_route_sha256": prior_route_after_sha256,
        },
        "active_work": {
            "after_sha256": active_work_after_sha256,
            "before_sha256": active_work_before_sha256,
            "unchanged": True,
        },
        "platform_fan_in": {
            "attestations": attestations,
            "categories": sorted(REQUIRED_SMOKE_CHECKS),
            "status": "passed",
        },
    }
    payload["evidence_sha256"] = _canonical_json_sha256(payload)
    path = asset_dir / f"gludd-rollback-receipt-{version}.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


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


def _verify_install_script(path: Path) -> list[str]:
    """Validate the staged installer without executing or over-reading it."""
    errors: list[str] = []
    if path.stat().st_mode & stat.S_IXUSR == 0:
        errors.append("install.sh must be executable")
    if path.stat().st_size > MAX_INSTALL_SCRIPT_BYTES:
        errors.append(
            f"install.sh exceeds the {MAX_INSTALL_SCRIPT_BYTES}-byte verification limit"
        )
        return errors
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"install.sh is not readable UTF-8: {exc}")
        return errors
    if not text.startswith("#!/usr/bin/env bash\n"):
        errors.append("install.sh must use the repository bash entrypoint")
    if INSTALL_FAIL_FAST_RE.search(text) is None:
        errors.append("install.sh must enable set -euo pipefail")
    return errors


def _verify_candidate_receipt_evidence(
    value: object,
    *,
    asset_dir: Path,
    version: str,
) -> tuple[list[str], str | None]:
    errors: list[str] = []
    candidate_digest: str | None = None
    if not isinstance(value, dict):
        errors.append("rollback receipt candidate evidence must be an object")
        return errors, None
    expected_fields = {
        "activation",
        "asset",
        "health",
        "observed_version",
        "sha256",
    }
    if set(value) != expected_fields:
        errors.append("rollback receipt candidate evidence fields are invalid")
    if value.get("activation") != "passed":
        errors.append("rollback receipt candidate activation is not passed")
    if value.get("health") != "passed":
        errors.append("rollback receipt candidate health is not passed")
    if value.get("observed_version") != version:
        errors.append("rollback receipt candidate version mismatch")
    candidate_asset = value.get("asset")
    candidate_sha = value.get("sha256")
    if (
        not isinstance(candidate_asset, str)
        or Path(candidate_asset).name != candidate_asset
    ):
        errors.append("rollback receipt candidate asset name is unsafe")
    elif not (asset_dir / candidate_asset).is_file():
        errors.append("rollback receipt candidate asset is missing")
    else:
        candidate_digest = hashlib.sha256(
            (asset_dir / candidate_asset).read_bytes()
        ).hexdigest()
        if not isinstance(candidate_sha, str) or not hmac.compare_digest(
            candidate_sha, candidate_digest
        ):
            errors.append("rollback receipt candidate checksum mismatch")
    return errors, candidate_digest


def _verify_restoration_receipt_evidence(
    value: object,
    *,
    version: str,
) -> tuple[list[str], str | None]:
    errors: list[str] = []
    prior_digest: str | None = None
    if not isinstance(value, dict):
        errors.append("rollback receipt prior restoration evidence must be an object")
        return errors, None
    expected_fields = {
        "health",
        "immutable",
        "observed_version",
        "prior_route_sha256",
        "prior_version",
        "restoration",
        "restored_route_sha256",
    }
    if set(value) != expected_fields:
        errors.append("rollback receipt prior restoration fields are invalid")
    prior_version = value.get("prior_version")
    if not isinstance(prior_version, str) or not prior_version:
        errors.append("rollback receipt prior version is invalid")
    if prior_version == version:
        errors.append("rollback receipt prior version is not distinct")
    if value.get("restoration") != "passed":
        errors.append("rollback receipt prior restoration is not passed")
    if value.get("health") != "passed":
        errors.append("rollback receipt restored health is not passed")
    if value.get("observed_version") != prior_version:
        errors.append("rollback receipt restored version mismatch")
    before = value.get("prior_route_sha256")
    after = value.get("restored_route_sha256")
    if not _valid_sha256(before) or not _valid_sha256(after):
        errors.append("rollback receipt prior route digests are invalid")
    elif not hmac.compare_digest(str(before), str(after)):
        errors.append("rollback receipt prior restoration is not immutable")
    else:
        prior_digest = str(before)
    if value.get("immutable") is not True:
        errors.append("rollback receipt prior restoration is not immutable")
    return errors, prior_digest


def _verify_active_work_receipt_evidence(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["rollback receipt active work evidence must be an object"]
    errors: list[str] = []
    if set(value) != {"after_sha256", "before_sha256", "unchanged"}:
        errors.append("rollback receipt active work evidence fields are invalid")
    before = value.get("before_sha256")
    after = value.get("after_sha256")
    if (
        not _valid_sha256(before)
        or not _valid_sha256(after)
        or not hmac.compare_digest(str(before), str(after))
        or value.get("unchanged") is not True
    ):
        errors.append("rollback receipt active work changed")
    return errors


def _verify_platform_fan_in_receipt_evidence(
    value: object,
    *,
    asset_dir: Path,
    version: str,
) -> list[str]:
    if not isinstance(value, dict):
        return ["rollback receipt platform fan-in must be an object"]
    errors: list[str] = []
    if set(value) != {"attestations", "categories", "status"}:
        errors.append("rollback receipt platform fan-in fields are invalid")
    if value.get("status") != "passed":
        errors.append("rollback receipt platform fan-in is not passed")
    if value.get("categories") != sorted(REQUIRED_SMOKE_CHECKS):
        errors.append("rollback receipt platform category fan-in is incomplete")
    if value.get("attestations") != _smoke_attestation_digests(asset_dir, version):
        errors.append("rollback receipt smoke attestation digest fan-in is stale")
    return errors


def _verify_rollback_receipt(
    path: Path,
    *,
    asset_dir: Path,
    version: str,
) -> list[str]:
    """Verify one immutable rollback receipt against the staged matrix."""
    payload, json_error = _json_object(path)
    if json_error:
        return [json_error]
    errors: list[str] = []
    expected_root = {
        "active_work",
        "candidate",
        "evidence_sha256",
        "platform_fan_in",
        "rollback",
        "schema_version",
        "source_sha",
        "version",
    }
    if set(payload) != expected_root:
        errors.append("rollback receipt schema fields are incomplete or unknown")
    if payload.get("schema_version") != ROLLBACK_RECEIPT_SCHEMA_VERSION:
        errors.append("rollback receipt schema version is invalid")
    if payload.get("version") != version:
        errors.append("rollback receipt release version mismatch")
    source_sha = payload.get("source_sha")
    if not isinstance(source_sha, str) or SOURCE_SHA_RE.fullmatch(source_sha) is None:
        errors.append("rollback receipt source SHA is invalid")
    release_manifest = asset_dir / f"gludd-release-manifest-{version}.json"
    if release_manifest.is_file():
        manifest_payload, manifest_error = _json_object(release_manifest)
        if manifest_error is None and manifest_payload.get("source_sha") != source_sha:
            errors.append("rollback receipt source SHA does not match release manifest")

    candidate_errors, candidate_digest = _verify_candidate_receipt_evidence(
        payload.get("candidate"),
        asset_dir=asset_dir,
        version=version,
    )
    errors.extend(candidate_errors)
    restoration_errors, prior_digest = _verify_restoration_receipt_evidence(
        payload.get("rollback"),
        version=version,
    )
    errors.extend(restoration_errors)
    errors.extend(_verify_active_work_receipt_evidence(payload.get("active_work")))
    errors.extend(
        _verify_platform_fan_in_receipt_evidence(
            payload.get("platform_fan_in"),
            asset_dir=asset_dir,
            version=version,
        )
    )

    expected_evidence = dict(payload)
    observed_evidence_digest = expected_evidence.pop("evidence_sha256", None)
    calculated_evidence_digest = _canonical_json_sha256(expected_evidence)
    if (
        not isinstance(observed_evidence_digest, str)
        or not hmac.compare_digest(
            observed_evidence_digest, calculated_evidence_digest
        )
    ):
        errors.append("rollback receipt evidence digest mismatch")

    if (
        isinstance(source_sha, str)
        and SOURCE_SHA_RE.fullmatch(source_sha) is not None
        and candidate_digest is not None
        and prior_digest is not None
    ):
        try:
            _exercise_rollback_state_machine(
                source_sha=source_sha,
                candidate_digest=candidate_digest,
                prior_digest=prior_digest,
            )
        except (TransitionError, ValueError) as exc:
            errors.append(f"rollback receipt ZDD replay failed: {exc}")
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
        errors.extend(_verify_install_script(install))

    rollback_receipt = asset_dir / f"gludd-rollback-receipt-{version}.json"
    if rollback_receipt.is_file():
        errors.extend(
            _verify_rollback_receipt(
                rollback_receipt,
                asset_dir=asset_dir,
                version=version,
            )
        )

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
    rollback = subparsers.add_parser("write-rollback-receipt")
    rollback.add_argument("asset_dir", type=Path)
    rollback.add_argument("version")
    rollback.add_argument("--source-sha", required=True)
    rollback.add_argument("--prior-version", required=True)
    rollback.add_argument("--candidate-asset", required=True)
    rollback.add_argument("--candidate-observed-version", required=True)
    rollback.add_argument("--restored-observed-version", required=True)
    rollback.add_argument("--candidate-health", required=True)
    rollback.add_argument("--restored-health", required=True)
    rollback.add_argument("--prior-route-before-sha256", required=True)
    rollback.add_argument("--prior-route-after-sha256", required=True)
    rollback.add_argument("--active-work-before-sha256", required=True)
    rollback.add_argument("--active-work-after-sha256", required=True)
    args = parser.parse_args(argv)

    if args.command == "write-manifest":
        written = write_release_manifest(args.asset_dir, args.version, args.source_sha)
        print(f"RELEASE_MANIFEST_WRITTEN path={written}", flush=True)
        return 0

    if args.command == "write-rollback-receipt":
        written = write_rollback_receipt(
            args.asset_dir,
            args.version,
            source_sha=args.source_sha,
            prior_version=args.prior_version,
            candidate_asset=args.candidate_asset,
            candidate_observed_version=args.candidate_observed_version,
            restored_observed_version=args.restored_observed_version,
            candidate_health=args.candidate_health,
            restored_health=args.restored_health,
            prior_route_before_sha256=args.prior_route_before_sha256,
            prior_route_after_sha256=args.prior_route_after_sha256,
            active_work_before_sha256=args.active_work_before_sha256,
            active_work_after_sha256=args.active_work_after_sha256,
        )
        print(f"ROLLBACK_RECEIPT_WRITTEN path={written}", flush=True)
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
