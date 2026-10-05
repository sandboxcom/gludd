"""Inspect a bounded FreeLLMAPI source archive without extracting it."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import NoReturn

from general_ludd.models.freellmapi_scoring_kernel import (
    FREELLMAPI_SCORING_BUNDLE_DIGEST,
    FREELLMAPI_SCORING_UPSTREAM_COMMIT,
)

__all__ = [
    "FREELLMAPI_EXPECTED_EXPORTS",
    "FREELLMAPI_REPOSITORY",
    "FREELLMAPI_SCORING_SOURCE",
    "FreeLLMAPIAdmissionError",
    "FreeLLMAPIAdmissionFault",
    "UpstreamSourceEvidence",
    "fail_admission",
    "inspect_upstream_archive",
    "validate_admitted_artifact",
]

FREELLMAPI_REPOSITORY = "tashfeenahmed/freellmapi"
FREELLMAPI_SCORING_SOURCE = "server/src/services/scoring.ts"
FREELLMAPI_EXPECTED_EXPORTS = (
    "reliabilityPosterior",
    "expectedReliability",
    "speedScore",
    "headroomFactor",
    "rateWindowHeadroomFactor",
    "rateLimitFactor",
)

_REQUIRED_SOURCE_PATHS = (
    "LICENSE",
    "package-lock.json",
    FREELLMAPI_SCORING_SOURCE,
)
_SOURCE_LIMITS = {
    "LICENSE": 256 * 1024,
    "package-lock.json": 4 * 1024 * 1024,
    FREELLMAPI_SCORING_SOURCE: 2 * 1024 * 1024,
}
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_BUNDLE_BYTES = 64 * 1024


class FreeLLMAPIAdmissionFault(StrEnum):
    """Content-free failure categories for upstream candidate handling."""

    REPOSITORY_IDENTITY = "repository_identity"
    RELEASE_IDENTITY = "release_identity"
    RELEASE_NOT_STABLE = "release_not_stable"
    TAG_COMMIT_MISMATCH = "tag_commit_mismatch"
    COMMIT_IDENTITY = "commit_identity"
    COMMIT_UNVERIFIED = "commit_unverified"
    ARCHIVE_INVALID = "archive_invalid"
    ARCHIVE_LAYOUT = "archive_layout"
    ARCHIVE_LIMIT = "archive_limit"
    LICENSE_INCOMPATIBLE = "license_incompatible"
    PACKAGE_LOCK_INVALID = "package_lock_invalid"
    ADMITTED_ARTIFACT_INVALID = "admitted_artifact_invalid"
    CANDIDATE_LOCK_INVALID = "candidate_lock_invalid"


class FreeLLMAPIAdmissionError(ValueError):
    """Fail-closed candidate error that never contains upstream content."""

    def __init__(self, fault: FreeLLMAPIAdmissionFault) -> None:
        """Create an error containing only its stable fault category."""
        self.fault = fault
        super().__init__(fault.value)


@dataclass(frozen=True, slots=True)
class UpstreamSourceEvidence:
    """Validated source bytes needed to derive content-free lock evidence."""

    license_bytes: bytes
    package_lock_bytes: bytes
    scoring_bytes: bytes
    symbols_present: list[str]
    symbols_missing: list[str]


def fail_admission(fault: FreeLLMAPIAdmissionFault) -> NoReturn:
    """Raise the stable content-free error for ``fault``."""
    raise FreeLLMAPIAdmissionError(fault)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_member_parts(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name:
        fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT)
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT)
    return path.parts


def _archive_sources(archive_bytes: bytes, *, max_archive_bytes: int) -> dict[str, bytes]:
    if not archive_bytes.startswith(b"\x1f\x8b"):
        fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_INVALID)
    if not archive_bytes or len(archive_bytes) > max_archive_bytes:
        fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > _MAX_ARCHIVE_MEMBERS:
                fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)
            expanded = sum(member.size for member in members if member.isfile())
            if expanded > _MAX_ARCHIVE_EXPANDED_BYTES:
                fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)

            roots: set[str] = set()
            targets: dict[str, list[tarfile.TarInfo]] = {
                path: [] for path in _REQUIRED_SOURCE_PATHS
            }
            for member in members:
                parts = _safe_member_parts(member.name)
                roots.add(parts[0])
                if len(parts) < 2:
                    continue
                relative = "/".join(parts[1:])
                if relative in targets:
                    targets[relative].append(member)
            if len(roots) != 1:
                fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT)

            result: dict[str, bytes] = {}
            for relative, matches in targets.items():
                if len(matches) != 1 or not matches[0].isfile():
                    fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT)
                member = matches[0]
                if member.size < 0 or member.size > _SOURCE_LIMITS[relative]:
                    fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)
                extracted = archive.extractfile(member)
                if extracted is None:
                    fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_INVALID)
                body = extracted.read(_SOURCE_LIMITS[relative] + 1)
                if len(body) != member.size or len(body) > _SOURCE_LIMITS[relative]:
                    fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT)
                result[relative] = body
            return result
    except FreeLLMAPIAdmissionError:
        raise
    except (OSError, tarfile.TarError, EOFError):
        fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_INVALID)


def _validate_license(content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        fail_admission(FreeLLMAPIAdmissionFault.LICENSE_INCOMPATIBLE)
    required = (
        "MIT License",
        "Permission is hereby granted, free of charge",
        'THE SOFTWARE IS PROVIDED "AS IS"',
    )
    if any(fragment not in text for fragment in required):
        fail_admission(FreeLLMAPIAdmissionFault.LICENSE_INCOMPATIBLE)


def _validate_package_lock(content: bytes) -> None:
    try:
        parsed: object = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail_admission(FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID)
    if not isinstance(parsed, dict):
        fail_admission(FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID)
    if parsed.get("name") != "@freellmapi/monorepo" or parsed.get("lockfileVersion") != 3:
        fail_admission(FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID)
    if not isinstance(parsed.get("packages"), dict):
        fail_admission(FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID)


def _source_symbols(content: bytes) -> tuple[list[str], list[str]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        fail_admission(FreeLLMAPIAdmissionFault.ARCHIVE_INVALID)
    present: list[str] = []
    missing: list[str] = []
    for name in FREELLMAPI_EXPECTED_EXPORTS:
        function_pattern = rf"\bexport\s+(?:async\s+)?function\s+{re.escape(name)}\s*\("
        value_pattern = rf"\bexport\s+(?:const|let|var)\s+{re.escape(name)}\b"
        target = present if re.search(function_pattern, text) or re.search(value_pattern, text) else missing
        target.append(name)
    return present, missing


def inspect_upstream_archive(
    archive_bytes: bytes,
    *,
    max_archive_bytes: int,
) -> UpstreamSourceEvidence:
    """Return validated required source evidence from one bounded tar archive."""
    sources = _archive_sources(archive_bytes, max_archive_bytes=max_archive_bytes)
    license_bytes = sources["LICENSE"]
    package_lock_bytes = sources["package-lock.json"]
    scoring_bytes = sources[FREELLMAPI_SCORING_SOURCE]
    _validate_license(license_bytes)
    _validate_package_lock(package_lock_bytes)
    symbols_present, symbols_missing = _source_symbols(scoring_bytes)
    return UpstreamSourceEvidence(
        license_bytes=license_bytes,
        package_lock_bytes=package_lock_bytes,
        scoring_bytes=scoring_bytes,
        symbols_present=symbols_present,
        symbols_missing=symbols_missing,
    )


def validate_admitted_artifact(manifest_bytes: bytes, bundle_bytes: bytes) -> dict[str, object]:
    """Bind a candidate to the exact, still-admitted scoring artifact."""
    fault = FreeLLMAPIAdmissionFault.ADMITTED_ARTIFACT_INVALID
    if not manifest_bytes or len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        fail_admission(fault)
    if not bundle_bytes or len(bundle_bytes) > _MAX_BUNDLE_BYTES:
        fail_admission(fault)
    try:
        parsed: object = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail_admission(fault)
    if not isinstance(parsed, dict):
        fail_admission(fault)
    if parsed.get("exports") != list(FREELLMAPI_EXPECTED_EXPORTS):
        fail_admission(fault)
    bundle_digest = _digest(bundle_bytes)
    if parsed.get("bundle_sha256") != bundle_digest or bundle_digest != FREELLMAPI_SCORING_BUNDLE_DIGEST:
        fail_admission(fault)
    if parsed.get("upstream_commit") != FREELLMAPI_SCORING_UPSTREAM_COMMIT:
        fail_admission(fault)
    if (
        parsed.get("license") != "MIT"
        or parsed.get("source") != FREELLMAPI_SCORING_SOURCE
        or parsed.get("upstream") != f"https://github.com/{FREELLMAPI_REPOSITORY}"
    ):
        fail_admission(fault)
    return {
        "abi_version": 1,
        "upstream_commit": FREELLMAPI_SCORING_UPSTREAM_COMMIT,
        "bundle_sha256": bundle_digest,
        "manifest_sha256": _digest(manifest_bytes),
        "exports": list(FREELLMAPI_EXPECTED_EXPORTS),
    }
